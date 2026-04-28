from __future__ import annotations

import os
import sqlite3
import uuid
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


DEFAULT_DB_PATH = "data/network.db"


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id() -> str:
    return str(uuid.uuid4())


def db_path_from_env(path: str | None = None) -> str:
    return path or os.environ.get("NETWORK_CHIEF_DB", DEFAULT_DB_PATH)


def connect(path: str | None = None) -> sqlite3.Connection:
    db_path = db_path_from_env(path)
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS people (
            id TEXT PRIMARY KEY,
            full_name TEXT NOT NULL,
            primary_email TEXT,
            phone TEXT,
            linkedin_url TEXT,
            instagram_handle TEXT,
            twitter_handle TEXT,
            telegram_handle TEXT,
            whatsapp_phone TEXT,
            location TEXT,
            notes TEXT,
            confidence REAL NOT NULL DEFAULT 0.5,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_people_email
            ON people(lower(primary_email))
            WHERE primary_email IS NOT NULL AND primary_email != '';

        CREATE UNIQUE INDEX IF NOT EXISTS idx_people_linkedin
            ON people(linkedin_url)
            WHERE linkedin_url IS NOT NULL AND linkedin_url != '';

        CREATE UNIQUE INDEX IF NOT EXISTS idx_people_twitter
            ON people(lower(twitter_handle))
            WHERE twitter_handle IS NOT NULL AND twitter_handle != '';

        CREATE TABLE IF NOT EXISTS organizations (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            domain TEXT,
            sector TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS roles (
            id TEXT PRIMARY KEY,
            person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            organization_id TEXT REFERENCES organizations(id) ON DELETE SET NULL,
            title TEXT,
            started_on TEXT,
            ended_on TEXT,
            source TEXT,
            source_ref TEXT,
            confidence REAL NOT NULL DEFAULT 0.5,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS resources (
            id TEXT PRIMARY KEY,
            person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            resource_type TEXT NOT NULL,
            description TEXT NOT NULL,
            source TEXT,
            confidence REAL NOT NULL DEFAULT 0.5,
            updated_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_resources_unique
            ON resources(person_id, resource_type, description);

        CREATE TABLE IF NOT EXISTS connection_values (
            id TEXT PRIMARY KEY,
            person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            value_type TEXT NOT NULL,
            description TEXT NOT NULL,
            score INTEGER NOT NULL DEFAULT 50,
            evidence TEXT,
            source TEXT,
            source_ref TEXT,
            confidence REAL NOT NULL DEFAULT 0.5,
            updated_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_connection_values_unique
            ON connection_values(person_id, value_type, description);

        CREATE INDEX IF NOT EXISTS idx_connection_values_type_score
            ON connection_values(value_type, score);

        CREATE TABLE IF NOT EXISTS relationships (
            id TEXT PRIMARY KEY,
            person_id TEXT NOT NULL UNIQUE REFERENCES people(id) ON DELETE CASCADE,
            strength INTEGER NOT NULL DEFAULT 35,
            warmth INTEGER NOT NULL DEFAULT 35,
            trust INTEGER NOT NULL DEFAULT 35,
            how_known TEXT,
            last_interaction_at TEXT,
            next_touch_at TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS interactions (
            id TEXT PRIMARY KEY,
            person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            channel TEXT NOT NULL,
            direction TEXT,
            subject TEXT,
            body_summary TEXT,
            occurred_at TEXT,
            source TEXT,
            source_ref TEXT,
            sentiment TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_interactions_person_time
            ON interactions(person_id, occurred_at);

        CREATE TABLE IF NOT EXISTS goals (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            cadence TEXT NOT NULL,
            capital_type TEXT,
            target_segment TEXT,
            success_metric TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            starts_on TEXT,
            ends_on TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS drafts (
            id TEXT PRIMARY KEY,
            person_id TEXT REFERENCES people(id) ON DELETE SET NULL,
            goal_id TEXT REFERENCES goals(id) ON DELETE SET NULL,
            channel TEXT NOT NULL,
            subject TEXT,
            body TEXT NOT NULL,
            rationale TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_facts (
            id TEXT PRIMARY KEY,
            person_id TEXT REFERENCES people(id) ON DELETE CASCADE,
            fact_type TEXT NOT NULL,
            fact_value TEXT NOT NULL,
            source TEXT,
            source_ref TEXT,
            confidence REAL NOT NULL DEFAULT 0.5,
            observed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_runs (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_ref TEXT,
            status TEXT NOT NULL,
            stats_json TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL
        );
        """
    )
    con.commit()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return None if row is None else dict(row)


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _clean_handle(value: str | None) -> str | None:
    value = _clean(value)
    if not value:
        return None
    if value.startswith("https://x.com/") or value.startswith("https://twitter.com/"):
        value = value.rstrip("/").split("/")[-1]
    return value.lstrip("@").strip().lower() or None


def upsert_person(
    con: sqlite3.Connection,
    *,
    full_name: str,
    email: str | None = None,
    phone: str | None = None,
    linkedin_url: str | None = None,
    twitter_handle: str | None = None,
    location: str | None = None,
    notes: str | None = None,
    confidence: float = 0.5,
) -> str:
    full_name = _clean(full_name) or "Unknown"
    email = _clean(email.lower() if email else None)
    phone = _clean(phone)
    linkedin_url = _clean(linkedin_url)
    twitter_handle = _clean_handle(twitter_handle)
    location = _clean(location)
    notes = _clean(notes)

    row = None
    if email:
        row = con.execute(
            "SELECT * FROM people WHERE lower(primary_email) = lower(?)",
            (email,),
        ).fetchone()
    if row is None and linkedin_url:
        row = con.execute(
            "SELECT * FROM people WHERE linkedin_url = ?",
            (linkedin_url,),
        ).fetchone()
    if row is None and twitter_handle:
        row = con.execute(
            "SELECT * FROM people WHERE lower(twitter_handle) = lower(?)",
            (twitter_handle,),
        ).fetchone()
    if row is None:
        row = con.execute(
            "SELECT * FROM people WHERE lower(full_name) = lower(?) AND primary_email IS NULL",
            (full_name,),
        ).fetchone()

    ts = now_iso()
    if row is None:
        person_id = new_id()
        con.execute(
            """
            INSERT INTO people (
                id, full_name, primary_email, phone, linkedin_url, twitter_handle, location, notes,
                confidence, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (person_id, full_name, email, phone, linkedin_url, twitter_handle, location, notes, confidence, ts, ts),
        )
        con.execute(
            """
            INSERT INTO relationships (id, person_id, updated_at)
            VALUES (?, ?, ?)
            """,
            (new_id(), person_id, ts),
        )
        con.commit()
        return person_id

    person_id = row["id"]
    con.execute(
        """
        UPDATE people
           SET full_name = COALESCE(NULLIF(?, ''), full_name),
               primary_email = COALESCE(primary_email, ?),
               phone = COALESCE(phone, ?),
               linkedin_url = COALESCE(linkedin_url, ?),
               twitter_handle = COALESCE(twitter_handle, ?),
               location = COALESCE(location, ?),
               notes = CASE
                   WHEN ? IS NULL THEN notes
                   WHEN notes IS NULL THEN ?
                   WHEN instr(notes, ?) = 0 THEN notes || char(10) || ?
                   ELSE notes
               END,
               confidence = MAX(confidence, ?),
               updated_at = ?
         WHERE id = ?
        """,
        (
            full_name,
            email,
            phone,
            linkedin_url,
            twitter_handle,
            location,
            notes,
            notes,
            notes or "",
            notes,
            confidence,
            ts,
            person_id,
        ),
    )
    con.commit()
    return person_id


def get_or_create_org(con: sqlite3.Connection, name: str | None) -> str | None:
    name = _clean(name)
    if not name:
        return None
    row = con.execute("SELECT id FROM organizations WHERE lower(name) = lower(?)", (name,)).fetchone()
    if row:
        return str(row["id"])
    ts = now_iso()
    org_id = new_id()
    con.execute(
        """
        INSERT INTO organizations (id, name, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (org_id, name, ts, ts),
    )
    con.commit()
    return org_id


def add_role(
    con: sqlite3.Connection,
    *,
    person_id: str,
    organization_id: str | None = None,
    title: str | None = None,
    source: str | None = None,
    source_ref: str | None = None,
    confidence: float = 0.5,
) -> str:
    title = _clean(title)
    row = con.execute(
        """
        SELECT id FROM roles
         WHERE person_id = ?
           AND COALESCE(organization_id, '') = COALESCE(?, '')
           AND COALESCE(title, '') = COALESCE(?, '')
           AND ended_on IS NULL
        """,
        (person_id, organization_id, title),
    ).fetchone()
    ts = now_iso()
    if row:
        con.execute("UPDATE roles SET confidence = MAX(confidence, ?), updated_at = ? WHERE id = ?", (confidence, ts, row["id"]))
        con.commit()
        return str(row["id"])
    role_id = new_id()
    con.execute(
        """
        INSERT INTO roles (
            id, person_id, organization_id, title, source, source_ref,
            confidence, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (role_id, person_id, organization_id, title, source, source_ref, confidence, ts),
    )
    con.commit()
    return role_id


def add_resource(
    con: sqlite3.Connection,
    *,
    person_id: str,
    resource_type: str,
    description: str,
    source: str | None = None,
    confidence: float = 0.5,
) -> str:
    ts = now_iso()
    row = con.execute(
        """
        SELECT id FROM resources
         WHERE person_id = ? AND resource_type = ? AND description = ?
        """,
        (person_id, resource_type, description),
    ).fetchone()
    if row:
        con.execute(
            "UPDATE resources SET confidence = MAX(confidence, ?), updated_at = ? WHERE id = ?",
            (confidence, ts, row["id"]),
        )
        con.commit()
        return str(row["id"])
    resource_id = new_id()
    con.execute(
        """
        INSERT INTO resources (id, person_id, resource_type, description, source, confidence, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (resource_id, person_id, resource_type, description, source, confidence, ts),
    )
    con.commit()
    return resource_id


def add_connection_value(
    con: sqlite3.Connection,
    *,
    person_id: str,
    value_type: str,
    description: str,
    score: int = 50,
    evidence: str | None = None,
    source: str | None = None,
    source_ref: str | None = None,
    confidence: float = 0.5,
) -> str:
    value_type = (_clean(value_type) or "unknown").lower()
    description = _clean(description) or "Unspecified value"
    evidence = _clean(evidence)
    score = max(0, min(100, int(score)))
    ts = now_iso()
    row = con.execute(
        """
        SELECT id, evidence FROM connection_values
         WHERE person_id = ? AND value_type = ? AND description = ?
        """,
        (person_id, value_type, description),
    ).fetchone()
    if row:
        merged_evidence = row["evidence"]
        if evidence and (not merged_evidence or evidence not in merged_evidence):
            merged_evidence = f"{merged_evidence}\n{evidence}" if merged_evidence else evidence
        con.execute(
            """
            UPDATE connection_values
               SET score = MAX(score, ?),
                   evidence = ?,
                   source = COALESCE(source, ?),
                   source_ref = COALESCE(source_ref, ?),
                   confidence = MAX(confidence, ?),
                   updated_at = ?
             WHERE id = ?
            """,
            (score, merged_evidence, source, source_ref, confidence, ts, row["id"]),
        )
        con.commit()
        return str(row["id"])

    value_id = new_id()
    con.execute(
        """
        INSERT INTO connection_values (
            id, person_id, value_type, description, score, evidence,
            source, source_ref, confidence, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (value_id, person_id, value_type, description, score, evidence, source, source_ref, confidence, ts),
    )
    con.commit()
    return value_id


def list_connection_values(
    con: sqlite3.Connection,
    *,
    value_type: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = ""
    if value_type:
        where = "WHERE connection_values.value_type = ?"
        params.append(value_type)
    query = f"""
        SELECT
            connection_values.*,
            people.full_name,
            people.primary_email,
            people.linkedin_url,
            people.twitter_handle,
            group_concat(DISTINCT organizations.name) AS organizations
          FROM connection_values
          JOIN people ON people.id = connection_values.person_id
          LEFT JOIN roles ON roles.person_id = people.id
          LEFT JOIN organizations ON organizations.id = roles.organization_id
          {where}
         GROUP BY connection_values.id
         ORDER BY connection_values.score DESC, connection_values.updated_at DESC
    """
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    return rows_to_dicts(con.execute(query, params).fetchall())


def add_interaction(
    con: sqlite3.Connection,
    *,
    person_id: str,
    channel: str,
    direction: str | None = None,
    subject: str | None = None,
    body_summary: str | None = None,
    occurred_at: str | None = None,
    source: str | None = None,
    source_ref: str | None = None,
    sentiment: str | None = None,
) -> str:
    if source_ref:
        existing = con.execute(
            """
            SELECT id FROM interactions
             WHERE person_id = ?
               AND channel = ?
               AND COALESCE(source, '') = COALESCE(?, '')
               AND source_ref = ?
               AND COALESCE(subject, '') = COALESCE(?, '')
            """,
            (person_id, channel, source, source_ref, _clean(subject) or ""),
        ).fetchone()
        if existing:
            return str(existing["id"])

    ts = now_iso()
    interaction_id = new_id()
    con.execute(
        """
        INSERT INTO interactions (
            id, person_id, channel, direction, subject, body_summary,
            occurred_at, source, source_ref, sentiment, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            interaction_id,
            person_id,
            channel,
            direction,
            _clean(subject),
            _clean(body_summary),
            occurred_at,
            source,
            source_ref,
            sentiment,
            ts,
        ),
    )
    if occurred_at:
        con.execute(
            """
            UPDATE relationships
               SET last_interaction_at =
                   CASE
                     WHEN last_interaction_at IS NULL OR last_interaction_at < ? THEN ?
                     ELSE last_interaction_at
                   END,
                   updated_at = ?
             WHERE person_id = ?
            """,
            (occurred_at, occurred_at, ts, person_id),
        )
    con.commit()
    return interaction_id


def add_source_fact(
    con: sqlite3.Connection,
    *,
    person_id: str | None,
    fact_type: str,
    fact_value: str,
    source: str | None = None,
    source_ref: str | None = None,
    confidence: float = 0.5,
) -> str:
    existing = con.execute(
        """
        SELECT id FROM source_facts
         WHERE COALESCE(person_id, '') = COALESCE(?, '')
           AND fact_type = ?
           AND fact_value = ?
           AND COALESCE(source, '') = COALESCE(?, '')
           AND COALESCE(source_ref, '') = COALESCE(?, '')
        """,
        (person_id, fact_type, fact_value, source, source_ref),
    ).fetchone()
    if existing:
        return str(existing["id"])

    fact_id = new_id()
    con.execute(
        """
        INSERT INTO source_facts (
            id, person_id, fact_type, fact_value, source, source_ref, confidence, observed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (fact_id, person_id, fact_type, fact_value, source, source_ref, confidence, now_iso()),
    )
    con.commit()
    return fact_id


def record_source_run(
    con: sqlite3.Connection,
    *,
    source: str,
    source_ref: str | None,
    status: str,
    stats: dict[str, Any] | None = None,
) -> str:
    run_id = new_id()
    ts = now_iso()
    con.execute(
        """
        INSERT INTO source_runs (
            id, source, source_ref, status, stats_json, started_at, finished_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, source, source_ref, status, json.dumps(stats or {}, sort_keys=True), ts, ts),
    )
    con.commit()
    return run_id


def create_goal(
    con: sqlite3.Connection,
    *,
    title: str,
    cadence: str,
    capital_type: str | None = None,
    target_segment: str | None = None,
    success_metric: str | None = None,
    starts_on: str | None = None,
    ends_on: str | None = None,
) -> str:
    ts = now_iso()
    goal_id = new_id()
    con.execute(
        """
        INSERT INTO goals (
            id, title, cadence, capital_type, target_segment, success_metric,
            starts_on, ends_on, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (goal_id, title, cadence, capital_type, target_segment, success_metric, starts_on, ends_on, ts, ts),
    )
    con.commit()
    return goal_id


def list_goals(con: sqlite3.Connection, status: str | None = "active") -> list[dict[str, Any]]:
    if status is None:
        rows = con.execute("SELECT * FROM goals ORDER BY created_at DESC").fetchall()
    else:
        rows = con.execute("SELECT * FROM goals WHERE status = ? ORDER BY created_at DESC", (status,)).fetchall()
    return rows_to_dicts(rows)
