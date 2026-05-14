from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from .auth.tokens import TokenStore, is_expired
from .db import has_send_enabled_account, rows_to_dicts
from .gbrain import GBrainAdapter
from .scoring import rank_people


EXPECTED_WEEKLY_SOURCES: tuple[str, ...] = (
    "google_people",
    "gmail_api",
    "x_api_following",
    "x_api_mentions",
    "linkedin_oidc",
    "telegram_discovery",
    "next_actions",
    "gbrain_writeback",
)

OUTCOME_EVENT_TYPES: tuple[str, ...] = (
    "response",
    "responded",
    "converted",
    "outcome",
    "no_response",
)

DELIVERY_EVENT_TYPES: tuple[str, ...] = ("sent", "published")

LINKEDIN_METRICS: tuple[str, ...] = (
    "impressions",
    "reactions",
    "comments",
    "reposts",
    "profile_views",
    "useful_replies",
)


def build_review_queue(con: sqlite3.Connection, *, limit: int = 12) -> dict[str, Any]:
    drafts = _pending_drafts(con)
    groups: dict[str, dict[str, Any]] = {}
    for draft in drafts:
        key = _draft_group_key(draft)
        group = groups.setdefault(
            key,
            {
                "representative": draft,
                "ids": [],
                "count": 0,
                "oldest_at": draft["created_at"],
                "newest_at": draft["created_at"],
            },
        )
        group["ids"].append(draft["id"])
        group["count"] += 1
        if str(draft["created_at"]) < str(group["oldest_at"]):
            group["oldest_at"] = draft["created_at"]
        if str(draft["created_at"]) > str(group["newest_at"]):
            group["newest_at"] = draft["created_at"]
            group["representative"] = draft

    enriched: list[dict[str, Any]] = []
    for group in groups.values():
        representative = group["representative"]
        score = _draft_priority_score(con, representative, duplicate_count=int(group["count"]))
        enriched.append(
            {
                **group,
                "score": score,
                "route": safe_execution_route(con, representative),
            }
        )
    enriched.sort(key=lambda item: (int(item["score"]), str(item["newest_at"])), reverse=True)
    return {
        "pending_total": len(drafts),
        "group_total": len(enriched),
        "limit": limit,
        "items": enriched[:limit],
    }


