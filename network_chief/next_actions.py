from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from .db import has_send_enabled_account, record_source_run, rows_to_dicts
from .drafts import choose_channel
from .gbrain import GBrainAdapter, GBrainResult, attach_gbrain_context_to_person
from .scoring import rank_people


@dataclass(frozen=True)
class NextAction:
    person_id: str | None
    person_name: str
    action_type: str
    channel: str
    score: int
    rationale: str
    weak_context: bool
    goal_title: str | None = None
    gbrain_refs: tuple[str, ...] = ()


def build_next_actions(
    con: sqlite3.Connection,
    *,
    limit: int = 10,
    adapter: GBrainAdapter | None = None,
    use_gbrain: bool = True,
) -> list[NextAction]:
    adapter = adapter or GBrainAdapter(enabled=use_gbrain)
    candidates: list[NextAction] = []
    seen: set[tuple[str | None, str, str]] = set()

    for person in rank_people(con, limit=max(limit * 3, 12), mode="relationship"):
        action = _action_for_person(con, person, mode="relationship", adapter=adapter, use_gbrain=use_gbrain)
        key = (action.person_id, action.action_type, action.channel)
        if key not in seen:
            candidates.append(action)
            seen.add(key)

    for person in rank_people(con, limit=max(limit * 3, 12), mode="audience"):
        action = _action_for_person(con, person, mode="audience", adapter=adapter, use_gbrain=use_gbrain)
        key = (action.person_id, action.action_type, action.channel)
        if key not in seen:
            candidates.append(action)
            seen.add(key)

    post_action = _public_post_action(con)
    if post_action:
        candidates.append(post_action)

    candidates.sort(key=lambda item: item.score, reverse=True)
    selected = candidates[:limit]
    record_source_run(
        con,
        source="next_actions",
        source_ref=None,
        status="ok",
        stats={"actions": len(selected), "gbrain_enabled": use_gbrain},
    )
    return selected


def format_next_actions(actions: list[NextAction]) -> str:
    lines = ["# Network Chief Next Best Actions", ""]
    if not actions:
        lines.append("- No actions available. Import sources or add active goals.")
        return "\n".join(lines)
    for index, action in enumerate(actions, start=1):
        weak = " yes" if action.weak_context else " no"
        lines.extend(
            [
                f"## {index}. {action.person_name}",
                "",
                f"- Action: {action.action_type}",
                f"- Channel: {action.channel}",
                f"- Score: {action.score}",
                f"- Weak context:{weak}",
                f"- Rationale: {action.rationale}",
            ]
        )
        if action.goal_title:
            lines.append(f"- Goal: {action.goal_title}")
        if action.gbrain_refs:
            lines.append(f"- GBrain: {', '.join(f'`{ref}`' for ref in action.gbrain_refs)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _action_for_person(
    con: sqlite3.Connection,
    person: dict[str, Any],
    *,
    mode: str,
    adapter: GBrainAdapter,
    use_gbrain: bool,
) -> NextAction:
    latest = _latest_interaction(con, str(person["id"]))
    channel = _best_channel(con, person, mode=mode)
    action_type = _action_type(person, latest=latest, mode=mode)
    gbrain_results: list[GBrainResult] = []
    if use_gbrain:
        query = _gbrain_query(person)
        gbrain_results = attach_gbrain_context_to_person(
            con,
            person_id=str(person["id"]),
            query=query,
            adapter=adapter,
            limit=2,
        )
    gbrain_refs = tuple(item.slug for item in gbrain_results)
    weak_context = _weak_context(person, gbrain_results)
    score = int(person.get("score") or 0)
    score += _channel_bonus(con, person, channel=channel)
    score += 10 if gbrain_results else 0
    if weak_context:
        score -= 20
    if action_type in {"warm_reply", "comment_opportunity"}:
        score += 12
    if action_type == "do_nothing":
        score -= 25
    rationale_parts = [str(person.get("rationale") or "ranked by Network Chief")]
    if latest:
        rationale_parts.append(f"latest {latest['channel']} {latest['direction'] or 'interaction'}: {latest['subject'] or latest['body_summary'] or latest['occurred_at']}")
    if gbrain_refs:
        rationale_parts.append("gbrain context found")
    if weak_context:
        rationale_parts.append("weak context; review before acting")
    goal = person.get("goal") or {}
    return NextAction(
        person_id=str(person["id"]),
        person_name=str(person.get("full_name") or "Unknown"),
        action_type=action_type,
        channel=channel,
        score=max(0, score),
        rationale="; ".join(rationale_parts),
        weak_context=weak_context,
        goal_title=goal.get("title"),
        gbrain_refs=gbrain_refs,
    )


