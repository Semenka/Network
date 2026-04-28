from __future__ import annotations

import sqlite3
from typing import Any

from .db import add_connection_value, rows_to_dicts
from .scoring import infer_connection_values_from_text


def _person_signal_rows(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT
            people.id,
            people.full_name,
            people.notes,
            people.location,
            group_concat(DISTINCT roles.title) AS titles,
            group_concat(DISTINCT organizations.name) AS organizations,
            group_concat(DISTINCT resources.resource_type || ': ' || resources.description) AS resources,
            group_concat(DISTINCT source_facts.fact_value) AS source_facts,
            group_concat(DISTINCT interactions.subject || ' ' || COALESCE(interactions.body_summary, '')) AS interactions
          FROM people
          LEFT JOIN roles ON roles.person_id = people.id
          LEFT JOIN organizations ON organizations.id = roles.organization_id
          LEFT JOIN resources ON resources.person_id = people.id
          LEFT JOIN source_facts ON source_facts.person_id = people.id
          LEFT JOIN interactions ON interactions.person_id = people.id
         GROUP BY people.id
        """
    ).fetchall()
    return rows_to_dicts(rows)


def maintain_connection_values(con: sqlite3.Connection, *, limit: int | None = None) -> dict[str, int]:
    people = _person_signal_rows(con)
    if limit:
        people = people[:limit]

    values_seen = 0
    for person in people:
        text = " ".join(
            str(person.get(key) or "")
            for key in (
                "full_name",
                "notes",
                "location",
                "titles",
                "organizations",
                "resources",
                "source_facts",
                "interactions",
            )
        )
        for value_type, description, score in infer_connection_values_from_text(text):
            add_connection_value(
                con,
                person_id=person["id"],
                value_type=value_type,
                description=description,
                score=score,
                evidence=text[:500],
                source="value_maintenance",
                confidence=0.45,
            )
            values_seen += 1

    return {"people_scanned": len(people), "values_seen": values_seen}
