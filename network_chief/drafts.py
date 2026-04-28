from __future__ import annotations

import sqlite3
from typing import Any

from .db import new_id, now_iso, rows_to_dicts


def choose_channel(person: dict[str, Any]) -> str:
    if person.get("primary_email"):
        return "gmail"
    if person.get("telegram_handle"):
        return "telegram"
    if person.get("whatsapp_phone") or person.get("phone"):
        return "whatsapp"
    if person.get("linkedin_url"):
        return "linkedin"
    return "note"


def compose_draft(person: dict[str, Any], goal: dict[str, Any] | None = None) -> dict[str, str]:
    name = str(person.get("full_name") or "there").split()[0]
    orgs = person.get("organizations") or "your current work"
    titles = person.get("titles") or ""
    goal_title = goal.get("title") if goal else None
    success_metric = goal.get("success_metric") if goal else None

    if goal_title:
        subject = f"Quick catch-up around {goal_title}"
        body = (
            f"Hi {name},\n\n"
            f"I was thinking about your work around {orgs}"
            f"{f' ({titles})' if titles else ''} and wanted to reconnect.\n\n"
            f"I am currently focused on: {goal_title}."
            f"{f' The concrete outcome I am aiming for is {success_metric}.' if success_metric else ''}\n\n"
            "Would you be open to a short catch-up next week? I would be glad to hear what you are working on "
            "and see where I can be useful as well.\n\n"
            "Best,\n"
            "Andrey"
        )
        rationale = f"Goal-linked outreach: {goal_title}"
    else:
        subject = "Quick catch-up"
        body = (
            f"Hi {name},\n\n"
            "I wanted to reconnect and hear what you are focused on these days. "
            f"I have {orgs} associated with your current work in my notes.\n\n"
            "Would you be open to a short catch-up sometime soon?\n\n"
            "Best,\n"
            "Andrey"
        )
        rationale = "Relationship maintenance draft"

    return {"subject": subject, "body": body, "rationale": rationale}


def create_draft(
    con: sqlite3.Connection,
    *,
    person: dict[str, Any],
    goal: dict[str, Any] | None = None,
    channel: str | None = None,
    status: str = "draft",
) -> str:
    draft = compose_draft(person, goal)
    ts = now_iso()
    channel = channel or choose_channel(person)
    existing = con.execute(
        """
        SELECT id FROM drafts
         WHERE COALESCE(person_id, '') = COALESCE(?, '')
           AND COALESCE(goal_id, '') = COALESCE(?, '')
           AND channel = ?
           AND status = 'draft'
           AND substr(created_at, 1, 10) = ?
        """,
        (person.get("id"), goal.get("id") if goal else None, channel, ts[:10]),
    ).fetchone()
    if existing:
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
           AND substr(created_at, 1, 10) = ?
        """,
        (person_id, goal_id, channel, subject or "", body, ts[:10]),
    ).fetchone()
    if existing:
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


def set_draft_status(con: sqlite3.Connection, draft_id: str, status: str) -> bool:
    cur = con.execute(
        "UPDATE drafts SET status = ?, updated_at = ? WHERE id = ?",
        (status, now_iso(), draft_id),
    )
    con.commit()
    return cur.rowcount > 0