def render_review_queue_markdown(queue: dict[str, Any]) -> str:
    lines = [
        "# Network Chief Review Queue",
        "",
        f"- Pending drafts: {queue['pending_total']}",
        f"- Grouped review items: {queue['group_total']}",
        f"- Showing: {len(queue['items'])}",
        "",
        "Approve, reject, or edit only after checking exact text. External delivery remains gated by the channel-specific confirmation step.",
        "",
    ]
    if not queue["items"]:
        lines.append("- No pending drafts.")
        return "\n".join(lines)

    for index, item in enumerate(queue["items"], start=1):
        draft = item["representative"]
        name = draft.get("full_name") or "Public audience"
        channel = draft["channel"]
        duplicate_note = f" ({item['count']} grouped)" if int(item["count"]) > 1 else ""
        lines.extend(
            [
                f"## {index}. {name} - {channel}{duplicate_note}",
                "",
                f"- Draft ID: `{draft['id']}`",
                f"- Score: {item['score']}",
                f"- Oldest/newest: {item['oldest_at']} / {item['newest_at']}",
                f"- Subject: {draft.get('subject') or '(none)'}",
            ]
        )
        if draft.get("goal_title"):
            lines.append(f"- Goal: {draft['goal_title']}")
        if draft.get("rationale"):
            lines.append(f"- Rationale: {_truncate(str(draft['rationale']), 220)}")
        older = [draft_id for draft_id in item["ids"] if draft_id != draft["id"]]
        if older:
            lines.append(f"- Older duplicate IDs: {', '.join(f'`{draft_id}`' for draft_id in older[:5])}")
        lines.extend(
            [
                "",
                "```text",
                _truncate(str(draft["body"]).replace("```", "'''"), 900),
                "```",
                "",
                "Decision commands:",
                f"- `network-chief approve-draft --id {draft['id']} --reason-code good_timing`",
                f"- `network-chief reject-draft --id {draft['id']} --reason-code weak_context`",
                f"- Route after approval: {item['route']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def safe_execution_route(con: sqlite3.Connection, draft: dict[str, Any]) -> str:
    channel = str(draft.get("channel") or "").lower()
    draft_id = str(draft.get("id") or "<draft-id>")
    if channel == "gmail":
        if not (draft.get("primary_email") or _has_account(con, draft.get("person_id"), "gmail")):
            return "add a Gmail channel account before delivery."
        google = _token_status(con, "google", ("https://www.googleapis.com/auth/gmail.compose",))
        if google["ok"]:
            return "`network-chief push-drafts --status approved --limit 1` or exact-send with `network-chief send-approved-gmail --draft-id %s --confirm-exact-text-file <file>`." % draft_id
        return "`network-chief send-approved-gmail --draft-id %s --confirm-exact-text-file <file>` after `auth-google`/Gmail token setup." % draft_id
    if channel == "telegram":
        if has_send_enabled_account(con, person_id=str(draft.get("person_id") or ""), channel="telegram"):
            return "`network-chief telegram-links --status approved --limit 10`; click Send in Telegram manually."
        return "store a send-enabled Telegram handle/chat id first; no contact automation without it."
    if channel == "linkedin_post":
        linkedin = _linkedin_posting_status(con)
        if linkedin["ok"]:
            return "`network-chief publish-approved-linkedin --draft-id %s --confirm-exact-text-file <file>`." % draft_id
        return "manual LinkedIn publish, then `network-chief record-draft-event --id %s --event published --external-ref linkedin:<post>`." % draft_id
    if channel == "linkedin":
        return "manual LinkedIn interaction only; no automated DMs/comments. Record sent/response events after action."
    if channel.startswith("x"):
        return "manual X publish/comment unless an official posting path is added; record published + outcome events."
    return "manual review route; record the lifecycle event that matches the action."


def build_source_health(
    con: sqlite3.Connection,
    *,
    top_n: int = 50,
    adapter: GBrainAdapter | None = None,
) -> dict[str, Any]:
    adapter = adapter or GBrainAdapter()
    source_runs = _latest_source_runs(con)
    total_people = _scalar(con, "SELECT count(*) FROM people")
    coverage = {
        "people": total_people,
        "email": _scalar(con, "SELECT count(*) FROM people WHERE primary_email IS NOT NULL AND primary_email != ''"),
        "linkedin": _scalar(con, "SELECT count(*) FROM people WHERE linkedin_url IS NOT NULL AND linkedin_url != ''"),
        "telegram": _scalar(con, "SELECT count(*) FROM people WHERE telegram_handle IS NOT NULL AND telegram_handle != ''"),
        "x": _scalar(con, "SELECT count(*) FROM people WHERE twitter_handle IS NOT NULL AND twitter_handle != ''"),
        "telegram_send_enabled": _scalar(
            con,
            "SELECT count(*) FROM channel_accounts WHERE channel = 'telegram' AND send_enabled = 1",
        ),
        "gmail_send_enabled": _scalar(
            con,
            "SELECT count(*) FROM channel_accounts WHERE channel = 'gmail' AND send_enabled = 1",
        ),
    }
    top_people = rank_people(con, limit=top_n, mode="relationship")
    top_person_ids = [str(person["id"]) for person in top_people]
    top_reachable = sum(1 for person in top_people if _has_reachable_channel(con, person))
    top_send_enabled = sum(1 for person in top_people if _has_send_enabled_path(con, person))
    gbrain_people = _gbrain_people_count(con)
    top_gbrain_people = _gbrain_people_count(con, person_ids=top_person_ids)
    connectors = [
        _connector("Google People", _token_status(con, "google", ("https://www.googleapis.com/auth/contacts.readonly",)), "network-chief auth-google"),
        _connector("Gmail API", _token_status(con, "google", ("https://www.googleapis.com/auth/gmail.readonly",)), "network-chief auth-google"),
        _connector("Gmail Drafts", _token_status(con, "google", ("https://www.googleapis.com/auth/gmail.compose",)), "network-chief auth-google"),
        _connector("LinkedIn posting", _linkedin_posting_status(con), "network-chief auth-linkedin --posting"),
        _connector("X API", _token_status(con, "x", ("follows.read",)), "network-chief auth-x"),
        {
            "name": "Telegram identities",
            "ok": coverage["telegram_send_enabled"] > 0,
            "status": "ok" if coverage["telegram_send_enabled"] > 0 else "missing",
            "detail": f"{coverage['telegram_send_enabled']} send-enabled Telegram account(s)",
            "command": "network-chief discover-telegram OR network-chief import-telegram --file exports/telegram_map.csv --lookup email",
        },
        {
            "name": "gbrain",
            "ok": adapter.available(),
            "status": "ok" if adapter.available() else "missing",
            "detail": f"binary={adapter.binary}, indexed people={gbrain_people}",
            "command": "install/configure gbrain, then network-chief next-actions --limit 25 --out data/next-actions.md",
        },
    ]
    missing_sources = [
        {
            "source": source,
            "last_at": source_runs.get(source),
            "command": _source_command(source),
        }
        for source in EXPECTED_WEEKLY_SOURCES
        if source not in source_runs
    ]
    findings: list[str] = []
    for connector in connectors:
        if not connector["ok"]:
            findings.append(f"{connector['name']} is {connector['status']}: {connector['command']}")
    if top_people and top_reachable < len(top_people):
        findings.append(f"Only {top_reachable}/{len(top_people)} top-ranked people have an actionable channel.")
    if top_people and top_gbrain_people < min(25, len(top_people)):
        findings.append(
            f"gbrain context covers {top_gbrain_people}/{min(25, len(top_people))} target top people; run next-actions with gbrain enabled."
        )
    return {
        "captured_at": _now_iso(),
        "connectors": connectors,
        "coverage": coverage,
        "top_queue": {
            "requested": top_n,
            "people": len(top_people),
            "reachable": top_reachable,
            "send_enabled": top_send_enabled,
            "gbrain_people": top_gbrain_people,
            "sample_queries": [_person_context_query(person) for person in top_people[:5]],
        },
        "source_runs": source_runs,
        "missing_sources": missing_sources,
        "findings": findings,
    }


def render_source_health_markdown(health: dict[str, Any]) -> str:
    coverage = health["coverage"]
    top = health["top_queue"]
    lines = [
        "# Network Chief Source Health",
        "",
        f"_Captured at {health['captured_at']}._",
        "",
        "## Connector Health",
        "",
    ]
    for connector in health["connectors"]:
        marker = "OK" if connector["ok"] else "NEEDS SETUP"
        lines.append(f"- **{connector['name']}**: {marker} - {connector['detail']}")
        if not connector["ok"]:
            lines.append(f"  Command: `{connector['command']}`")
    lines.extend(
        [
            "",
            "## Channel Coverage",
            "",
            f"- People: {coverage['people']}",
            f"- Email: {_pct(coverage['email'], coverage['people'])}% ({coverage['email']})",
            f"- LinkedIn: {_pct(coverage['linkedin'], coverage['people'])}% ({coverage['linkedin']})",
            f"- Telegram: {_pct(coverage['telegram'], coverage['people'])}% ({coverage['telegram']}); send-enabled {coverage['telegram_send_enabled']}",
            f"- X/Twitter: {_pct(coverage['x'], coverage['people'])}% ({coverage['x']})",
            f"- Gmail send-enabled accounts: {coverage['gmail_send_enabled']}",
            "",
            "## Top Queue Readiness",
            "",
            f"- Ranked people checked: {top['people']}/{top['requested']}",
            f"- Actionable channel: {top['reachable']}/{top['people']}",
            f"- Send-enabled direct path: {top['send_enabled']}/{top['people']}",
            f"- gbrain context in top queue: {top['gbrain_people']}/{min(25, top['people']) if top['people'] else 0}",
            "",
            "## Missing Weekly Sources",
            "",
        ]
    )
    if health["missing_sources"]:
        for item in health["missing_sources"]:
            lines.append(f"- `{item['source']}` - last <never>; run `{item['command']}`")
    else:
        lines.append("- none")
    lines.extend(["", "## Recommended Next Commands", ""])
    if health["findings"]:
        for finding in health["findings"]:
            lines.append(f"- {finding}")
    else:
        lines.append("- Source and channel readiness looks healthy.")
    return "\n".join(lines).rstrip() + "\n"


def build_outcome_sweep(con: sqlite3.Connection, *, since_days: int = 7) -> dict[str, Any]:
    cutoff = _now_minus(days=since_days)
    approved = _approved_without_delivery(con, cutoff=cutoff)
    delivered = _delivered_without_outcome(con, cutoff=cutoff)
    linkedin_metrics = _linkedin_metrics_needed(con, cutoff=cutoff)
    return {
        "captured_at": _now_iso(),
        "since_days": since_days,
        "approved_without_delivery": approved,
        "delivered_without_outcome": delivered,
        "linkedin_metrics_needed": linkedin_metrics,
    }


def render_outcome_sweep_markdown(sweep: dict[str, Any]) -> str:
    lines = [
        "# Network Chief Outcome Sweep",
        "",
        f"_Captured at {sweep['captured_at']}; window: last {sweep['since_days']} days._",
        "",
        "## Approved But Not Delivered",
        "",
    ]
    if sweep["approved_without_delivery"]:
        for row in sweep["approved_without_delivery"]:
            lines.append(f"- `{row['id']}` {row.get('full_name') or 'Public audience'} / {row['channel']}: {row.get('subject') or '(none)'}")
            lines.append(f"  Route: {row['route']}")
    else:
        lines.append("- none")
    lines.extend(["", "## Delivered Without Outcome", ""])
    if sweep["delivered_without_outcome"]:
        for row in sweep["delivered_without_outcome"]:
            lines.append(
                f"- `{row['id']}` {row.get('full_name') or 'Public audience'} / {row['channel']} delivered at {row['delivered_at']}"
            )
            lines.append(
                f"  Label: `network-chief record-engagement-outcome --draft-id {row['id']} --outcome reply|useful_conversation|meeting|no_response|bad_fit`"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## LinkedIn Metrics Needed", ""])
    if sweep["linkedin_metrics_needed"]:
        for row in sweep["linkedin_metrics_needed"]:
            missing = ", ".join(row["missing_metrics"])
            lines.append(f"- `{row['id']}` published at {row['published_at']} missing: {missing}")
            for metric in row["missing_metrics"]:
                lines.append(
                    f"  `network-chief record-audience-metric --channel linkedin --metric-type {metric} --value <n> --draft-id {row['id']}`"
                )
    else:
        lines.append("- none")
    return "\n".join(lines).rstrip() + "\n"


def _pending_drafts(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT
            d.*,
            p.full_name,
            p.primary_email,
            p.linkedin_url,
            p.telegram_handle,
            g.title AS goal_title,
            r.warmth,
            r.trust,
            r.strength,
            COALESCE((SELECT max(score) FROM connection_values cv WHERE cv.person_id = d.person_id), 0) AS max_value_score
          FROM drafts d
          LEFT JOIN people p ON p.id = d.person_id
          LEFT JOIN goals g ON g.id = d.goal_id
          LEFT JOIN relationships r ON r.person_id = d.person_id
         WHERE d.status = 'draft'
         ORDER BY d.created_at DESC
        """
    ).fetchall()
    return rows_to_dicts(rows)


def _draft_group_key(draft: dict[str, Any]) -> str:
    person_id = draft.get("person_id")
    if person_id:
        return "|".join([str(person_id), str(draft.get("channel") or ""), str(draft.get("goal_id") or "")])
    return "|".join(
        [
            "public",
            str(draft.get("channel") or ""),
            str(draft.get("subject") or ""),
            str(draft.get("body") or ""),
        ]
    )


def _draft_priority_score(con: sqlite3.Connection, draft: dict[str, Any], *, duplicate_count: int) -> int:
    channel = str(draft.get("channel") or "")
    channel_weight = {
        "linkedin_post": 95,
        "gmail": 85,
        "telegram": 82,
        "linkedin": 70,
        "x_comment": 68,
        "x_post": 65,
        "note": 25,
    }.get(channel, 40)
    value = int(draft.get("max_value_score") or 0)
    relationship = max(int(draft.get("warmth") or 0), int(draft.get("trust") or 0), int(draft.get("strength") or 0))
    score = channel_weight + value + min(16, duplicate_count * 4) + round(relationship / 5)
    if draft.get("goal_id"):
        score += 10
    if channel == "gmail" and (draft.get("primary_email") or _has_account(con, draft.get("person_id"), "gmail")):
        score += 10
    if channel == "telegram" and has_send_enabled_account(con, person_id=str(draft.get("person_id") or ""), channel="telegram"):
        score += 14
    return score


def _has_account(con: sqlite3.Connection, person_id: Any, channel: str) -> bool:
    if not person_id:
        return False
    row = con.execute(
        "SELECT 1 FROM channel_accounts WHERE person_id = ? AND channel = ? LIMIT 1",
        (str(person_id), channel),
    ).fetchone()
    return row is not None


def _has_reachable_channel(con: sqlite3.Connection, person: dict[str, Any]) -> bool:
    return bool(
        person.get("primary_email")
        or person.get("linkedin_url")
        or person.get("twitter_handle")
        or has_send_enabled_account(con, person_id=str(person["id"]), channel="telegram")
    )


def _has_send_enabled_path(con: sqlite3.Connection, person: dict[str, Any]) -> bool:
    return bool(
        has_send_enabled_account(con, person_id=str(person["id"]), channel="gmail")
        or has_send_enabled_account(con, person_id=str(person["id"]), channel="telegram")
    )


def _person_context_query(person: dict[str, Any]) -> str:
    parts = [
        str(person.get("full_name") or ""),
        str(person.get("organizations") or ""),
        str(person.get("titles") or ""),
    ]
    return " ".join(part for part in parts if part).strip()


def _token_status(con: sqlite3.Connection, provider: str, required_scopes: tuple[str, ...]) -> dict[str, Any]:
    record = TokenStore(con).get(provider)
    if record is None:
        env_ok = provider == "google" and bool(os.environ.get("GMAIL_ACCESS_TOKEN"))
        if env_ok:
            return {"ok": True, "status": "ok", "detail": "GMAIL_ACCESS_TOKEN present; saved token missing"}
        return {"ok": False, "status": "missing", "detail": "no saved OAuth token"}
    if is_expired(record.get("expires_at")):
        return {"ok": False, "status": "expired", "detail": f"token expired at {record.get('expires_at')}"}
    scopes = _scope_set(record.get("scopes") or "")
    missing = [scope for scope in required_scopes if scope not in scopes]
    if missing:
        return {"ok": False, "status": "missing_scope", "detail": "missing scope(s): " + ", ".join(missing)}
    return {"ok": True, "status": "ok", "detail": f"account={record.get('account')}, scopes={len(scopes)}"}


def _linkedin_posting_status(con: sqlite3.Connection) -> dict[str, Any]:
    record = TokenStore(con).get("linkedin")
    if record is None:
        return {"ok": False, "status": "missing", "detail": "no saved LinkedIn OAuth token"}
    if is_expired(record.get("expires_at")):
        return {"ok": False, "status": "expired", "detail": f"token expired at {record.get('expires_at')}"}
    scopes = _scope_set(record.get("scopes") or "")
    if "w_member_social" not in scopes and "w_organization_social" not in scopes:
        return {"ok": False, "status": "missing_scope", "detail": "missing w_member_social/w_organization_social"}
    return {"ok": True, "status": "ok", "detail": f"account={record.get('account')}"}


def _connector(name: str, status: dict[str, Any], command: str) -> dict[str, Any]:
    return {"name": name, "command": command, **status}


def _scope_set(scopes: str) -> set[str]:
    return {part.strip() for part in scopes.replace(",", " ").split() if part.strip()}


def _latest_source_runs(con: sqlite3.Connection) -> dict[str, str]:
    rows = con.execute("SELECT source, max(finished_at) AS last_at FROM source_runs GROUP BY source").fetchall()
    return {str(row["source"]): str(row["last_at"]) for row in rows}


def _source_command(source: str) -> str:
    return {
        "google_people": "network-chief sync-google --limit 1000",
        "gmail_api": "network-chief sync-google --skip-people",
        "x_api_following": "network-chief sync-x --max-pages 5",
        "x_api_mentions": "network-chief sync-x --skip-following --max-pages 5",
        "linkedin_oidc": "network-chief sync-linkedin",
        "telegram_discovery": "network-chief discover-telegram",
        "next_actions": "network-chief next-actions --limit 10 --out data/next-actions.md",
        "gbrain_writeback": "network-chief sync-gbrain --since-days 7 --mode auto-summary",
    }.get(source, "network-chief sync-sources --include-downloads")


def _approved_without_delivery(con: sqlite3.Connection, *, cutoff: str) -> list[dict[str, Any]]:
    rows = rows_to_dicts(
        con.execute(
            """
            SELECT d.*, p.full_name, p.primary_email, p.telegram_handle
              FROM drafts d
              LEFT JOIN people p ON p.id = d.person_id
             WHERE d.updated_at >= ?
               AND d.status = 'approved'
               AND NOT EXISTS (
                   SELECT 1 FROM draft_events e
                    WHERE e.draft_id = d.id
                      AND e.event_type IN ('sent', 'published')
               )
             ORDER BY d.updated_at DESC
            """,
            (cutoff,),
        ).fetchall()
    )
    for row in rows:
        row["route"] = safe_execution_route(con, row)
    return rows


def _delivered_without_outcome(con: sqlite3.Connection, *, cutoff: str) -> list[dict[str, Any]]:
    return rows_to_dicts(
        con.execute(
            """
            SELECT
                d.*,
                p.full_name,
                max(CASE WHEN e.event_type IN ('sent', 'published') THEN e.created_at END) AS delivered_at
              FROM drafts d
              LEFT JOIN people p ON p.id = d.person_id
              JOIN draft_events e ON e.draft_id = d.id
             GROUP BY d.id
            HAVING delivered_at >= ?
               AND sum(CASE WHEN e.event_type IN ('response', 'responded', 'converted', 'outcome', 'no_response') THEN 1 ELSE 0 END) = 0
             ORDER BY delivered_at DESC
            """,
            (cutoff,),
        ).fetchall()
    )


def _linkedin_metrics_needed(con: sqlite3.Connection, *, cutoff: str) -> list[dict[str, Any]]:
    rows = rows_to_dicts(
        con.execute(
            """
            SELECT
                d.*,
                max(e.created_at) AS published_at
              FROM drafts d
              JOIN draft_events e ON e.draft_id = d.id AND e.event_type = 'published'
             WHERE d.channel = 'linkedin_post'
             GROUP BY d.id
            HAVING published_at >= ?
             ORDER BY published_at DESC
            """,
            (cutoff,),
        ).fetchall()
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        seen = {
            str(metric["metric_type"])
            for metric in con.execute(
                "SELECT metric_type FROM audience_metrics WHERE draft_id = ? AND channel = 'linkedin'",
                (row["id"],),
            ).fetchall()
        }
        missing = [metric for metric in LINKEDIN_METRICS if metric not in seen]
        if missing:
            row["missing_metrics"] = missing
            out.append(row)
    return out


def _gbrain_people_count(con: sqlite3.Connection, *, person_ids: list[str] | None = None) -> int:
    if person_ids is not None and not person_ids:
        return 0
    if person_ids is None:
        return _scalar(
            con,
            """
            SELECT count(DISTINCT person_id)
              FROM source_facts
             WHERE source = 'gbrain'
               AND fact_type = 'gbrain_context'
               AND person_id IS NOT NULL
            """,
        )
    placeholders = ",".join("?" for _ in person_ids)
    return _scalar(
        con,
        f"""
        SELECT count(DISTINCT person_id)
          FROM source_facts
         WHERE source = 'gbrain'
           AND fact_type = 'gbrain_context'
           AND person_id IN ({placeholders})
        """,
        tuple(person_ids),
    )


def _scalar(con: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> int:
    row = con.execute(query, params).fetchone()
    return int(row[0] or 0) if row else 0


def _pct(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((part / total) * 100, 1)


def _truncate(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _now_minus(*, days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
