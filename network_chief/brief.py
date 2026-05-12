from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from .drafts import choose_channel, create_draft
from .scoring import rank_people


def _public_channel(person: dict[str, Any]) -> str:
    if person.get("twitter_handle"):
        return "x"
    if person.get("linkedin_url"):
        return "linkedin"
    return choose_channel(person)


def build_daily_brief(
    con: sqlite3.Connection,
    limit: int = 10,
    create_draft_records: bool = True,
    *,
    mode: str = "relationship",
) -> str:
    ranked = rank_people(con, limit=limit, mode=mode)
    today = datetime.now(UTC).date().isoformat()
    title = "Network Chief Audience Brief" if mode == "audience" else "Network Chief Daily Brief"
    section = "Audience-Growth Targets" if mode == "audience" else "Suggested Interactions"
    lines = [
        f"# {title} - {today}",
        "",
        f"## {section}",
        "",
    ]
    if not ranked:
        lines.extend(
            [
                "No people are available yet.",
                "",
                "Import LinkedIn connections, Gmail exports, X exports, or add people manually.",
            ]
        )
        return "\n".join(lines)

    for index, person in enumerate(ranked, start=1):
        goal = person.get("goal")
        draft_id = create_draft(con, person=person, goal=goal) if create_draft_records else None
        channel_label = _public_channel(person) if mode == "audience" else choose_channel(person)
        lines.extend(
            [
                f"### {index}. {person['full_name']}",
                "",
                f"- Score: {person['score']}",
                f"- Channel: {channel_label}",
                f"- Rationale: {person['rationale']}",
                f"- Organization: {person.get('organizations') or 'unknown'}",
                f"- Resources: {person.get('resources') or 'not mapped yet'}",
                f"- Connection value: {person.get('connection_values') or 'not mapped yet'}",
            ]
        )
        if mode == "audience":
            lines.extend(
                [
                    f"- Conversation potential: {person.get('conversation_potential', 0)}",
                    f"- Public recency: {person.get('public_recency_days', 'unknown')} days",
                ]
            )
        if goal:
            lines.append(f"- Goal: {goal['title']}")
        if draft_id:
            lines.append(f"- Draft: {draft_id}")
        lines.append("")
    return "\n".join(lines)


def export_mindmap(con: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    for row in con.execute("SELECT id, full_name, primary_email, location FROM people"):
        nodes.append({"id": row["id"], "label": row["full_name"], "type": "person", "email": row["primary_email"], "location": row["location"]})

    for row in con.execute("SELECT id, name, sector FROM organizations"):
        nodes.append({"id": row["id"], "label": row["name"], "type": "organization", "sector": row["sector"]})

    for row in con.execute(
        """
        SELECT roles.person_id, roles.organization_id, roles.title
          FROM roles
         WHERE roles.organization_id IS NOT NULL
        """
    ):
        edges.append({"source": row["person_id"], "target": row["organization_id"], "type": "role", "label": row["title"]})

    for row in con.execute("SELECT id, person_id, resource_type, description FROM resources"):
        resource_node_id = f"resource:{row['id']}"
        nodes.append({"id": resource_node_id, "label": row["resource_type"], "type": "resource", "description": row["description"]})
        edges.append({"source": row["person_id"], "target": resource_node_id, "type": "has_resource"})

    for row in con.execute("SELECT id, person_id, value_type, description, score FROM connection_values"):
        value_node_id = f"value:{row['id']}"
        nodes.append(
            {
                "id": value_node_id,
                "label": row["value_type"],
                "type": "connection_value",
                "description": row["description"],
                "score": row["score"],
            }
        )
        edges.append({"source": row["person_id"], "target": value_node_id, "type": "has_connection_value"})

    for row in con.execute("SELECT id, person_id, channel, account_ref, send_enabled FROM channel_accounts"):
        account_node_id = f"channel:{row['id']}"
        nodes.append(
            {
                "id": account_node_id,
                "label": f"{row['channel']}: {row['account_ref']}",
                "type": "channel_account",
                "channel": row["channel"],
                "send_enabled": bool(row["send_enabled"]),
            }
        )
        edges.append({"source": row["person_id"], "target": account_node_id, "type": "has_channel_account"})

    return {"nodes": nodes, "edges": edges}


def mindmap_json(con: sqlite3.Connection) -> str:
    return json.dumps(export_mindmap(con), indent=2, sort_keys=True)
