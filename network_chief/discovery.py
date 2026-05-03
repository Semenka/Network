"""Discover Telegram handles in already-imported data.

There is no public API to map "email/phone → Telegram username." The
realistic path is to extract handles already mentioned in fields we
have: ``people.notes``, ``source_facts.fact_value``, and
``interactions.body_summary`` / ``subject``. Two patterns:

* High-confidence: ``t.me/<handle>``, ``telegram.me/<handle>``,
  ``tg://resolve?domain=<handle>``.
* Medium-confidence: handle preceded by ``Telegram:`` / ``TG:``.

Handles must be 5-32 chars, start with a letter, contain only letters,
digits, and underscores. Updates ``people.telegram_handle`` only when
currently empty.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any


_HIGH_CONFIDENCE = re.compile(
    r"(?:t|telegram)\.me/(?!joinchat/|\+)([a-zA-Z][a-zA-Z0-9_]{4,31})"
    r"|tg://resolve\?domain=([a-zA-Z][a-zA-Z0-9_]{4,31})",
    re.IGNORECASE,
)
_LABELED = re.compile(
    r"(?:^|[^a-zA-Z])(?:tg|telegram|tlg|телеграм|телеграмм)[\s:]+@?([a-zA-Z][a-zA-Z0-9_]{4,31})\b",
    re.IGNORECASE,
)
_BLOCKLIST = {
    # Common words that match handle-shape but are clearly not handles.
    "joinchat", "addstickers", "share", "iv", "channel",
}


def _extract(text: str | None) -> set[str]:
    if not text:
        return set()
    found: set[str] = set()
    for match in _HIGH_CONFIDENCE.finditer(text):
        handle = (match.group(1) or match.group(2) or "").lower()
        if handle and handle not in _BLOCKLIST:
            found.add(handle)
    for match in _LABELED.finditer(text):
        handle = match.group(1).lower()
        if handle not in _BLOCKLIST:
            found.add(handle)
    return found


def _gather_text_per_person(con: sqlite3.Connection) -> dict[str, list[str]]:
    rows = con.execute(
        """
        SELECT id, full_name, COALESCE(notes, '') AS notes,
               COALESCE(linkedin_url, '') AS linkedin_url,
               COALESCE(twitter_handle, '') AS twitter_handle,
               COALESCE(telegram_handle, '') AS telegram_handle
          FROM people
        """
    ).fetchall()
    text_by_pid: dict[str, list[str]] = {}
    for row in rows:
        text_by_pid[row["id"]] = [row["notes"]]
    sf_rows = con.execute(
        """
        SELECT person_id, COALESCE(fact_value, '') AS fact_value
          FROM source_facts
         WHERE person_id IS NOT NULL
        """
    ).fetchall()
    for row in sf_rows:
        text_by_pid.setdefault(row["person_id"], []).append(row["fact_value"])
    intr_rows = con.execute(
        """
        SELECT person_id,
               COALESCE(subject, '') || ' ' || COALESCE(body_summary, '') AS text
          FROM interactions
        """
    ).fetchall()
    for row in intr_rows:
        text_by_pid.setdefault(row["person_id"], []).append(row["text"])
    return text_by_pid


def discover_telegram_handles(con: sqlite3.Connection) -> dict[str, Any]:
    """Scan all per-person text and update ``people.telegram_handle`` where empty.

    Returns ``{"scanned": N, "candidates": M, "updated": K, "skipped_existing": E,
    "skipped_collision": C}``.
    """
    existing = {
        (row[0] or "").lower()
        for row in con.execute(
            "SELECT telegram_handle FROM people WHERE telegram_handle IS NOT NULL AND telegram_handle != ''"
        ).fetchall()
    }
    text_by_pid = _gather_text_per_person(con)
    scanned = len(text_by_pid)

    pre_filled = {
        row["id"]: (row["telegram_handle"] or "").lower()
        for row in con.execute(
            "SELECT id, telegram_handle FROM people"
        ).fetchall()
    }

    candidates = updated = skipped_existing = skipped_collision = 0
    samples: list[dict[str, str]] = []
    for person_id, snippets in text_by_pid.items():
        handles: set[str] = set()
        for snippet in snippets:
            handles |= _extract(snippet)
        if not handles:
            continue
        candidates += 1
        if pre_filled.get(person_id):
            skipped_existing += 1
            continue
        chosen = next(iter(sorted(handles)))
        if chosen in existing:
            skipped_collision += 1
            continue
        existing.add(chosen)
        con.execute(
            "UPDATE people SET telegram_handle = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
            (chosen, person_id),
        )
        if len(samples) < 10:
            samples.append({"person_id": person_id, "handle": chosen})
        updated += 1
    con.commit()
    return {
        "scanned": scanned,
        "candidates": candidates,
        "updated": updated,
        "skipped_existing": skipped_existing,
        "skipped_collision": skipped_collision,
        "samples": samples,
    }


def _normalise_handle(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned = raw.strip().lower()
    if "://" in cleaned:
        cleaned = cleaned.split("://", 1)[1]
    cleaned = cleaned.lstrip("@")
    for prefix in ("t.me/", "telegram.me/"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    if cleaned.startswith("resolve?domain="):
        cleaned = cleaned[len("resolve?domain="):]
    cleaned = cleaned.split("?", 1)[0].split("/", 1)[0]
    return cleaned or None


def set_telegram_handle(
    con: sqlite3.Connection,
    *,
    handle: str,
    person_id: str | None = None,
    email: str | None = None,
    linkedin_url: str | None = None,
    full_name: str | None = None,
) -> dict[str, Any]:
    """Set ``people.telegram_handle`` by id, email, linkedin_url, or full_name.

    Returns ``{"matched": bool, "person_id": str | None, "handle": str | None}``.
    """
    cleaned = _normalise_handle(handle)
    if not cleaned:
        return {"matched": False, "person_id": None, "handle": None, "reason": "empty handle"}
    row = None
    if person_id:
        row = con.execute("SELECT id, full_name FROM people WHERE id = ?", (person_id,)).fetchone()
    elif email:
        row = con.execute(
            "SELECT id, full_name FROM people WHERE lower(primary_email) = lower(?)", (email,)
        ).fetchone()
    elif linkedin_url:
        row = con.execute(
            "SELECT id, full_name FROM people WHERE linkedin_url = ?", (linkedin_url,)
        ).fetchone()
    elif full_name:
        row = con.execute(
            "SELECT id, full_name FROM people WHERE lower(full_name) = lower(?) LIMIT 2",
            (full_name,),
        ).fetchone()
    if not row:
        return {"matched": False, "person_id": None, "handle": cleaned, "reason": "no person matched"}
    con.execute(
        "UPDATE people SET telegram_handle = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
        (cleaned, row["id"]),
    )
    con.commit()
    return {
        "matched": True,
        "person_id": row["id"],
        "full_name": row["full_name"],
        "handle": cleaned,
    }


def import_telegram_csv(
    con: sqlite3.Connection,
    path: str,
    *,
    lookup: str = "email",
) -> dict[str, Any]:
    """Bulk import handles from a CSV with at least two columns: ``<lookup>`` and ``handle``."""
    import csv as _csv

    if lookup not in {"email", "linkedin_url", "full_name"}:
        raise ValueError(f"unknown lookup column: {lookup!r}")
    matched = unmatched = 0
    misses: list[str] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = _csv.DictReader(fh)
        for row in reader:
            key = (row.get(lookup) or "").strip()
            handle = row.get("handle") or row.get("telegram") or row.get("telegram_handle")
            if not key or not handle:
                continue
            kwargs = {lookup: key, "handle": handle}
            result = set_telegram_handle(con, **kwargs)
            if result["matched"]:
                matched += 1
            else:
                unmatched += 1
                if len(misses) < 10:
                    misses.append(key)
    return {"matched": matched, "unmatched": unmatched, "sample_misses": misses, "lookup": lookup}
