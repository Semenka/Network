from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from ..db import add_interaction, add_resource, add_role, add_source_fact, get_or_create_org, upsert_person
from ..scoring import infer_resources_from_text


def _linkedin_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        raw_rows = list(csv.reader(handle))
    header_index = 0
    for index, row in enumerate(raw_rows):
        normalized = {cell.strip().lower() for cell in row}
        if "first name" in normalized and "last name" in normalized:
            header_index = index
            break
    header = raw_rows[header_index]
    data_rows = raw_rows[header_index + 1 :]
    return [dict(zip(header, row, strict=False)) for row in data_rows if any(cell.strip() for cell in row)]


def import_connections(con: sqlite3.Connection, path: str | Path) -> dict[str, int]:
    people = roles = interactions = resources = 0
    for row in _linkedin_rows(path):
        first_name = row.get("First Name", "").strip()
        last_name = row.get("Last Name", "").strip()
        full_name = " ".join(part for part in (first_name, last_name) if part).strip()
        if not full_name:
            continue
        email = row.get("Email Address") or row.get("Email")
        company = row.get("Company")
        position = row.get("Position")
        url = row.get("URL") or row.get("Profile URL")
        connected_on = row.get("Connected On")

        person_id = upsert_person(
            con,
            full_name=full_name,
            email=email,
            linkedin_url=url,
            confidence=0.75,
        )
        people += 1
        org_id = get_or_create_org(con, company)
        if org_id or position:
            add_role(
                con,
                person_id=person_id,
                organization_id=org_id,
                title=position,
                source="linkedin_export",
                source_ref=str(path),
                confidence=0.75,
            )
            roles += 1
        if connected_on:
            add_interaction(
                con,
                person_id=person_id,
                channel="linkedin",
                direction="connected",
                subject="LinkedIn connection",
                occurred_at=connected_on,
                source="linkedin_export",
                source_ref=str(path),
            )
            interactions += 1
        add_source_fact(
            con,
            person_id=person_id,
            fact_type="linkedin_connection",
            fact_value=f"{full_name} | {position or ''} | {company or ''}",
            source="linkedin_export",
            source_ref=str(path),
            confidence=0.75,
        )
        for resource_type, description in infer_resources_from_text(f"{position or ''} {company or ''}"):
            add_resource(
                con,
                person_id=person_id,
                resource_type=resource_type,
                description=description,
                source="linkedin_export",
                confidence=0.55,
            )
            resources += 1
    return {"people_seen": people, "roles_seen": roles, "interactions_seen": interactions, "resources_seen": resources}
