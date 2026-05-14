from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from .brief import build_daily_brief
from .db import rows_to_dicts
from .engagement import prepare_gmail_keepalive, prepare_linkedin_posts, prepare_x_comments, prepare_x_posts
from .value import maintain_connection_values


def prepare_audience_brief(
    con: sqlite3.Connection,
    *,
    limit: int = 10,
    topic: str | None = None,
    linkedin_posts: int = 2,
    x_posts: int = 2,
    x_comments: int = 5,
    gmail_followups: int = 3,
    create_draft_records: bool = True,
) -> str:
    maintenance = maintain_connection_values(con)
    prepared: list[tuple[str, list[str]]] = []
    if linkedin_posts > 0:
        prepared.append(("LinkedIn posts", prepare_linkedin_posts(con, topic=topic, count=linkedin_posts)))
    if x_posts > 0:
        prepared.append(("X posts", prepare_x_posts(con, topic=topic, count=x_posts)))
    if x_comments > 0:
        prepared.append(("X comments", prepare_x_comments(con, topic=topic, count=x_comments)))
    if gmail_followups > 0:
        prepared.append(("Strategic follow-ups", prepare_gmail_keepalive(con, limit=gmail_followups)))

    brief = build_daily_brief(con, limit=limit, create_draft_records=create_draft_records, mode="audience")
    lines = [
        brief,
        "## Drafts Prepared",
        "",
        f"- Value maintenance: {maintenance['people_scanned']} people scanned, {maintenance['values_seen']} value signals seen",
    ]
    for label, draft_ids in prepared:
        joined = ", ".join(draft_ids) if draft_ids else "none"
        lines.append(f"- {label}: {joined}")
    return "\n".join(lines)


