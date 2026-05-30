"""Mermaid renderer for the network graph.

GitHub renders ```mermaid``` fences natively in markdown, so the graph
shows up inline anywhere we push a dashboard or graph file. Designed
to stay readable up to ~50 nodes; above that the graph is truncated
to the highest-value contacts.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any


VALUE_PILLARS = ("financial_capital", "competence", "specific_knowledge", "time_saving")


def _safe_id(prefix: str, raw: str) -> str:
    """Mermaid node ids must be alphanumeric. Use a prefix + sanitised slug."""
    slug = re.sub(r"[^a-zA-Z0-9]", "", raw)[:16]
    if not slug:
        slug = "x"
    return f"{prefix}_{slug}"


def _escape_label(text: str) -> str:
    """Escape Mermaid label text — wrap in quotes and escape internal quotes."""
    cleaned = (text or "").strip().replace("\n", " ").replace('"', '\\"')
    if len(cleaned) > 40:
        cleaned = cleaned[:37] + "..."
    return cleaned


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _classify(person: dict[str, Any]) -> str:
    """Return a class name for the person based on recency × value."""
    last = _parse_iso(person.get("last_interaction_at"))
    score = int(person.get("max_value_score") or 0)
    if last and (_now() - last) <= timedelta(days=30):
        return "recent"
    if last and (_now() - last) <= timedelta(days=90):
        return "warm"
    if score >= 60:
        return "redalert"
    return "stale"


def _top_people(con: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT
            p.id,
            p.full_name,
            rel.last_interaction_at,
            COALESCE(MAX(cv.score), 0) AS max_value_score,
            (
                SELECT cv2.value_type
                  FROM connection_values cv2
                 WHERE cv2.person_id = p.id
                 ORDER BY cv2.score DESC
                 LIMIT 1
            ) AS primary_value_type
        FROM people p
        LEFT JOIN relationships rel ON rel.person_id = p.id
        LEFT JOIN connection_values cv ON cv.person_id = p.id
        GROUP BY p.id
        ORDER BY max_value_score DESC,
                 CASE WHEN rel.last_interaction_at IS NULL THEN 1 ELSE 0 END,
                 rel.last_interaction_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def _person_orgs(con: sqlite3.Connection, person_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not person_ids:
        return {}
    placeholders = ",".join(["?"] * len(person_ids))
    rows = con.execute(
        f"""
        SELECT r.person_id, r.title, o.id AS org_id, o.name AS org_name
          FROM roles r
          LEFT JOIN organizations o ON o.id = r.organization_id
         WHERE r.person_id IN ({placeholders})
           AND (r.ended_on IS NULL OR r.ended_on = '')
        """,
        tuple(person_ids),
    ).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(row["person_id"], []).append(dict(row))
    return out


def render_mermaid(con: sqlite3.Connection, *, limit: int = 40) -> str:
    """Build the Mermaid graph code (without code fences)."""
    people = _top_people(con, limit=limit)
    if not people:
        return "graph LR\n  empty[No contacts yet]"

    orgs_by_person = _person_orgs(con, [p["id"] for p in people])
    lines: list[str] = ["graph LR"]

    classified: list[tuple[dict[str, Any], str]] = [(p, _classify(p)) for p in people]
    by_pillar: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for person, klass in classified:
        pillar = person.get("primary_value_type") or "no_value_signal"
        by_pillar.setdefault(pillar, []).append((person, klass))

    person_node_ids: dict[str, str] = {}
    org_node_ids: dict[str, str] = {}

    pillar_order = list(VALUE_PILLARS) + sorted(set(by_pillar) - set(VALUE_PILLARS))
    for pillar in pillar_order:
        bucket = by_pillar.get(pillar)
        if not bucket:
            continue
        lines.append(f"  subgraph {pillar}")
        for person, _klass in bucket:
            node_id = _safe_id("P", person["id"])
            person_node_ids[person["id"]] = node_id
            label = _escape_label(person["full_name"] or "Unknown")
            lines.append(f'    {node_id}["{label}"]')
        lines.append("  end")

    for person, _klass in classified:
        pid = person["id"]
        for role in orgs_by_person.get(pid, []):
            org_id = role.get("org_id")
            org_name = role.get("org_name")
            if not org_id or not org_name:
                continue
            if org_id not in org_node_ids:
                org_node_ids[org_id] = _safe_id("O", org_id)
                lines.append(f'  {org_node_ids[org_id]}(("{_escape_label(org_name)}"))')
            title_label = _escape_label(role.get("title") or "role")
            lines.append(
                f'  {person_node_ids[pid]} -->|"{title_label}"| {org_node_ids[org_id]}'
            )

    lines.extend(
        [
            "  classDef recent fill:#d1f4d1,stroke:#2a8a2a,color:#0b0b0b;",
            "  classDef warm fill:#fff7c2,stroke:#a07c0a,color:#0b0b0b;",
            "  classDef stale fill:#e6e6e6,stroke:#666,color:#0b0b0b;",
            "  classDef redalert fill:#ffd1d1,stroke:#b32a2a,color:#0b0b0b,stroke-width:2px;",
        ]
    )

    by_class: dict[str, list[str]] = {}
    for person, klass in classified:
        by_class.setdefault(klass, []).append(person_node_ids[person["id"]])
    for klass, ids in by_class.items():
        lines.append(f"  class {','.join(ids)} {klass};")

    return "\n".join(lines)


def render_graph_markdown(con: sqlite3.Connection, *, limit: int = 40) -> str:
    """Return a fully-formed markdown section with the Mermaid graph."""
    diagram = render_mermaid(con, limit=limit)
    lines = [
        f"## Network graph (top {limit} by value-score)",
        "",
        "Legend: 🟢 touched in 30d • 🟡 touched in 31–90d • ⚪ stale (>90d, low value) • 🔴 stale-but-valuable (≥60 value-score, >90d).",
        "",
        "```mermaid",
        diagram,
        "```",
        "",
    ]
    return "\n".join(lines)
