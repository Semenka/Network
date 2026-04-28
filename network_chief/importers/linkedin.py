from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

from ..db import add_connection_value, add_interaction, add_resource, add_role, add_source_fact, get_or_create_org, upsert_person
from ..scoring import infer_connection_values_from_text, infer_resources_from_text


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


def _pick(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if value is None:
            value = record.get(key.lower())
        if value is None:
            value = record.get(key.upper())
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _records(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key in ("messages", "interactions", "items", "results"):
                if isinstance(data.get(key), list):
                    return list(data[key])
            return [data]
        if isinstance(data, list):
            return list(data)
        raise ValueError("LinkedIn JSON must be an object, list, or contain messages/interactions/items/results.")

    with path.open(newline="", encoding="utf-8-sig") as handle:
        raw_rows = list(csv.reader(handle))
    header_index = 0
    expected = {"from", "sender", "to", "recipients", "date", "sent date", "content", "message", "body"}
    for index, row in enumerate(raw_rows):
        normalized = {cell.strip().lower() for cell in row}
        if normalized & expected:
            header_index = index
            break
    header = raw_rows[header_index]
    return [dict(zip(header, row, strict=False)) for row in raw_rows[header_index + 1 :] if any(cell.strip() for cell in row)]


def import_connections(con: sqlite3.Connection, path: str | Path) -> dict[str, int]:
    people = roles = interactions = resources = values = 0
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
        for value_type, description, score in infer_connection_values_from_text(f"{position or ''} {company or ''}"):
            add_connection_value(
                con,
                person_id=person_id,
                value_type=value_type,
                description=description,
                score=score,
                evidence=f"{position or ''} {company or ''}".strip(),
                source="linkedin_export",
                source_ref=str(path),
                confidence=0.55,
            )
            values += 1
    return {
        "people_seen": people,
        "roles_seen": roles,
        "interactions_seen": interactions,
        "resources_seen": resources,
        "values_seen": values,
    }


def import_linkedin_interactions(
    con: sqlite3.Connection,
    path: str | Path,
    *,
    owner_name: str | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    owner = owner_name.lower() if owner_name else None
    people = interactions = values = 0
    records = _records(path)
    if limit:
        records = records[:limit]

    for index, record in enumerate(records):
        sender = _pick(record, "From", "Sender", "from", "sender")
        recipients = _pick(record, "To", "Recipients", "to", "recipients")
        participant = _pick(record, "Participant", "Name", "Connection", "profile_name")
        url = _pick(record, "Profile URL", "URL", "LinkedIn Profile", "profile_url")
        date = _pick(record, "Date", "Sent Date", "Created At", "timestamp", "created_at")
        subject = _pick(record, "Subject", "Conversation Title", "Thread", "topic")
        content = _pick(record, "Content", "Message", "Text", "Body", "body", "snippet")
        source_ref = _pick(record, "id", "message_id", "Message ID") or f"{path}:{index}"

        counterpart = participant or sender or recipients
        direction = None
        if owner and sender:
            direction = "outgoing" if owner in sender.lower() else "incoming"
            if direction == "outgoing" and recipients:
                counterpart = recipients.split(";")[0].split(",")[0].strip()
        if not counterpart:
            continue

        person_id = upsert_person(
            con,
            full_name=counterpart,
            linkedin_url=url,
            confidence=0.6,
        )
        people += 1
        add_interaction(
            con,
            person_id=person_id,
            channel="linkedin",
            direction=direction,
            subject=subject or "LinkedIn interaction",
            body_summary=content[:500] if content else None,
            occurred_at=date,
            source="linkedin_interactions",
            source_ref=str(source_ref),
        )
        interactions += 1
        add_source_fact(
            con,
            person_id=person_id,
            fact_type="linkedin_interaction",
            fact_value=" ".join(part for part in (subject, content) if part)[:500],
            source="linkedin_interactions",
            source_ref=str(source_ref),
            confidence=0.6,
        )
        for value_type, description, score in infer_connection_values_from_text(" ".join(part for part in (subject, content) if part)):
            add_connection_value(
                con,
                person_id=person_id,
                value_type=value_type,
                description=description,
                score=score,
                evidence=content[:500] if content else subject,
                source="linkedin_interactions",
                source_ref=str(source_ref),
                confidence=0.4,
            )
            values += 1

    return {"people_seen": people, "interactions_seen": interactions, "values_seen": values}
