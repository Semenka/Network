"""Weekly retrospective: what the agent did + where it's being inefficient.

Sibling of :mod:`network_chief.dashboard` — the dashboard is a *state*
snapshot, this is an *activity* snapshot. Reads `source_runs`,
`drafts`, and `kpi_snapshots`, runs a small registry of rules, and
emits a ranked markdown report with concrete remediation commands.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from .dashboard import _delta, _percent, _scalar, previous_snapshot
from .db import new_id, now_iso, rows_to_dicts


SEV_CRITICAL = "critical"
SEV_ATTENTION = "attention"
SEV_INFO = "info"
SEV_OK = "ok"

_SEV_ORDER = {SEV_CRITICAL: 0, SEV_ATTENTION: 1, SEV_INFO: 2, SEV_OK: 3}
_SEV_GLYPH = {SEV_CRITICAL: "🟥", SEV_ATTENTION: "🟧", SEV_INFO: "🟨", SEV_OK: "🟩"}


def _isofmt(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _floor(window_days: int) -> str:
    return _isofmt(datetime.now(UTC) - timedelta(days=window_days))


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _hours_since(value: str | None) -> float | None:
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    return round((datetime.now(UTC) - parsed).total_seconds() / 3600, 1)


# Sources we expect to run regularly; "missing" means no row in the window.
EXPECTED_SOURCES_WEEKLY: tuple[str, ...] = (
    "google_people",
    "gmail_api",
    "x_api_following",
    "x_api_mentions",
    "linkedin_oidc",
    "value_maintenance",
    "telegram_discovery",
    "next_actions",
    "gbrain_writeback",
)


def compute_review(con: sqlite3.Connection, *, window_days: int = 7) -> dict[str, Any]:
    """Compute the full review payload for ``window_days`` (default 7)."""
    floor = _floor(window_days)

    activity_rows = rows_to_dicts(
        con.execute(
            """
            SELECT source,
                   count(*) AS runs,
                   sum(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_runs,
                   sum(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) AS error_runs,
                   max(finished_at) AS last_at,
                   group_concat(stats_json, '') AS blobs
              FROM source_runs
             WHERE finished_at >= ?
             GROUP BY source
             ORDER BY last_at DESC
            """,
            (floor,),
        ).fetchall()
    )
    sources_seen = {row["source"] for row in activity_rows}

    last_run_per_source = {
        row["source"]: row["last_at"]
        for row in con.execute(
            "SELECT source, max(finished_at) AS last_at FROM source_runs GROUP BY source"
        ).fetchall()
    }
    missing_sources = [
        {"source": src, "last_at": last_run_per_source.get(src), "hours_since_last": _hours_since(last_run_per_source.get(src))}
        for src in EXPECTED_SOURCES_WEEKLY
        if src not in sources_seen
    ]

    drafts_created = _scalar(
        con, "SELECT count(*) FROM drafts WHERE created_at >= ?", (floor,)
    )
    drafts_approved = _scalar(
        con,
        "SELECT count(*) FROM drafts WHERE status = 'approved' AND updated_at >= ?",
        (floor,),
    )
    drafts_rejected = _scalar(
        con,
        "SELECT count(*) FROM drafts WHERE status = 'rejected' AND updated_at >= ?",
        (floor,),
    )
    decided = drafts_approved + drafts_rejected
    approval_rate = _percent(drafts_approved, decided)

    # Mean idle time across drafts that are STILL in 'draft' status
    idle_row = con.execute(
        """
        SELECT avg((julianday('now') - julianday(created_at)) * 24) AS mean_h,
               count(*) AS pending,
               max((julianday('now') - julianday(created_at)) * 24) AS max_h
          FROM drafts
         WHERE status = 'draft'
        """
    ).fetchone()
    mean_idle_h = round(float(idle_row[0]), 2) if idle_row[0] is not None else None
    pending_drafts = int(idle_row[1] or 0)
    max_idle_h = round(float(idle_row[2]), 2) if idle_row[2] is not None else None

    channel_mix = rows_to_dicts(
        con.execute(
            """
            SELECT channel, count(*) AS n
              FROM drafts
             WHERE created_at >= ?
             GROUP BY channel
             ORDER BY n DESC
            """,
            (floor,),
        ).fetchall()
    )
    distinct_subjects = _scalar(
        con,
        "SELECT count(DISTINCT subject) FROM drafts WHERE created_at >= ?",
        (floor,),
    )
    subject_diversity = round(distinct_subjects / drafts_created, 3) if drafts_created else None

    kpi_rows = rows_to_dicts(
        con.execute(
            """
            SELECT id, captured_at, metrics_json
              FROM kpi_snapshots
             WHERE window_days = 30 AND captured_at >= ?
             ORDER BY captured_at
            """,
            (floor,),
        ).fetchall()
    )
    kpi_deltas: dict[str, Any] = {}
    if kpi_rows:
        first = json.loads(kpi_rows[0]["metrics_json"])
        last = json.loads(kpi_rows[-1]["metrics_json"])
        kpi_deltas = {
            "first_at": kpi_rows[0]["captured_at"],
            "last_at": kpi_rows[-1]["captured_at"],
            "n_snapshots": len(kpi_rows),
            "deltas": {
                "total_people": (last["breadth"]["total_people"], first["breadth"]["total_people"]),
                "pct_active_30d": (last["cadence"]["pct_active_30d"], first["cadence"]["pct_active_30d"]),
                "stale_high_value": (last["cadence"]["stale_high_value"], first["cadence"]["stale_high_value"]),
                "approval_rate_pct": (last["pipeline"]["approval_rate_pct"], first["pipeline"]["approval_rate_pct"]),
            },
        }

    # State counters used by rules
    active_goals = _scalar(con, "SELECT count(*) FROM goals WHERE status = 'active'")
    total_people = _scalar(con, "SELECT count(*) FROM people")
    telegram_handles = _scalar(
        con, "SELECT count(*) FROM people WHERE telegram_handle IS NOT NULL AND telegram_handle != ''"
    )
    telegram_pct = _percent(telegram_handles, total_people)
    telegram_discovery_runs = _scalar(
        con, "SELECT count(*) FROM source_runs WHERE source = 'telegram_discovery'"
    )
    last_brief_at = (
        con.execute(
            "SELECT max(created_at) FROM drafts WHERE rationale LIKE '%maintenance%' OR rationale LIKE '%Goal-linked%'"
        ).fetchone()[0]
    )
    x_token_present = _scalar(con, "SELECT count(*) FROM oauth_tokens WHERE provider = 'x'") > 0
    gbrain_context_people = _scalar(
        con,
        """
        SELECT count(DISTINCT person_id)
          FROM source_facts
         WHERE fact_type = 'gbrain_context'
           AND source = 'gbrain'
           AND person_id IS NOT NULL
        """,
    )
    linkedin_posts_without_outcomes = _scalar(
        con,
        """
        SELECT count(*)
          FROM drafts d
         WHERE d.channel = 'linkedin_post'
           AND d.status IN ('published', 'sent')
           AND d.updated_at >= ?
           AND NOT EXISTS (
               SELECT 1 FROM draft_events e
                WHERE e.draft_id = d.id
                  AND e.event_type IN ('response', 'responded', 'converted', 'outcome', 'no_response')
           )
        """,
        (floor,),
    )

    snapshot_count_in_window = _scalar(
        con, "SELECT count(*) FROM kpi_snapshots WHERE window_days = 30 AND captured_at >= ?", (floor,)
    )
    first_kpi_ever = (
        con.execute("SELECT min(captured_at) FROM kpi_snapshots WHERE window_days = 30").fetchone()[0]
    )
    days_since_first_kpi = (
        round((_hours_since(first_kpi_ever) or 0) / 24, 1) if first_kpi_ever else None
    )

    review = {
        "captured_at": now_iso(),
        "window_days": window_days,
        "activity": {
            "rows": activity_rows,
            "missing_sources": missing_sources,
            "total_runs": sum(int(r["runs"]) for r in activity_rows),
        },
        "pipeline": {
            "drafts_created": drafts_created,
            "drafts_approved": drafts_approved,
            "drafts_rejected": drafts_rejected,
            "approval_rate_pct": approval_rate,
            "channel_mix": channel_mix,
            "subject_diversity": subject_diversity,
            "distinct_subjects": distinct_subjects,
            "pending_drafts": pending_drafts,
            "mean_idle_h": mean_idle_h,
            "max_idle_h": max_idle_h,
        },
        "kpi": kpi_deltas,
        "state": {
            "active_goals": active_goals,
            "total_people": total_people,
            "telegram_coverage_pct": telegram_pct,
            "telegram_discovery_runs": telegram_discovery_runs,
            "last_brief_at": last_brief_at,
            "hours_since_last_brief": _hours_since(last_brief_at),
            "x_token_present": x_token_present,
            "last_run_per_source": last_run_per_source,
            "snapshot_count_in_window": snapshot_count_in_window,
            "days_since_first_kpi": days_since_first_kpi,
            "gbrain_context_people": gbrain_context_people,
            "linkedin_posts_without_outcomes": linkedin_posts_without_outcomes,
        },
    }
    review["findings"] = _run_rules(review)
    return review


# ---------------------------------------------------------------------------
# Rules registry — pure functions over the review dict.
# Each returns either None or a dict with: severity, headline, evidence, command.
# ---------------------------------------------------------------------------

Finding = dict[str, Any]


def _r_idle_drafts(r: dict[str, Any]) -> Finding | None:
    p = r["pipeline"]
    if p["drafts_created"] == 0:
        return None
    if p["pending_drafts"] == 0:
        return None
    idle = p["mean_idle_h"]
    if idle is None or idle <= 24:
        return None
    decided = p["drafts_approved"] + p["drafts_rejected"]
    if decided >= p["pending_drafts"]:
        return None
    return {
        "severity": SEV_CRITICAL,
        "headline": f"{p['pending_drafts']} drafts queued; no decisions in {idle:.0f}h. Approval-rate KPI is starving.",
        "evidence": f"pending={p['pending_drafts']}, decided={decided}, mean_idle_h={idle}, max_idle_h={p['max_idle_h']}",
        "command": "network-chief review-queue --limit 12 --out data/review-queue.md   # then approve-draft/reject-draft grouped items",
    }


def _r_no_goals(r: dict[str, Any]) -> Finding | None:
    if r["state"]["active_goals"] > 0:
        return None
    return {
        "severity": SEV_ATTENTION,
        "headline": "No active goals — every draft falls back to the generic template.",
        "evidence": "goals.status='active' count = 0",
        "command": 'network-chief add-goal --title "<title>" --cadence weekly --capital-type <type>',
    }


def _r_telegram_empty(r: dict[str, Any]) -> Finding | None:
    state = r["state"]
    if state["telegram_coverage_pct"] >= 1.0:
        return None
    if state["telegram_discovery_runs"] == 0:
        return None
    return {
        "severity": SEV_ATTENTION,
        "headline": (
            f"Telegram preference set but coverage is {state['telegram_coverage_pct']}%; "
            "passive discovery yields nothing."
        ),
        "evidence": f"telegram_discovery_runs={state['telegram_discovery_runs']}, coverage={state['telegram_coverage_pct']}%",
        "command": "network-chief import-telegram --file exports/telegram_map.csv --lookup email",
    }


def _r_sync_google_stale(r: dict[str, Any]) -> Finding | None:
    last = r["state"]["last_run_per_source"].get("google_people") or r["state"][
        "last_run_per_source"
    ].get("gmail_api")
    h = _hours_since(last)
    if h is None or h <= 48:
        return None
    return {
        "severity": SEV_CRITICAL,
        "headline": f"Google sync stale — last run {h:.0f}h ago.",
        "evidence": f"last sync at {last}",
        "command": "network-chief sync-google --limit 1000",
    }


def _r_sync_x_stale(r: dict[str, Any]) -> Finding | None:
    if not r["state"]["x_token_present"]:
        return None
    last = r["state"]["last_run_per_source"].get("x_api_following")
    h = _hours_since(last)
    if h is not None and h <= 168:  # 7 days
        return None
    return {
        "severity": SEV_ATTENTION,
        "headline": "X sync hasn't run in the last week.",
        "evidence": f"last x_api_following at {last or '<never>'}",
        "command": "network-chief sync-x --max-pages 5",
    }


def _r_subject_diversity(r: dict[str, Any]) -> Finding | None:
    p = r["pipeline"]
    if p["drafts_created"] <= 5:
        return None
    if p["subject_diversity"] is None or p["subject_diversity"] >= 0.3:
        return None
    return {
        "severity": SEV_ATTENTION,
        "headline": (
            f"Drafts are templated identically — {p['distinct_subjects']} distinct subject(s) "
            f"across {p['drafts_created']} drafts."
        ),
        "evidence": f"subject_diversity={p['subject_diversity']}",
        "command": "Add at least one goal, then re-run network-chief brief --limit <N>",
    }


def _r_stale_backlog(r: dict[str, Any]) -> Finding | None:
    deltas = r.get("kpi", {}).get("deltas") or {}
    pair = deltas.get("stale_high_value")
    if not pair:
        return None
    current = pair[0]
    if current is None or current <= 100:
        return None
    last_brief_h = r["state"]["hours_since_last_brief"]
    if last_brief_h is not None and last_brief_h <= 72:
        return None
    return {
        "severity": SEV_CRITICAL,
        "headline": f"Stale-but-valuable backlog at {current}; last brief was {last_brief_h or 'never'}h ago.",
        "evidence": f"stale_high_value={current}, hours_since_last_brief={last_brief_h}",
        "command": "network-chief brief --limit 12",
    }


def _r_low_approval_rate(r: dict[str, Any]) -> Finding | None:
    p = r["pipeline"]
    decided = p["drafts_approved"] + p["drafts_rejected"]
    if decided < 5:
        return None
    if p["approval_rate_pct"] >= 50:
        return None
    return {
        "severity": SEV_ATTENTION,
        "headline": (
            f"Approval rate {p['approval_rate_pct']}% across {decided} decisions — "
            "templates may be misaligned with judgment."
        ),
        "evidence": f"approved={p['drafts_approved']}, rejected={p['drafts_rejected']}",
        "command": "network-chief drafts --status rejected   # inspect to inform template tuning",
    }


def _r_gbrain_coverage(r: dict[str, Any]) -> Finding | None:
    state = r["state"]
    if state["total_people"] == 0:
        return None
    if state["gbrain_context_people"] >= min(10, state["total_people"]):
        return None
    if state["last_run_per_source"].get("next_actions") is None:
        return None
    return {
        "severity": SEV_ATTENTION,
        "headline": (
            f"gbrain context covers {state['gbrain_context_people']} people; next-action rationales may miss private memory."
        ),
        "evidence": f"gbrain_context_people={state['gbrain_context_people']}, total_people={state['total_people']}",
        "command": "network-chief next-actions --limit 10 --out data/next-actions.md",
    }


def _r_linkedin_outcome_gap(r: dict[str, Any]) -> Finding | None:
    missing = r["state"]["linkedin_posts_without_outcomes"]
    if missing <= 0:
        return None
    return {
        "severity": SEV_ATTENTION,
        "headline": f"{missing} published LinkedIn post(s) have no recorded outcome.",
        "evidence": "Published posts need 2h/24h metrics or an explicit no_response outcome.",
        "command": "network-chief record-engagement-outcome --draft-id <id> --outcome reply|useful_conversation|meeting|no_response",
    }


def _r_cadence_gap(r: dict[str, Any]) -> Finding | None:
    state = r["state"]
    if state["snapshot_count_in_window"] >= 2:
        return None
    if (state["days_since_first_kpi"] or 0) < 7:
        return None
    return {
        "severity": SEV_INFO,
        "headline": "Only one KPI snapshot in the window — wire weekly cron to enable W-o-W deltas.",
        "evidence": f"snapshot_count_in_window={state['snapshot_count_in_window']}",
        "command": "Set up OpenClaw cron: 'Mon 08:00 network-chief dashboard --window 30 ...'",
    }


RULES: list[Callable[[dict[str, Any]], Finding | None]] = [
    _r_idle_drafts,
    _r_no_goals,
    _r_telegram_empty,
    _r_sync_google_stale,
    _r_sync_x_stale,
    _r_subject_diversity,
    _r_stale_backlog,
    _r_low_approval_rate,
    _r_gbrain_coverage,
    _r_linkedin_outcome_gap,
    _r_cadence_gap,
]


def _run_rules(review: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    for rule in RULES:
        try:
            out = rule(review)
        except Exception as exc:  # pragma: no cover
            out = {
                "severity": SEV_INFO,
                "headline": f"Rule {rule.__name__} errored: {exc}",
                "evidence": "",
                "command": "",
            }
        if out:
            findings.append(out)
    if not findings:
        findings.append(
            {
                "severity": SEV_OK,
                "headline": "No high-leverage waste detected this week.",
                "evidence": "",
                "command": "",
            }
        )
    findings.sort(key=lambda f: _SEV_ORDER.get(f["severity"], 99))
    return findings


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_review(con: sqlite3.Connection, review: dict[str, Any]) -> str:
    rid = new_id()
    con.execute(
        """
        INSERT INTO review_snapshots (id, captured_at, window_days, findings_json)
        VALUES (?, ?, ?, ?)
        """,
        (rid, review["captured_at"], int(review["window_days"]), json.dumps(review, sort_keys=True)),
    )
    con.commit()
    return rid


def previous_review(con: sqlite3.Connection, *, window_days: int) -> dict[str, Any] | None:
    row = con.execute(
        "SELECT findings_json FROM review_snapshots WHERE window_days = ? ORDER BY captured_at DESC LIMIT 1",
        (window_days,),
    ).fetchone()
    return json.loads(row[0]) if row else None


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

def render_review_markdown(review: dict[str, Any], previous: dict[str, Any] | None = None) -> str:
    out: list[str] = []
    window = review["window_days"]
    captured = review["captured_at"]
    out.append(f"# Network Chief Agent Review ({window}-day retrospective)")
    if previous:
        out.append(f"_Captured at {captured} — comparing vs previous review at {previous['captured_at']}._")
    else:
        out.append(f"_Captured at {captured} — first review for this window._")
    out.append("")

    # 1. Summary (top-3 by severity)
    findings = review.get("findings") or []
    out.append("## Summary")
    if not findings:
        out.append("- No findings.")
    else:
        for f in findings[:3]:
            glyph = _SEV_GLYPH.get(f["severity"], "•")
            line = f"- {glyph} **{f['headline']}**"
            if f.get("command"):
                line += f"  →  `{f['command']}`"
            out.append(line)
    out.append("")

    # 2. Activity log
    activity = review["activity"]
    out.append(f"## Activity log (last {window}d)")
    out.append(f"_Total runs: {activity['total_runs']}._")
    if activity["rows"]:
        out.append("")
        out.append("| Source | Runs | OK | Errors | Last at |")
        out.append("|---|---|---|---|---|")
        for row in activity["rows"]:
            out.append(
                f"| `{row['source']}` | {row['runs']} | {row['ok_runs']} | {row['error_runs']} | {row['last_at']} |"
            )
    else:
        out.append("- No `source_runs` rows in window. Did the OpenClaw cron actually run?")
    if activity["missing_sources"]:
        out.append("")
        out.append("**Missing (expected on a weekly cadence):**")
        for m in activity["missing_sources"]:
            last = m["last_at"] or "<never>"
            since = m["hours_since_last"]
            extra = f" (last {since:.0f}h ago)" if since is not None else ""
            out.append(f"- `{m['source']}` — last {last}{extra}")
    out.append("")

    # 3. Pipeline throughput
    p = review["pipeline"]
    out.append(f"## Pipeline throughput (last {window}d)")
    out.append(
        f"- **Drafts**: created {p['drafts_created']} • "
        f"approved {p['drafts_approved']} • rejected {p['drafts_rejected']}"
    )
    out.append(f"- **Approval rate**: {p['approval_rate_pct']}%")
    if p["channel_mix"]:
        chans = ", ".join(f"{r['channel']}: {r['n']}" for r in p["channel_mix"])
        out.append(f"- **By channel**: {chans}")
    if p["subject_diversity"] is not None:
        out.append(
            f"- **Subject diversity**: {p['distinct_subjects']}/{p['drafts_created']} "
            f"= {p['subject_diversity']}"
        )
    if p["mean_idle_h"] is not None:
        out.append(
            f"- **Pending drafts**: {p['pending_drafts']} • "
            f"mean idle {p['mean_idle_h']}h • max idle {p['max_idle_h']}h"
        )
    out.append("")

    # 4. KPI deltas
    kpi = review.get("kpi") or {}
    out.append(f"## KPI deltas (within window)")
    if kpi.get("deltas"):
        out.append(
            f"_From {kpi['first_at']} to {kpi['last_at']} ({kpi['n_snapshots']} snapshots)._"
        )
        for label, (curr, prev) in kpi["deltas"].items():
            out.append(f"- **{label}**: {curr}{_delta(curr, prev)}")
    else:
        out.append("- No `kpi_snapshots` in window. Run `network-chief dashboard --window 30` to seed.")
    out.append("")

    # 5. Findings
    out.append("## Findings & recommendations")
    for f in findings:
        glyph = _SEV_GLYPH.get(f["severity"], "•")
        out.append(f"### {glyph} {f['headline']}")
        if f.get("evidence"):
            out.append(f"_evidence: {f['evidence']}_")
        if f.get("command"):
            out.append(f"")
            out.append(f"```")
            out.append(f"{f['command']}")
            out.append(f"```")
        out.append("")

    return "\n".join(out)
