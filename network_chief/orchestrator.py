"""Autopilot: the self-directing SENSE -> THINK -> ACT -> REPORT loop.

``run_cycle`` composes the existing single-purpose functions into one
autonomous pass. It is designed to run unattended (OpenClaw cron) and to
degrade gracefully: any phase that fails (missing/expired token, rate
limit, network) is logged to ``source_runs`` and the cycle continues.

Self-direction: after THINK produces ``review.findings``, the orchestrator
maps the highest-leverage findings to cheap, reversible actions via
``FINDING_ACTIONS``. Judgement calls (idle drafts, low approval rate,
rejection patterns) are never auto-decided — they surface in the digest.

Outbound sending is delegated entirely to ``policy.permits_send`` inside
``send_drafts_via_gmail``; at the default level 0 nothing is ever sent.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Callable

from .auth.errors import AuthRequired, OAuthError, RateLimited
from .brief import build_daily_brief
from .dashboard import compute_dashboard, previous_snapshot, render_markdown, save_snapshot
from .db import record_source_run
from .engagement import (
    prepare_gmail_keepalive,
    prepare_linkedin_posts,
    prepare_telegram_keepalive,
    prepare_x_comments,
    prepare_x_posts,
)
from .importers.google_api import (
    detect_replies_heuristic,
    push_drafts_to_gmail,
    send_drafts_via_gmail,
    sync_gmail_messages,
    sync_google_contacts,
)
from .importers.x_api import sync_x_following, sync_x_mentions
from .review import compute_review, previous_review, render_review_markdown, save_review
from .scoring import rank_people
from .value import maintain_connection_values

_SKIP_EXCEPTIONS = (AuthRequired, OAuthError, RateLimited)


def _safe(con, phase: str, label: str, fn: Callable[[], Any], result: dict[str, Any]) -> Any:
    """Run one provider/action, recording status; never raise upward."""
    try:
        out = fn()
        result["steps"].append({"phase": phase, "step": label, "status": "ok", "stats": out})
        return out
    except _SKIP_EXCEPTIONS as exc:
        result["steps"].append({"phase": phase, "step": label, "status": "skipped", "reason": str(exc)})
        return None
    except Exception as exc:  # pragma: no cover - defensive; keep the loop alive
        result["steps"].append({"phase": phase, "step": label, "status": "error", "reason": str(exc)})
        return None


# --- self-direction: finding rule -> automatic, reversible action -----------

def _act_stale_backlog(con: sqlite3.Connection) -> dict[str, Any]:
    build_daily_brief(con, limit=12, create_draft_records=True)
    ids = prepare_gmail_keepalive(con, limit=10)
    return {"keepalive_drafts": len(ids)}


FINDING_ACTIONS: dict[str, Callable[[sqlite3.Connection], dict[str, Any]] | None] = {
    "_r_stale_backlog": _act_stale_backlog,
    "_r_sync_google_stale": lambda con: {"google": sync_google_contacts(con, limit=1000)},
    "_r_sync_x_stale": lambda con: {"x": sync_x_following(con, max_pages=5)},
    # Judgement calls — surfaced in the digest, never auto-decided:
    "_r_idle_drafts": None,
    "_r_low_approval_rate": None,
    "_r_rejection_pattern": None,
    "_r_no_goals": None,
    "_r_telegram_empty": None,
    "_r_cadence_gap": None,
}


def run_cycle(con: sqlite3.Connection, *, policy, dashboards_dir: str | None = None) -> dict[str, Any]:
    """Execute one autonomous SENSE -> THINK -> ACT -> REPORT cycle."""
    result: dict[str, Any] = {"steps": [], "findings": [], "auto_actions": [], "send": None}

    # ---- SENSE -----------------------------------------------------------
    _safe(con, "sense", "google_contacts", lambda: sync_google_contacts(con, limit=1000), result)
    _safe(con, "sense", "gmail_messages", lambda: sync_gmail_messages(con, limit=200), result)
    _safe(con, "sense", "gmail_reply_heuristic", lambda: detect_replies_heuristic(con), result)
    _safe(con, "sense", "x_following", lambda: sync_x_following(con, max_pages=3), result)
    _safe(con, "sense", "x_mentions", lambda: sync_x_mentions(con, max_pages=3), result)
    record_source_run(con, source="autopilot.sense", source_ref=None, status="ok",
                      stats={"steps": [s for s in result["steps"] if s["phase"] == "sense"]})

    # ---- THINK -----------------------------------------------------------
    _safe(con, "think", "maintain_values", lambda: maintain_connection_values(con), result)
    _safe(con, "think", "rank_people", lambda: {"ranked": len(rank_people(con, limit=12))}, result)
    review = _safe(con, "think", "compute_review", lambda: compute_review(con, window_days=7), result)
    findings = (review or {}).get("findings", []) if isinstance(review, dict) else []
    result["findings"] = findings
    record_source_run(con, source="autopilot.think", source_ref=None, status="ok",
                      stats={"findings": len(findings)})

    # ---- self-direction: act on high-leverage findings -------------------
    for finding in findings:
        if finding.get("severity") not in ("critical", "attention"):
            continue
        action = FINDING_ACTIONS.get(finding.get("rule"))
        if action is None:
            continue
        out = _safe(con, "self_direct", finding["rule"], lambda a=action: a(con), result)
        result["auto_actions"].append({"rule": finding["rule"], "headline": finding.get("headline"), "result": out})

    # ---- ACT -------------------------------------------------------------
    _safe(con, "act", "build_daily_brief", lambda: {"brief": bool(build_daily_brief(con, limit=12, create_draft_records=True))}, result)
    _safe(con, "act", "prepare_gmail_keepalive", lambda: {"ids": len(prepare_gmail_keepalive(con, limit=10))}, result)
    _safe(con, "act", "prepare_telegram_keepalive", lambda: {"ids": len(prepare_telegram_keepalive(con, limit=10))}, result)
    _safe(con, "act", "prepare_linkedin_posts", lambda: {"ids": len(prepare_linkedin_posts(con, count=2))}, result)
    _safe(con, "act", "prepare_x_posts", lambda: {"ids": len(prepare_x_posts(con, count=2))}, result)
    _safe(con, "act", "prepare_x_comments", lambda: {"ids": len(prepare_x_comments(con, count=3))}, result)
    # Always stage into Gmail Drafts (safe; needs gmail.compose).
    _safe(con, "act", "push_drafts_to_gmail", lambda: push_drafts_to_gmail(con), result)
    # Autonomous send is fully gated by the policy (level 0 => nothing sent).
    send = _safe(con, "act", "send_drafts_via_gmail", lambda: send_drafts_via_gmail(con, policy=policy), result)
    result["send"] = send
    record_source_run(con, source="autopilot.act", source_ref=None,
                      status="ok",
                      stats={"send": send, "level": policy.level, "dry_run": policy.dry_run})

    # ---- REPORT ----------------------------------------------------------
    out_dir = Path(dashboards_dir or os.environ.get("NETWORK_CHIEF_DASHBOARDS_DIR", "dashboards"))

    def _report() -> dict[str, Any]:
        snap = compute_dashboard(con, window_days=30)
        prev = previous_snapshot(con, window_days=30)
        save_snapshot(con, snap)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "dashboard-30d.md").write_text(render_markdown(snap, previous=prev, con=con), encoding="utf-8")
        rev = compute_review(con, window_days=7)
        prev_rev = previous_review(con, window_days=7)
        save_review(con, rev)
        (out_dir / "agent-review-7d.md").write_text(render_review_markdown(rev, previous=prev_rev), encoding="utf-8")
        digest = render_operator_digest(result, policy)
        (out_dir / "autopilot-digest.md").write_text(digest, encoding="utf-8")
        return {"dashboards_dir": str(out_dir)}

    _safe(con, "report", "render", _report, result)
    record_source_run(con, source="autopilot.report", source_ref=None, status="ok", stats={})

    return result


def render_operator_digest(result: dict[str, Any], policy) -> str:
    """One-screen markdown summary of what the cycle did and what needs a human."""
    out: list[str] = ["# Autopilot digest", ""]
    out.append(f"_Autonomy level {policy.level} · dry_run={policy.dry_run} · "
               f"channels={','.join(policy.channels_enabled)} · cap={policy.daily_send_cap}/day_")
    out.append("")

    send = result.get("send") or {}
    if send:
        out.append("## Outbound")
        out.append(f"- sent: **{send.get('sent', 0)}** · dry-run staged: {send.get('dry_run', 0)} · "
                   f"blocked by policy: {send.get('blocked', 0)} · errors: {len(send.get('errors', []))}")
        for b in (send.get("blocked_items") or [])[:5]:
            out.append(f"  - blocked → {b['name']} <{b['to']}>: {b['reason']}")
        out.append("")

    if result.get("auto_actions"):
        out.append("## Auto-actions taken")
        for a in result["auto_actions"]:
            out.append(f"- {a['rule']}: {a.get('headline', '')}")
        out.append("")

    # Findings that need a human decision (no auto-action mapped).
    needs_human = [f for f in result.get("findings", [])
                   if f.get("severity") in ("critical", "attention")
                   and FINDING_ACTIONS.get(f.get("rule")) is None]
    if needs_human:
        out.append("## Needs your decision")
        for f in needs_human:
            cmd = f.get("command")
            line = f"- {f.get('headline')}"
            if cmd:
                line += f"  →  `{cmd}`"
            out.append(line)
        out.append("")

    out.append("## Phase log")
    for s in result.get("steps", []):
        mark = {"ok": "✓", "skipped": "–", "error": "✗"}.get(s["status"], "?")
        line = f"- {mark} {s['phase']}/{s['step']}"
        if s.get("reason"):
            line += f" — {s['reason']}"
        out.append(line)
    out.append("")
    return "\n".join(out)
