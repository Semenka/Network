from __future__ import annotations

import json
import mailbox
import sqlite3
from datetime import UTC, datetime, timedelta
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path
from typing import Any

from ..db import add_connection_value, add_interaction, add_source_fact, upsert_channel_account, upsert_person
from ..scoring import infer_connection_values_from_text


def _parse_date(value: str | None) -> str | None:
    if not value:
        return None
    value = str(value).strip()
    if value.isdigit() and len(value) >= 12:
        parsed = datetime.fromtimestamp(int(value) / 1000, tz=UTC)
        return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")
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
                return _flatten_message_records(list(data[key]))
        return [data]
    if isinstance(data, list):
        return _flatten_message_records(data)
    raise ValueError("Gmail JSON must be a list or an object containing messages/emails/items/results.")


def _flatten_message_records(records: list[Any]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        messages = record.get("messages")
        if isinstance(messages, list):
            thread_id = record.get("threadId") or record.get("thread_id") or record.get("id")
            for message in messages:
                if isinstance(message, dict):
                    merged = dict(message)
                    if thread_id and not (merged.get("threadId") or merged.get("thread_id")):
                        merged["threadId"] = thread_id
                    flattened.append(merged)
        else:
            flattened.append(record)
    return flattened


def _header(record: dict[str, Any], name: str) -> str | None:
    wanted = name.lower()
    headers = record.get("headers")
    payload = record.get("payload")
    if not isinstance(headers, list) and isinstance(payload, dict):
        headers = payload.get("headers")
    if not isinstance(headers, list):
        return None
    for header in headers:
        if not isinstance(header, dict):
            continue
        header_name = str(header.get("name") or "").lower()
        if header_name == wanted:
            value = header.get("value")
            return str(value) if value is not None else None
    return None


def _record_addresses(record: dict[str, Any], key: str) -> list[tuple[str, str]]:
    value = record.get(key) or record.get(key.capitalize()) or record.get(key.upper()) or _header(record, key)
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


def _record_value(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
        lower = key.lower()
        if lower in record and record[lower] not in (None, ""):
            return record[lower]
        header = _header(record, key)
        if header:
            return header
    return None


def _record_labels(record: dict[str, Any]) -> set[str]:
    labels = record.get("labels") or record.get("labelIds") or record.get("label_ids") or []
    if isinstance(labels, str):
        labels = [labels]
    if not isinstance(labels, list):
        return set()
    return {str(label).strip().lower().replace(" ", "_") for label in labels if str(label).strip()}


def _cutoff_for_months(months: int | None) -> datetime | None:
    if not months:
        return None
    return datetime.now(UTC) - timedelta(days=max(1, int(months)) * 31)


def _is_before_cutoff(value: str | None, cutoff: datetime | None) -> bool:
    if not value or cutoff is None:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC) < cutoff


def import_gmail_json(
    con: sqlite3.Connection,
    path: str | Path,
    *,
    mailbox_owner: str | None = None,
    limit: int | None = None,
    since_months: int | None = None,
    exclude_default_labels: bool = False,
) -> dict[str, int]:
    owner = mailbox_owner.lower() if mailbox_owner else None
    cutoff = _cutoff_for_months(since_months)
    seen_people = interactions = values = accounts = 0
    records = _message_records_from_json(path)
    if limit:
        records = records[:limit]
    for record in records:
        if exclude_default_labels and _record_labels(record) & {"spam", "trash", "category_promotions", "promotions"}:
            continue
        senders = _record_addresses(record, "from") or _record_addresses(record, "sender")
        recipients = _record_addresses(record, "to") + _record_addresses(record, "cc")
        all_people = senders + recipients
        if not all_people:
            continue
        sender_email = senders[0][1] if senders else None
        direction = "outgoing" if owner and sender_email == owner else "incoming"
        subject = _record_value(record, "subject")
        snippet = _record_value(record, "snippet", "body", "body_summary", "text", "textPlain")
        date = _parse_date(str(_record_value(record, "date", "timestamp", "internalDate") or ""))
        if _is_before_cutoff(date, cutoff):
            continue
        message_id = _record_value(record, "id", "message_id", "messageId", "Message-ID")
        thread_id = _record_value(record, "threadId", "thread_id", "thread")
        source_ref = str(message_id or thread_id or path)

        for name, email in all_people:
            if owner and email == owner:
                continue
            person_id = upsert_person(con, full_name=name, email=email, confidence=0.65)
            seen_people += 1
            upsert_channel_account(
                con,
                person_id=person_id,
                channel="gmail",
                account_ref=email,
                display_name=name,
                send_enabled=True,
                source="gmail_json",
                confidence=0.7,
                last_verified_at=date,
            )
            accounts += 1
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
            if thread_id:
                add_source_fact(
                    con,
                    person_id=person_id,
                    fact_type="gmail_thread_id",
                    fact_value=str(thread_id),
                    source="gmail_json",
                    source_ref=source_ref,
                    confidence=0.65,
                )
            if message_id:
                add_source_fact(
                    con,
                    person_id=person_id,
                    fact_type="gmail_message_id",
                    fact_value=str(message_id),
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
    return {"people_seen": seen_people, "accounts_seen": accounts, "interactions_seen": interactions, "values_seen": values}


def import_gmail_mbox(
    con: sqlite3.Connection,
    path: str | Path,
    *,
    mailbox_owner: str | None = None,
    limit: int | None = None,
    since_months: int | None = None,
) -> dict[str, int]:
    owner = mailbox_owner.lower() if mailbox_owner else None
    cutoff = _cutoff_for_months(since_months)
    box = mailbox.mbox(path)
    seen_people = interactions = values = accounts = 0
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
        if _is_before_cutoff(date, cutoff):
            continue
        source_ref = message.get("message-id") or f"{path}:{index}"
        preview = _body_preview(message)
        for name, email in all_people:
            if owner and email == owner:
                continue
            person_id = upsert_person(con, full_name=name, email=email, confidence=0.65)
            seen_people += 1
            upsert_channel_account(
                con,
                person_id=person_id,
                channel="gmail",
                account_ref=email,
                display_name=name,
                send_enabled=True,
                source="gmail_mbox",
                confidence=0.7,
                last_verified_at=date,
            )
            accounts += 1
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
    return {"people_seen": seen_people, "accounts_seen": accounts, "interactions_seen": interactions, "values_seen": values}
