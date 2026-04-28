from __future__ import annotations

import sqlite3
from typing import Any

from .db import rows_to_dicts
from .drafts import create_custom_draft, create_draft
from .scoring import rank_people


def _active_goals(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute("SELECT * FROM goals WHERE status = 'active' ORDER BY created_at DESC").fetchall()
    return rows_to_dicts(rows)


def _topic(con: sqlite3.Connection, topic: str | None) -> tuple[str, dict[str, Any] | None]:
    goals = _active_goals(con)
    if topic:
        return topic, goals[0] if goals else None
    if goals:
        return str(goals[0]["title"]), goals[0]
    return "useful network building", None


def prepare_gmail_keepalive(con: sqlite3.Connection, *, limit: int = 10) -> list[str]:
    draft_ids: list[str] = []
    for person in rank_people(con, limit=limit * 3):
        if not person.get("primary_email"):
            continue
        draft_ids.append(create_draft(con, person=person, goal=person.get("goal"), channel="gmail"))
        if len(draft_ids) >= limit:
            break
    return draft_ids


def prepare_linkedin_posts(con: sqlite3.Connection, *, topic: str | None = None, count: int = 3) -> list[str]:
    topic_text, goal = _topic(con, topic)
    templates = [
        (
            "LinkedIn post: network signal",
            (
                f"I am mapping practical lessons around {topic_text}.\n\n"
                "The pattern I keep seeing: useful networks compound when people know exactly what you are building, "
                "what kind of help would matter, and what you can give back.\n\n"
                "I am especially interested in operators, investors, and domain experts who like concrete execution.\n\n"
                "Who should I learn from next?"
            ),
        ),
        (
            "LinkedIn post: ask the network",
            (
                f"Current focus: {topic_text}.\n\n"
                "I am collecting sharp examples of people turning expert knowledge, capital, or operational leverage "
                "into measurable outcomes.\n\n"
                "If someone in your network is unusually good at this, I would appreciate an introduction."
            ),
        ),
        (
            "LinkedIn post: give-first loop",
            (
                f"I am spending this week reconnecting around {topic_text}.\n\n"
                "If you are building in this area and a useful second opinion, intro, or structured feedback would help, "
                "send me a short note. I am happy to compare notes and route people where I can."
            ),
        ),
    ]
    draft_ids: list[str] = []
    for subject, body in templates[: max(0, count)]:
        draft_ids.append(
            create_custom_draft(
                con,
                channel="linkedin_post",
                subject=subject,
                body=body,
                rationale=f"Professional network engagement around {topic_text}",
                goal_id=goal.get("id") if goal else None,
            )
        )
    return draft_ids


def prepare_x_posts(con: sqlite3.Connection, *, topic: str | None = None, count: int = 3) -> list[str]:
    topic_text, goal = _topic(con, topic)
    templates = [
        (
            "X post: crisp insight",
            f"Strong networks are not contact lists. They are living maps of trust, timing, competence, and specific asks. Testing this around {topic_text}.",
        ),
        (
            "X post: community ask",
            f"Who is doing unusually practical work on {topic_text}? Looking for people with real execution lessons, not generic takes.",
        ),
        (
            "X post: build in public",
            f"This week I am tuning my network system to track value signals: capital, time saved, competence, and specific knowledge. The hard part is timing the right ask.",
        ),
    ]
    draft_ids: list[str] = []
    for subject, body in templates[: max(0, count)]:
        draft_ids.append(
            create_custom_draft(
                con,
                channel="x_post",
                subject=subject,
                body=body,
                rationale=f"Community engagement and follower growth around {topic_text}",
                goal_id=goal.get("id") if goal else None,
            )
        )
    return draft_ids


def _x_people(con: sqlite3.Connection, limit: int) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT
            people.*,
            relationships.last_interaction_at,
            latest_x.body_summary AS latest_x_body
          FROM people
          LEFT JOIN relationships ON relationships.person_id = people.id
          LEFT JOIN interactions AS latest_x
            ON latest_x.id = (
                SELECT interactions.id
                  FROM interactions
                 WHERE interactions.person_id = people.id
                   AND interactions.channel = 'x'
                 ORDER BY interactions.occurred_at DESC, interactions.created_at DESC
                 LIMIT 1
            )
         WHERE people.twitter_handle IS NOT NULL AND people.twitter_handle != ''
         ORDER BY
            CASE WHEN relationships.last_interaction_at IS NULL THEN 0 ELSE 1 END,
            relationships.last_interaction_at ASC,
            people.updated_at DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return rows_to_dicts(rows)


def prepare_x_comments(con: sqlite3.Connection, *, topic: str | None = None, count: int = 5) -> list[str]:
    topic_text, goal = _topic(con, topic)
    draft_ids: list[str] = []
    for person in _x_people(con, count):
        handle = person.get("twitter_handle")
        latest = str(person.get("latest_x_body") or "").strip()
        if latest:
            body = (
                f"Comment draft for @{handle} on: {latest[:180]}\n\n"
                f"Strong point. The part I would add from {topic_text}: "
                "the best outcomes usually come from pairing a specific ask with a clear give-back."
            )
        else:
            body = (
                f"Comment angle for @{handle}: Strong point. The part I would add from {topic_text}: "
                "the best outcomes usually come from pairing a specific ask with a clear give-back."
            )
        draft_ids.append(
            create_custom_draft(
                con,
                channel="x_comment",
                person_id=person["id"],
                goal_id=goal.get("id") if goal else None,
                subject=f"X comment for @{handle}",
                body=body,
                rationale=f"Lightweight community engagement with {person['full_name']}",
            )
        )
    return draft_ids
