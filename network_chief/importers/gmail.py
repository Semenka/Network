from __future__ import annotations

import json
import mailbox
import sqlite3
from datetime import UTC, datetime
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path
from typing import Any

from ..db import add_connection_value, add_interaction, add_source_fact, upsert_person
from ..scoring import infer_connection_values_from_text


def _parse_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _addresses(value: str | None) -> list[tuple[str, str]]:
    if not value:
        return []
    parsed = []
    for name, address in getaddresses([value]):
        address = address.strip().lower()
        if not address:
            continue
        parsed.append((name.strip() or address.split("@")[0], address))
    return parsed


def _body_preview(message: Message, limit: int = 500) -> str | None:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                try:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    text = payload.decode(charset, errors="replace") if payload else ""
                    return " ".join(text.split())[:limit]
                except Exception:
                    continue
        return None
    try:
        payload = message.get_payload(decode=True)
        charset = message.get_content_charset() or "utf-8"
        text = payload.decode(charset, errors="replace") if payload else str(message.get_payload())
        return " ".join(text.split())[:limit]
    except Exception:
        return None


def _message_records_from_json(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        for key in ("messages", "emails", "items", "results"):
            if isinstance(data.get(key), list):
                return list(data[key])
        return [data]
    if isinstance(data, list):
        return data
    raise ValueError("Gmail JSON must be a list or an object containing messages/emails/items/results.")


def _record_addresses(record: dict[str, Any], key: str) -> list[tuple[str, str]]:
    value = record.get(key) or record.get(key.capitalize()) or record.get(key.upper())
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                name = item.get("name") or item.get("displayName") or item.get("email")
                email = item.get("email") or item.get("address")
                if email:
                    parts.append((str(name or email), str(email).lower()))
            else:
                parts.extend(_addresses(str(item)))
        return parts
    return _addresses(str(value)) if value else []


def import_gmail_json(
    con: sqlite3.Connection,
    path: str | Path,
    *,
    mailbox_owner: str | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    owner = mailbox_owner.lower() if mailbox_owner else None
    seen_people = interactions = values = 0
    records = _message_records_from_json(path)
    if limit:
        records = records[:limit]
    for record in records:
        senders = _record_addresses(record, "from") or _record_addresses(record, "sender")
        recipients = _record_addresses(record, "to") + _record_addresses(record, "cc")
        all_people = senders + recipients
        if not all_people:
            continue
        sender_email = senders[0][1] if senders else None
        direction = "outgoing" if owner and sender_email == owner else "incoming"
        subject = record.get("subject") or record.get("Subject")
        snippet = record.get("snippet") or record.get("body") or record.get("body_summary")
        date = _parse_date(str(record.get("date") or record.get("timestamp") or record.get("internalDate") or ""))
        source_ref = str(record.get("id") or record.get("message_id") or path)

        for name, email in all_people:
            if owner and email == owner:
                continue
            person_id = upsert_person(con, full_name=name, email=email, confidence=0.65)
            seen_people += 1
            add_interaction(
                con,
                person_id=person_id,
                channel="gmail",
                direction=direction,
                subject=str(subject) if subject else None,
                body_summary=str(snippet)[:500] if snippet else None,
                occurred_at=date,
                source="gmail_json",
                source_ref=source_ref,
            )
            add_source_fact(
                con,
                person_id=person_id,
                fact_type="gmail_contact",
                fact_value=email,
                source="gmail_json",
                source_ref=source_ref,
                confidence=0.65,
            )
            for value_type, description, score in infer_connection_values_from_text(" ".join(str(part or "") for part in (subject, snippet))):
                add_connection_value(
                    con,
                    person_id=person_id,
                    value_type=value_type,
                    description=description,
                    score=score,
                    evidence=str(snippet)[:500] if snippet else str(subject or ""),
                    source="gmail_json",
                    source_ref=source_ref,
                    confidence=0.35,
                )
                values += 1
            interactions += 1
    return {"people_seen": seen_people, "interactions_seen": interactions, "values_seen": values}


def import_gmail_mbox(
    con: sqlite3.Connection,
    path: str | Path,
    *,
    mailbox_owner: str | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    owner = mailbox_owner.lower() if mailbox_owner else None
    box = mailbox.mbox(path)
    seen_people = interactions = values = 0
    for index, message in enumerate(box):
        if limit and index >= limit:
            break
        senders = _addresses(message.get("from"))
        recipients = _addresses(message.get("to")) + _addresses(message.get("cc"))
        all_people = senders + recipients
        if not all_people:
            continue
        sender_email = senders[0][1] if senders else None
        direction = "outgoing" if owner and sender_email == owner else "incoming"
        subject = message.get("subject")
        date = _parse_date(message.get("date"))
        source_ref = message.get("message-id") or f"{path}:{index}"
        preview = _body_preview(message)
        for name, email in all_people:
            if owner and email == owner:
                continue
            person_id = upsert_person(con, full_name=name, email=email, confidence=0.65)
            seen_people += 1
            add_interaction(
                con,
                person_id=person_id,
                channel="gmail",
                direction=direction,
                subject=subject,
                body_summary=preview,
                occurred_at=date,
                source="gmail_mbox",
                source_ref=str(source_ref),
            )
            add_source_fact(
                con,
                person_id=person_id,
                fact_type="gmail_contact",
                fact_value=email,
                source="gmail_mbox",
                source_ref=str(source_ref),
                confidence=0.65,
            )
            for value_type, description, score in infer_connection_values_from_text(" ".join(str(part or "") for part in (subject, preview))):
                add_connection_value(
                    con,
                    person_id=person_id,
                    value_type=value_type,
                    description=description,
                    score=score,
                    evidence=preview[:500] if preview else str(subject or ""),
                    source="gmail_mbox",
                    source_ref=str(source_ref),
                    confidence=0.35,
                )
                values += 1
            interactions += 1
    return {"people_seen": seen_people, "interactions_seen": interactions, "values_seen": values}