def _latest_interaction(con: sqlite3.Connection, person_id: str) -> dict[str, Any] | None:
    row = con.execute(
        """
        SELECT * FROM interactions
         WHERE person_id = ?
         ORDER BY COALESCE(occurred_at, created_at) DESC
         LIMIT 1
        """,
        (person_id,),
    ).fetchone()
    return dict(row) if row else None


def _best_channel(con: sqlite3.Connection, person: dict[str, Any], *, mode: str) -> str:
    if mode == "audience":
        if person.get("linkedin_url"):
            return "linkedin"
        if person.get("twitter_handle"):
            return "x"
    if has_send_enabled_account(con, person_id=str(person["id"]), channel="telegram"):
        return "telegram"
    return choose_channel(person)


def _action_type(person: dict[str, Any], *, latest: dict[str, Any] | None, mode: str) -> str:
    if latest and latest.get("direction") == "incoming":
        return "warm_reply"
    if mode == "audience" and (person.get("linkedin_url") or person.get("twitter_handle")):
        public_days = int(person.get("public_recency_days") or 365)
        return "comment_opportunity" if public_days <= 90 else "follow_up"
    stale_days = int(person.get("staleness_days") or 365)
    if stale_days >= 120:
        return "reactivation"
    if stale_days >= 30:
        return "follow_up"
    return "do_nothing"


def _weak_context(person: dict[str, Any], gbrain_results: list[GBrainResult]) -> bool:
    has_channel = bool(person.get("primary_email") or person.get("linkedin_url") or person.get("telegram_handle") or person.get("twitter_handle"))
    has_signal = bool(person.get("resources") or person.get("connection_values") or gbrain_results)
    return not (has_channel and has_signal)


def _channel_bonus(con: sqlite3.Connection, person: dict[str, Any], *, channel: str) -> int:
    if channel == "telegram" and has_send_enabled_account(con, person_id=str(person["id"]), channel="telegram"):
        return 16
    if channel == "gmail" and person.get("primary_email"):
        return 12
    if channel == "linkedin" and person.get("linkedin_url"):
        return 10
    if channel == "x" and person.get("twitter_handle"):
        return 8
    return 0


def _gbrain_query(person: dict[str, Any]) -> str:
    parts = [
        str(person.get("full_name") or ""),
        str(person.get("organizations") or ""),
        str(person.get("titles") or ""),
    ]
    return " ".join(part for part in parts if part).strip()


def _public_post_action(con: sqlite3.Connection) -> NextAction | None:
    draft = con.execute(
        """
        SELECT id, subject, rationale, created_at
          FROM drafts
         WHERE channel = 'linkedin_post'
           AND status = 'draft'
         ORDER BY created_at DESC
         LIMIT 1
        """
    ).fetchone()
    if not draft:
        goal = con.execute("SELECT title FROM goals WHERE status = 'active' ORDER BY created_at DESC LIMIT 1").fetchone()
        if not goal:
            return None
        return NextAction(
            person_id=None,
            person_name="Public audience",
            action_type="post",
            channel="linkedin",
            score=65,
            rationale=f"create one high-signal public post tied to active goal: {goal['title']}",
            weak_context=False,
            goal_title=str(goal["title"]),
        )
    return NextAction(
        person_id=None,
        person_name="Public audience",
        action_type="post",
        channel="linkedin",
        score=80,
        rationale=f"review and publish prepared LinkedIn draft {draft['id']}: {draft['subject'] or draft['rationale'] or 'daily post'}",
        weak_context=False,
    )


def summarize_action_counts(actions: list[NextAction]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for action in actions:
        counts[action.action_type] = counts.get(action.action_type, 0) + 1
    return counts