def _cutoff(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _count_rows(con: sqlite3.Connection, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return rows_to_dicts(con.execute(query, params).fetchall())


def build_scorecard(con: sqlite3.Connection, *, days: int = 7) -> str:
    cutoff = _cutoff(days)
    created = _count_rows(
        con,
        """
        SELECT channel, count(*) AS count
          FROM drafts
         WHERE created_at >= ?
         GROUP BY channel
         ORDER BY count DESC, channel
        """,
        (cutoff,),
    )
    events = _count_rows(
        con,
        """
        SELECT event_type, count(*) AS count
          FROM draft_events
         WHERE created_at >= ?
         GROUP BY event_type
         ORDER BY count DESC, event_type
        """,
        (cutoff,),
    )
    channel_events = _count_rows(
        con,
        """
        SELECT drafts.channel, draft_events.event_type, count(*) AS count
          FROM draft_events
          JOIN drafts ON drafts.id = draft_events.draft_id
         WHERE draft_events.created_at >= ?
         GROUP BY drafts.channel, draft_events.event_type
         ORDER BY drafts.channel, draft_events.event_type
        """,
        (cutoff,),
    )
    reasons = _count_rows(
        con,
        """
        SELECT reason_code, count(*) AS count
          FROM draft_events
         WHERE created_at >= ?
           AND reason_code IS NOT NULL
           AND reason_code != ''
         GROUP BY reason_code
         ORDER BY count DESC, reason_code
        """,
        (cutoff,),
    )
    metrics = _count_rows(
        con,
        """
        SELECT channel, metric_type, sum(value) AS value
          FROM audience_metrics
         WHERE observed_at >= ?
         GROUP BY channel, metric_type
         ORDER BY channel, metric_type
        """,
        (cutoff,),
    )
    subject_rows = _count_rows(
        con,
        """
        SELECT channel, COALESCE(subject, '') AS subject, count(*) AS count
          FROM drafts
         WHERE created_at >= ?
         GROUP BY channel, COALESCE(subject, '')
         ORDER BY count DESC, channel, subject
         LIMIT 10
        """,
        (cutoff,),
    )
    subject_stats = con.execute(
        "SELECT count(DISTINCT COALESCE(subject, '')) FROM drafts WHERE created_at >= ?",
        (cutoff,),
    ).fetchone()
    distinct_subjects = int((subject_stats[0] if subject_stats else 0) or 0)
    metric_totals = {
        (str(row["channel"]), str(row["metric_type"])): int(row["value"] or 0)
        for row in metrics
    }

    created_total = sum(int(row["count"]) for row in created)
    event_counts = {str(row["event_type"]): int(row["count"]) for row in events}
    approved = event_counts.get("approve", 0) + event_counts.get("approved", 0)
    published = event_counts.get("published", 0)
    sent = event_counts.get("sent", 0)
    responses = (
        event_counts.get("response", 0)
        + event_counts.get("responded", 0)
        + event_counts.get("converted", 0)
    )
    approval_rate = round((approved / created_total) * 100) if created_total else 0
    response_rate = round((responses / max(1, published)) * 100) if published else 0

    today = datetime.now(UTC).date().isoformat()
    lines = [
        f"# Network Chief Audience Scorecard - {today}",
        "",
        f"Window: last {days} days",
        "",
        "## KPIs",
        "",
        f"- Drafts created: {created_total}",
        f"- Draft approvals: {approved} ({approval_rate}% of created drafts)",
        f"- Published/sent items: {published + sent}",
        f"- Responses recorded: {responses} ({response_rate}% of published items)",
        "",
        "## Drafts Created by Channel",
        "",
    ]
    lines.extend(_format_count_rows(created, "channel"))
    lines.extend(["", "## Events", ""])
    lines.extend(_format_count_rows(events, "event_type"))
    lines.extend(["", "## Events by Channel", ""])
    if channel_events:
        for row in channel_events:
            lines.append(f"- {row['channel']} / {row['event_type']}: {row['count']}")
    else:
        lines.append("- none")
    lines.extend(["", "## Channel Conversion", ""])
    lines.extend(_format_channel_conversion(created=created, channel_events=channel_events, metrics=metrics))
    lines.extend(["", "## Template Signals", ""])
    lines.extend(_format_template_signals(created_total=created_total, distinct_subjects=distinct_subjects, subject_rows=subject_rows))
    lines.extend(["", "## Audience Metrics", ""])
    if metrics:
        for row in metrics:
            lines.append(f"- {row['channel']} / {row['metric_type']}: {row['value']}")
    else:
        lines.append("- none")
    lines.extend(["", "## Review Signals", ""])
    lines.extend(_format_count_rows(reasons, "reason_code"))
    lines.extend(["", "## Adaptation Suggestions", ""])
    lines.extend(
        _adaptation_suggestions(
            metric_totals=metric_totals,
            event_counts=event_counts,
            published=published,
            created_total=created_total,
            distinct_subjects=distinct_subjects,
            subject_rows=subject_rows,
        )
    )
    return "\n".join(lines)


def _format_count_rows(rows: list[dict[str, Any]], label: str) -> list[str]:
    if not rows:
        return ["- none"]
    return [f"- {row[label]}: {row['count']}" for row in rows]


def _format_channel_conversion(
    *,
    created: list[dict[str, Any]],
    channel_events: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
) -> list[str]:
    channels = {str(row["channel"]) for row in created}
    channels.update(str(row["channel"]) for row in channel_events)
    channels.update(str(row["channel"]) for row in metrics)
    if not channels:
        return ["- none"]
    created_by_channel = {str(row["channel"]): int(row["count"]) for row in created}
    event_by_channel: dict[tuple[str, str], int] = {}
    for row in channel_events:
        event_by_channel[(str(row["channel"]), str(row["event_type"]))] = int(row["count"])
    metric_by_channel: dict[tuple[str, str], int] = {}
    for row in metrics:
        metric_by_channel[(str(row["channel"]), str(row["metric_type"]))] = int(row["value"] or 0)

    lines: list[str] = []
    for channel in sorted(channels):
        channel_created = created_by_channel.get(channel, 0)
        approved = event_by_channel.get((channel, "approve"), 0) + event_by_channel.get((channel, "approved"), 0)
        delivered = event_by_channel.get((channel, "sent"), 0) + event_by_channel.get((channel, "published"), 0)
        responses = (
            event_by_channel.get((channel, "response"), 0)
            + event_by_channel.get((channel, "responded"), 0)
            + event_by_channel.get((channel, "converted"), 0)
        )
        meetings = metric_by_channel.get((channel, "meetings"), 0)
        lines.append(
            "- "
            f"{channel}: created {channel_created}, approved {approved} ({_rate(approved, channel_created)}%), "
            f"delivered {delivered} ({_rate(delivered, approved)}% of approved), "
            f"responses {responses} ({_rate(responses, delivered)}% of delivered), meetings {meetings}"
        )
    return lines


def _format_template_signals(
    *,
    created_total: int,
    distinct_subjects: int,
    subject_rows: list[dict[str, Any]],
) -> list[str]:
    if created_total == 0:
        return ["- none"]
    diversity = round(distinct_subjects / created_total, 3)
    lines = [f"- Subject diversity: {distinct_subjects}/{created_total} = {diversity}"]
    repeated = [row for row in subject_rows if int(row["count"]) > 1]
    if repeated:
        lines.append("- Top repeated subjects:")
        for row in repeated[:5]:
            subject = row["subject"] or "(none)"
            lines.append(f"  - {row['channel']} / {subject}: {row['count']}")
    else:
        lines.append("- No repeated subjects in the window.")
    return lines


def _rate(part: int, total: int) -> int:
    return round((part / total) * 100) if total else 0


def _adaptation_suggestions(
    *,
    metric_totals: dict[tuple[str, str], int],
    event_counts: dict[str, int],
    published: int,
    created_total: int,
    distinct_subjects: int,
    subject_rows: list[dict[str, Any]],
) -> list[str]:
    suggestions: list[str] = []
    linkedin_impressions = metric_totals.get(("linkedin", "impressions"), 0)
    linkedin_reactions = metric_totals.get(("linkedin", "reactions"), 0)
    linkedin_comments = metric_totals.get(("linkedin", "comments"), 0)
    linkedin_reposts = metric_totals.get(("linkedin", "reposts"), 0)
    linkedin_follows = metric_totals.get(("linkedin", "follows"), 0) + metric_totals.get(("linkedin", "profile_views"), 0)
    responses = (
        event_counts.get("response", 0)
        + event_counts.get("responded", 0)
        + event_counts.get("converted", 0)
    )
    useful_conversations = sum(
        metric_totals.get((channel, "useful_conversations"), 0)
        for channel in ("linkedin", "gmail", "telegram", "x")
    )
    meetings = sum(
        metric_totals.get((channel, "meetings"), 0)
        for channel in ("linkedin", "gmail", "telegram", "x")
    )

    if published == 0 and event_counts.get("sent", 0) == 0:
        suggestions.append("- Publish one high-signal post before changing templates; there is no outcome baseline yet.")
    if not metric_totals:
        suggestions.append("- After posting, record 2-hour and 24-hour LinkedIn metrics so tomorrow's post can adapt.")
    subject_diversity = (distinct_subjects / created_total) if created_total else 1.0
    if created_total > 5 and subject_diversity < 0.3:
        suggestions.append("- Draft templates are repeating; use the review queue to approve/reject one grouped item instead of creating more same-subject drafts.")
    if not metric_totals:
        return suggestions
    if linkedin_impressions and linkedin_reactions == 0 and linkedin_comments == 0:
        suggestions.append("- The topic reached people but did not invite action; make tomorrow's opening more opinionated and the question narrower.")
    if linkedin_reactions > 0 and linkedin_comments == 0:
        suggestions.append("- Reactions without comments usually mean the CTA is too broad; ask a forced-choice question next.")
    if linkedin_comments > 0:
        suggestions.append("- Turn the best comment into a follow-up DM and tomorrow's post hook; public comments are relationship openings.")
    if linkedin_reposts > 0:
        suggestions.append("- Reposts signal portable framing; keep the same theme and add one sharper data point in the next post.")
    if linkedin_follows > 0:
        suggestions.append("- New profile attention should trigger a light follow-up list: inspect viewers/followers and draft 3 context-aware messages.")
    if responses > 0:
        suggestions.append("- Responses are the strongest signal; raise similar people/topics in the relationship ranking this week.")
    if useful_conversations > 0:
        suggestions.append("- Useful conversations beat vanity metrics; preserve the same specificity and move similar contacts earlier in next-actions.")
    if meetings > 0:
        suggestions.append("- Meetings booked are conversion signals; turn the path into a reusable follow-up timing experiment.")
    if not suggestions:
        suggestions.append("- Keep the cadence, but test one variable tomorrow: hook, data point, or CTA, not all three.")
    return suggestions
