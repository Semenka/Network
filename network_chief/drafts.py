from __future__ import annotations

import sqlite3
from typing import Any

from .db import new_id, now_iso, record_audience_metric, record_draft_event, rows_to_dicts


DRAFT_EVENT_STATUS: dict[str, str] = {
    "proposed": "proposed",
    "drafted": "draft",
    "approve": "approved",
    "approved": "approved",
    "reject": "rejected",
    "rejected": "rejected",
    "edit": "draft",
    "send_ready": "approved",
    "publish_ready": "approved",
    "sent": "sent",
    "published": "published",
    "response": "responded",
    "responded": "responded",
    "converted": "converted",
    "snoozed": "snoozed",
    "no_response": "no_response",
}

ENGAGEMENT_OUTCOME_EVENTS: dict[str, tuple[str, str, str]] = {
    "useful_conversation": ("response", "responded", "useful_conversations"),
    "reply": ("response", "responded", "replies"),
    "meeting": ("converted", "converted", "meetings"),
    "no_response": ("no_response", "no_response", "no_responses"),
    "bad_fit": ("reject", "rejected", "bad_fit"),
}


def choose_channel(person: dict[str, Any]) -> str:
    if person.get("telegram_handle"):
        return "telegram"
    if person.get("primary_email"):
        return "gmail"
    if person.get("whatsapp_phone") or person.get("phone"):
        return "whatsapp"
    if person.get("linkedin_url"):
        return "linkedin"
    return "note"


def compose_draft(
    person: dict[str, Any],
    goal: dict[str, Any] | None = None,
    *,
    channel: str | None = None,
    voice_summary: str | None = None,
) -> dict[str, str]:
    name = str(person.get("full_name") or "there").split()[0]
    orgs = person.get("organizations") or "your current work"
    titles = person.get("titles") or ""
    goal_title = goal.get("title") if goal else None
    success_metric = goal.get("success_metric") if goal else None
    channel = channel or choose_channel(person)
    is_telegram = channel.lower() == "telegram"
    signoff = "" if channel in {"telegram", "linkedin"} else "\n\nBest,\nAndrey"
    channel_prefix = "Hey" if channel == "telegram" else "Hi"

    if goal_title and is_telegram:
        subject = f"Catch-up — {goal_title}"
        body = (
            f"Hey {name} — quick one. I'm focused on {goal_title}"
            f"{f' ({success_metric})' if success_metric else ''} this week and was thinking about your "
            f"{orgs} work. Up for a 15-min call?"
        )
        rationale = f"Goal-linked Telegram outreach: {goal_title}"
    elif is_telegram:
        subject = "Catch-up"
        body = (
            f"Hey {name} — wanted to reconnect. I have {orgs} associated with you in my notes. "
            "Free for a quick 15-min call sometime soon?"
        )
        rationale = "Relationship maintenance — Telegram"
    elif goal_title:
        subject = f"Quick catch-up around {goal_title}"
        body = (
            f"{channel_prefix} {name},\n\n"
            f"I had your work around {orgs}{f' ({titles})' if titles else ''} in mind and wanted to reconnect.\n\n"
            f"I am focused on {goal_title} right now"
            f"{f', aiming for {success_metric}' if success_metric else ''}. "
            "I would value your view, and I am happy to share anything useful from what I am seeing as well.\n\n"
            "Would a short catch-up next week be easy?"
            f"{signoff}"
        )
        rationale = f"Goal-linked outreach: {goal_title}"
    else:
        subject = "Quick catch-up"
        body = (
            f"{channel_prefix} {name},\n\n"
            f"I was going through my network notes and saw {orgs} connected to your current work. "
            "I wanted to reconnect and hear what you are focused on now.\n\n"
            "Would a short catch-up sometime soon be easy?"
            f"{signoff}"
        )
        rationale = "Relationship maintenance draft"
    if voice_summary:
        rationale = f"{rationale}; voice: {voice_summary[:180]}"

    return {"subject": subject, "body": body, "rationale": rationale}


