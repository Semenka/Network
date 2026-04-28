from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

from ..db import add_connection_value, add_interaction, add_source_fact, upsert_person
from ..scoring import infer_connection_values_from_text


def _clean_handle(value: str | None) -> str | None:
    if not value:
        return None
    value = str(value).strip()
    if value.startswith("https://x.com/") or value.startswith("https://twitter.com/"):
        value = value.rstrip("/").split("/")[-1]
    return value.lstrip("@").strip().lower() or None


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


def _load_jsonish(path: Path) -> Any:
    text = path.read_text(encoding="utf-8").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if "=" in text:
        payload = text.split("=", 1)[1].strip().rstrip(";")
        return json.loads(payload)
    raise


def _records_from_path(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return [dict(row) for row in csv.DictReader(handle)]

    data = _load_jsonish(path)
    if isinstance(data, dict):
        for key in ("people", "accounts", "following", "tweets", "items", "results", "messages"):
            if isinstance(data.get(key), list):
                return list(data[key])
        return [data]
    if isinstance(data, list):
        return list(data)
    raise ValueError("X import expects CSV, JSON, or X archive-style JavaScript containing records.")


def _account_record(record: dict[str, Any]) -> dict[str, Any]:
    for key in ("account", "user", "profile", "person"):
        value = record.get(key)
        if isinstance(value, dict):
            return value
    return record


def _tweet_record(record: dict[str, Any]) -> dict[str, Any] | None:
    tweet = record.get("tweet")
    return tweet if isinstance(tweet, dict) else None


def _mentions_from_tweet(tweet: dict[str, Any]) -> list[dict[str, str]]:
    entities = tweet.get("entities") if isinstance(tweet.get("entities"), dict) else {}
    mentions = entities.get("user_mentions") or entities.get("mentions") or []
    if not isinstance(mentions, list):
        return []
    normalized = []
    for mention in mentions:
        if not isinstance(mention, dict):
            continue
        handle = _clean_handle(str(mention.get("screen_name") or mention.get("username") or mention.get("handle") or ""))
        if not handle:
            continue
        normalized.append(
            {
                "handle": handle,
                "name": str(mention.get("name") or handle),
            }
        )
    return normalized


def import_x_export(
    con: sqlite3.Connection,
    path: str | Path,
    *,
    owner_handle: str | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    owner = _clean_handle(owner_handle)
    records = _records_from_path(path)
    if limit:
        records = records[:limit]

    people = interactions = values = posts = 0
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue

        tweet = _tweet_record(record)
        if tweet:
            text = _pick(tweet, "full_text", "text", "body") or ""
            created_at = _pick(tweet, "created_at", "date", "timestamp")
            source_ref = _pick(tweet, "id_str", "id") or f"{path}:{index}"
            mentions = _mentions_from_tweet(tweet)
            posts += 1
            for mention in mentions:
                if owner and mention["handle"] == owner:
                    continue
                person_id = upsert_person(
                    con,
                    full_name=mention["name"],
                    twitter_handle=mention["handle"],
                    confidence=0.55,
                )
                people += 1
                add_interaction(
                    con,
                    person_id=person_id,
                    channel="x",
                    direction="outgoing",
                    subject="X mention",
                    body_summary=text[:500],
                    occurred_at=created_at,
                    source="x_export",
                    source_ref=str(source_ref),
                )
                interactions += 1
            if text:
                add_source_fact(
                    con,
                    person_id=None,
                    fact_type="x_post",
                    fact_value=text[:500],
                    source="x_export",
                    source_ref=str(source_ref),
                    confidence=0.65,
                )
            continue

        account = _account_record(record)
        handle = _clean_handle(
            _pick(account, "handle", "username", "screen_name", "twitter_handle", "accountId", "account")
        )
        if not handle or (owner and handle == owner):
            continue
        name = _pick(account, "name", "display_name", "accountDisplayName", "full_name") or handle
        bio = _pick(account, "bio", "description", "summary", "about")
        location = _pick(account, "location")
        text = _pick(account, "text", "message", "body", "snippet")
        date = _pick(account, "date", "created_at", "last_interaction_at", "timestamp")
        interaction_type = _pick(account, "interaction_type", "type", "kind") or "profile"
        source_ref = _pick(account, "id", "id_str", "profile_url", "url") or f"{path}:{index}"

        person_id = upsert_person(
            con,
            full_name=name,
            twitter_handle=handle,
            location=location,
            notes=bio,
            confidence=0.6,
        )
        people += 1
        add_source_fact(
            con,
            person_id=person_id,
            fact_type="x_profile",
            fact_value=" ".join(part for part in (f"@{handle}", name, bio) if part)[:500],
            source="x_export",
            source_ref=str(source_ref),
            confidence=0.6,
        )
        if text or date:
            add_interaction(
                con,
                person_id=person_id,
                channel="x",
                direction="community",
                subject=f"X {interaction_type}",
                body_summary=text[:500] if text else None,
                occurred_at=date,
                source="x_export",
                source_ref=str(source_ref),
            )
            interactions += 1
        for value_type, description, score in infer_connection_values_from_text(" ".join(part for part in (bio, text) if part)):
            add_connection_value(
                con,
                person_id=person_id,
                value_type=value_type,
                description=description,
                score=score,
                evidence=" ".join(part for part in (bio, text) if part)[:500],
                source="x_export",
                source_ref=str(source_ref),
                confidence=0.45,
            )
            values += 1

    return {"people_seen": people, "posts_seen": posts, "interactions_seen": interactions, "values_seen": values}
