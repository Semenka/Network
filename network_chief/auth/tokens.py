from __future__ import annotations

import json as _json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from ..db import new_id, now_iso, rows_to_dicts


class TokenStore:
    """SQLite-backed token store. One row per (provider, account)."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def save(
        self,
        *,
        provider: str,
        account: str,
        access_token: str,
        refresh_token: str | None = None,
        expires_at: str | None = None,
        scopes: str = "",
        token_type: str = "Bearer",
        extra: dict[str, Any] | None = None,
    ) -> str:
        ts = now_iso()
        existing = self.get(provider, account)
        extra_json = _json.dumps(extra or {}, sort_keys=True)
        if existing:
            self.con.execute(
                """
                UPDATE oauth_tokens
                   SET access_token = ?,
                       refresh_token = COALESCE(?, refresh_token),
                       token_type = ?,
                       scopes = ?,
                       expires_at = ?,
                       extra_json = ?,
                       updated_at = ?
                 WHERE id = ?
                """,
                (
                    access_token,
                    refresh_token,
                    token_type,
                    scopes,
                    expires_at,
                    extra_json,
                    ts,
                    existing["id"],
                ),
            )
            self.con.commit()
            return str(existing["id"])

        token_id = new_id()
        self.con.execute(
            """
            INSERT INTO oauth_tokens (
                id, provider, account, access_token, refresh_token, token_type,
                scopes, expires_at, obtained_at, updated_at, extra_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token_id,
                provider,
                account,
                access_token,
                refresh_token,
                token_type,
                scopes,
                expires_at,
                ts,
                ts,
                extra_json,
            ),
        )
        self.con.commit()
        return token_id

    def get(self, provider: str, account: str | None = None) -> dict[str, Any] | None:
        if account is None:
            row = self.con.execute(
                "SELECT * FROM oauth_tokens WHERE provider = ? ORDER BY updated_at DESC LIMIT 1",
                (provider,),
            ).fetchone()
        else:
            row = self.con.execute(
                "SELECT * FROM oauth_tokens WHERE provider = ? AND lower(account) = lower(?)",
                (provider, account),
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["extra"] = _json.loads(record.get("extra_json") or "{}")
        return record

    def list(self) -> list[dict[str, Any]]:
        rows = self.con.execute(
            "SELECT * FROM oauth_tokens ORDER BY provider, updated_at DESC"
        ).fetchall()
        return rows_to_dicts(rows)

    def delete(self, provider: str, account: str | None = None) -> int:
        if account is None:
            cur = self.con.execute("DELETE FROM oauth_tokens WHERE provider = ?", (provider,))
        else:
            cur = self.con.execute(
                "DELETE FROM oauth_tokens WHERE provider = ? AND lower(account) = lower(?)",
                (provider, account),
            )
        self.con.commit()
        return cur.rowcount

    def mark_refreshed(
        self,
        token_id: str,
        *,
        access_token: str,
        expires_at: str | None,
        refresh_token: str | None = None,
    ) -> None:
        self.con.execute(
            """
            UPDATE oauth_tokens
               SET access_token = ?,
                   refresh_token = COALESCE(?, refresh_token),
                   expires_at = ?,
                   updated_at = ?
             WHERE id = ?
            """,
            (access_token, refresh_token, expires_at, now_iso(), token_id),
        )
        self.con.commit()


def expires_at_from_seconds(expires_in: int | float | None) -> str | None:
    if not expires_in:
        return None
    target = datetime.now(UTC) + timedelta(seconds=int(expires_in))
    return target.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def is_expired(expires_at: str | None, *, leeway_s: int = 60) -> bool:
    if not expires_at:
        return False
    try:
        target = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    return datetime.now(UTC) + timedelta(seconds=leeway_s) >= target