def create_draft(
    con: sqlite3.Connection,
    *,
    person: dict[str, Any],
    goal: dict[str, Any] | None = None,
    channel: str | None = None,
    status: str = "draft",
) -> str:
    ts = now_iso()
    channel = channel or choose_channel(person)
    try:
        from .voice import get_voice_profile_summary

        voice_summary = get_voice_profile_summary(con)
    except Exception:
        voice_summary = None
    draft = compose_draft(person, goal, channel=channel, voice_summary=voice_summary)
    existing = con.execute(
        """
        SELECT id FROM drafts
         WHERE COALESCE(person_id, '') = COALESCE(?, '')
           AND COALESCE(goal_id, '') = COALESCE(?, '')
           AND channel = ?
           AND status = 'draft'
         ORDER BY updated_at DESC, created_at DESC
         LIMIT 1
        """,
        (person.get("id"), goal.get("id") if goal else None, channel),
    ).fetchone()
    if existing:
        con.execute("UPDATE drafts SET updated_at = ? WHERE id = ?", (ts, existing["id"]))
        con.commit()
        return str(existing["id"])

    draft_id = new_id()
    con.execute(
        """
        INSERT INTO drafts (
            id, person_id, goal_id, channel, subject, body, rationale,
            status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            draft_id,
            person.get("id"),
            goal.get("id") if goal else None,
            channel,
            draft["subject"],
            draft["body"],
            draft["rationale"],
            status,
            ts,
            ts,
        ),
    )
    con.commit()
    return draft_id


def create_custom_draft(
    con: sqlite3.Connection,
    *,
    channel: str,
    body: str,
    subject: str | None = None,
    rationale: str | None = None,
    person_id: str | None = None,
    goal_id: str | None = None,
    status: str = "draft",
) -> str:
    ts = now_iso()
    existing = con.execute(
        """
        SELECT id FROM drafts
         WHERE COALESCE(person_id, '') = COALESCE(?, '')
           AND COALESCE(goal_id, '') = COALESCE(?, '')
           AND channel = ?
           AND COALESCE(subject, '') = COALESCE(?, '')
           AND body = ?
           AND status = 'draft'
         ORDER BY updated_at DESC, created_at DESC
         LIMIT 1
        """,
        (person_id, goal_id, channel, subject or "", body),
    ).fetchone()
    if existing:
        con.execute("UPDATE drafts SET updated_at = ? WHERE id = ?", (ts, existing["id"]))
        con.commit()
        return str(existing["id"])

    draft_id = new_id()
    con.execute(
        """
        INSERT INTO drafts (
            id, person_id, goal_id, channel, subject, body, rationale,
            status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (draft_id, person_id, goal_id, channel, subject, body, rationale, status, ts, ts),
    )
    con.commit()
    return draft_id


def list_drafts(con: sqlite3.Connection, status: str | None = "draft") -> list[dict[str, Any]]:
    if status:
        rows = con.execute(
            """
            SELECT drafts.*, people.full_name, people.primary_email
              FROM drafts
              LEFT JOIN people ON people.id = drafts.person_id
             WHERE drafts.status = ?
             ORDER BY drafts.created_at DESC
            """,
            (status,),
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT drafts.*, people.full_name, people.primary_email
              FROM drafts
              LEFT JOIN people ON people.id = drafts.person_id
             ORDER BY drafts.created_at DESC
            """
        ).fetchall()
    return rows_to_dicts(rows)


def set_draft_status(
    con: sqlite3.Connection,
    draft_id: str,
    status: str,
    *,
    reason_code: str | None = None,
    note: str | None = None,
    external_ref: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    cur = con.execute(
        "UPDATE drafts SET status = ?, updated_at = ? WHERE id = ?",
        (status, now_iso(), draft_id),
    )
    con.commit()
    if cur.rowcount <= 0:
        return False
    event_type = {"approved": "approve", "rejected": "reject"}.get(status)
    if event_type:
        record_draft_event(
            con,
            draft_id=draft_id,
            event_type=event_type,
            reason_code=reason_code,
            note=note,
            external_ref=external_ref,
            metadata=metadata,
        )
    return True


def apply_draft_event(
    con: sqlite3.Connection,
    *,
    draft_id: str,
    event_type: str,
    reason_code: str | None = None,
    note: str | None = None,
    external_ref: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    status = DRAFT_EVENT_STATUS.get(event_type)
    if status:
        cur = con.execute(
            "UPDATE drafts SET status = ?, updated_at = ? WHERE id = ?",
            (status, now_iso(), draft_id),
        )
        con.commit()
        if cur.rowcount <= 0:
            return None
    return record_draft_event(
        con,
        draft_id=draft_id,
        event_type=event_type,
        reason_code=reason_code,
        note=note,
        external_ref=external_ref,
        metadata=metadata,
    )


def record_engagement_outcome(
    con: sqlite3.Connection,
    *,
    draft_id: str,
    outcome: str,
    note: str | None = None,
    external_ref: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Record a normalized downstream outcome for a draft.

    The outcome is stored both as a draft event and as an audience metric so the
    weekly scorecard can learn from replies, meetings, and bad-fit signals.
    """

    if outcome not in ENGAGEMENT_OUTCOME_EVENTS:
        allowed = ", ".join(sorted(ENGAGEMENT_OUTCOME_EVENTS))
        raise ValueError(f"Unsupported outcome '{outcome}'. Expected one of: {allowed}")
    draft = con.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if not draft:
        return None

    event_type, status, metric_type = ENGAGEMENT_OUTCOME_EVENTS[outcome]
    merged_metadata = {"outcome": outcome, **(metadata or {})}
    event_id = apply_draft_event(
        con,
        draft_id=draft_id,
        event_type=event_type,
        reason_code=outcome,
        note=note,
        external_ref=external_ref,
        metadata=merged_metadata,
    )
    metric_id = record_audience_metric(
        con,
        channel=_metric_channel(str(draft["channel"])),
        metric_type=metric_type,
        value=1,
        draft_id=draft_id,
        person_id=draft["person_id"],
        goal_id=draft["goal_id"],
        note=note,
        external_ref=external_ref,
        metadata=merged_metadata,
    )
    return {"event_id": event_id, "metric_id": metric_id, "status": status, "outcome": outcome}


def _metric_channel(channel: str) -> str:
    if channel.startswith("linkedin"):
        return "linkedin"
    if channel.startswith("x_") or channel == "x":
        return "x"
    return channel
