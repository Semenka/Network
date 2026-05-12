"""Quarantine and delete misclassified person rows.

Importers occasionally land company names or email addresses in the
``full_name`` column when source data is inconsistent. This module
identifies suspect rows and (with explicit ``--delete``) removes them.
The ``people`` foreign keys cascade so dependent rows in
``relationships``, ``roles``, ``connection_values``, ``interactions``,
``source_facts``, and ``resources`` are cleaned up automatically.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any


_EMAIL_LIKE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_DOMAIN_LIKE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9\-_.]+\.(com|io|net|org|ai|co|app|dev)$", re.I)


def _classify_row(row: dict[str, Any], org_names: set[str]) -> str | None:
    """Return a reason string if the row looks suspicious, else None.

    ``email-as-name`` and ``domain-as-name`` fire unconditionally — those
    strings are never legitimate person names. ``organization-as-name``
    requires the row to have no other identity (email/linkedin/twitter)
    so we don't accidentally delete real people who happen to share a
    name with one of their employers.
    """
    full_name = (row.get("full_name") or "").strip()
    linkedin = (row.get("linkedin_url") or "").strip().lower()
    if "linkedin.com/company/" in linkedin or "linkedin.com/school/" in linkedin:
        return "company-linkedin-url"
    if not full_name:
        return None
    if _EMAIL_LIKE.search(full_name):
        return "email-as-name"
    lowered = full_name.lower()
    if _DOMAIN_LIKE.search(lowered):
        return "domain-as-name"
    if lowered in org_names and not (
        (row.get("primary_email") or "").strip()
        or linkedin
        or (row.get("twitter_handle") or "").strip()
    ):
        return "organization-as-name"
    return None


def find_misclassified(con: sqlite3.Connection) -> list[dict[str, Any]]:
    org_names = {
        (row["name"] or "").strip().lower()
        for row in con.execute("SELECT name FROM organizations").fetchall()
    }
    org_names.discard("")
    rows = con.execute(
        """
        SELECT id, full_name, primary_email, linkedin_url, twitter_handle, confidence
          FROM people
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        reason = _classify_row(record, org_names)
        if reason is None:
            continue
        record["reason"] = reason
        out.append(record)
    return out


def delete_people(con: sqlite3.Connection, person_ids: list[str]) -> int:
    if not person_ids:
        return 0
    placeholders = ",".join(["?"] * len(person_ids))
    cur = con.execute(f"DELETE FROM people WHERE id IN ({placeholders})", tuple(person_ids))
    con.commit()
    return cur.rowcount
