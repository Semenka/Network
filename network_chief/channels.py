from __future__ import annotations

import sqlite3
from typing import Any

from .db import has_send_enabled_account, list_channel_accounts, upsert_channel_account
from .drafts import create_draft
from .engagement import prepare_gmail_keepalive
from .scoring import rank_people


SUPPORTED_DRAFT_CHANNELS = {"gmail", "linkedin", "telegram"}


def add_or_update_channel_account(
    con: sqlite3.Connection,
    *,
    person_id: str,
    channel: str,
    account_ref: str,
    display_name: str | None = None,
    send_enabled: bool | None = None,
    source: str = "manual",
    confidence: float = 0.9,
) -> str:
    channel = channel.lower().strip()
    if send_enabled is None:
        send_enabled = channel in {"gmail", "telegram"}
    return upsert_channel_account(
        con,
        person_id=person_id,
        channel=channel,
        account_ref=account_ref,
        display_name=display_name,
        send_enabled=send_enabled,
        source=source,
        confidence=confidence,
    )


def format_channel_accounts(accounts: list[dict[str, Any]]) -> str:
    if not accounts:
        return "No channel accounts found."
    lines = []
    for account in accounts:
        allowed = "send-ok" if account.get("send_enabled") else "manual-only"
        lines.append(
            f"{account['id']} | {account['channel']} | {account['account_ref']} | "
            f"{account.get('full_name') or account['person_id']} | {allowed}"
        )
    return "\n".join(lines)


def prepare_channel_drafts(
    con: sqlite3.Connection,
    *,
    channels: list[str],
    limit: int = 10,
) -> dict[str, list[str]]:
    requested = [channel.lower().strip() for channel in channels if channel.strip()]
    unsupported = [channel for channel in requested if channel not in SUPPORTED_DRAFT_CHANNELS]
    if unsupported:
        raise ValueError(f"Unsupported draft channel(s): {', '.join(sorted(set(unsupported)))}")

    prepared: dict[str, list[str]] = {channel: [] for channel in requested}
    if "gmail" in requested:
        prepared["gmail"] = prepare_gmail_keepalive(con, limit=limit)

    ranked = rank_people(con, limit=max(limit * 4, 20), mode="relationship")
    if "linkedin" in requested:
        for person in ranked:
            if not (person.get("linkedin_url") or _has_channel_account(con, person["id"], "linkedin")):
                continue
            prepared["linkedin"].append(create_draft(con, person=person, goal=person.get("goal"), channel="linkedin"))
            if len(prepared["linkedin"]) >= limit:
                break

    if "telegram" in requested:
        for person in ranked:
            if not has_send_enabled_account(con, person_id=person["id"], channel="telegram"):
                continue
            prepared["telegram"].append(create_draft(con, person=person, goal=person.get("goal"), channel="telegram"))
            if len(prepared["telegram"]) >= limit:
                break

    return prepared


def _has_channel_account(con: sqlite3.Connection, person_id: str, channel: str) -> bool:
    return bool(list_channel_accounts(con, person_id=person_id, channel=channel, limit=1))

