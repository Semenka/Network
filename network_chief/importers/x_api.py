"""X.com (Twitter) v2 OAuth + sync.

Free tier supports user-context endpoints with tight monthly caps. We
gracefully degrade on rate-limit by recording ``status="rate_limited"``.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

from ..auth.errors import AuthRequired, RateLimited
from ..auth.http_util import paginate, request_json
from ..auth.oauth import OAuthFlow
from ..auth.tokens import TokenStore, is_expired
from ..db import (
    add_connection_value,
    add_interaction,
    add_source_fact,
    upsert_person,
)
from ..scoring import infer_connection_values_from_text


PROVIDER = "x"
AUTH_URL = "https://twitter.com/i/oauth2/authorize"
TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
REVOKE_URL = "https://api.twitter.com/2/oauth2/revoke"
ME_URL = "https://api.twitter.com/2/users/me"
FOLLOWING_URL = "https://api.twitter.com/2/users/{id}/following"
MENTIONS_URL = "https://api.twitter.com/2/users/{id}/mentions"

DEFAULT_SCOPES = "tweet.read users.read follows.read offline.access"


def _port() -> int:
    return int(os.environ.get("NETWORK_CHIEF_OAUTH_PORT_X", "47319"))


def _flow(client_id: str, client_secret: str | None, *, port: int, scopes: str = DEFAULT_SCOPES) -> OAuthFlow:
    use_basic = bool(client_secret)
    return OAuthFlow(
        provider=PROVIDER,
        client_id=client_id,
        client_secret=client_secret,
        auth_url=AUTH_URL,
        token_url=TOKEN_URL,
        revoke_url=REVOKE_URL,
        scopes=scopes,
        redirect_port=port,
        use_pkce=True,
        use_basic_auth=use_basic,
    )


def auth_x(
    con: sqlite3.Connection,
    *,
    client_id: str | None = None,
    client_secret: str | None = None,
    scopes: str | None = None,
    open_browser: bool = True,
) -> dict[str, Any]:
    cid = client_id or os.environ.get("X_CLIENT_ID")
    csec = client_secret if client_secret is not None else os.environ.get("X_CLIENT_SECRET") or None
    if not cid:
        raise AuthRequired(
            "X_CLIENT_ID not set. Register an OAuth 2.0 app at https://developer.x.com/portal "
            f"and add redirect URI http://127.0.0.1:{_port()}/callback"
        )
    flow = _flow(cid, csec, port=_port(), scopes=scopes or DEFAULT_SCOPES)
    token = flow.authorize_blocking(open_browser=open_browser)
    me = request_json(
        "GET",
        ME_URL,
        headers={"Authorization": f"Bearer {token['access_token']}"},
        params={"user.fields": "username,name,description,location,public_metrics"},
    )
    user = (me.get("data") or {})
    handle = (user.get("username") or "").lower() or "x"
    user_id = user.get("id")

    store = TokenStore(con)
    store.save(
        provider=PROVIDER,
        account=handle,
        access_token=token["access_token"],
        refresh_token=token.get("refresh_token"),
        expires_at=token.get("_expires_at"),
        scopes=token.get("scope") or DEFAULT_SCOPES,
        token_type=token.get("token_type", "Bearer"),
        extra={"client_id": cid, "user_id": user_id, "name": user.get("name")},
    )
    return {"account": handle, "user_id": user_id, "expires_at": token.get("_expires_at")}


def _ensure_token(con: sqlite3.Connection, *, account: str | None = None) -> dict[str, Any]:
    store = TokenStore(con)
    record = store.get(PROVIDER, account)
    if not record:
        raise AuthRequired("No X token. Run: network-chief auth-x")
    if is_expired(record.get("expires_at")):
        record = _refresh(con, record)
    return record


def _refresh(con: sqlite3.Connection, record: dict[str, Any]) -> dict[str, Any]:
    refresh_token = record.get("refresh_token")
    if not refresh_token:
        raise AuthRequired("X token expired and no refresh_token. Re-run: network-chief auth-x")
    extra = record.get("extra") or json.loads(record.get("extra_json") or "{}")
    cid = extra.get("client_id") or os.environ.get("X_CLIENT_ID")
    csec = os.environ.get("X_CLIENT_SECRET") or None
    if not cid:
        raise AuthRequired("Cannot refresh X token without X_CLIENT_ID")
    flow = _flow(cid, csec, port=_port(), scopes=record.get("scopes") or DEFAULT_SCOPES)
    token = flow.refresh(refresh_token)
    store = TokenStore(con)
    store.mark_refreshed(
        record["id"],
        access_token=token["access_token"],
        expires_at=token.get("_expires_at"),
        refresh_token=token.get("refresh_token"),
    )
    return store.get(PROVIDER, record["account"])  # type: ignore[return-value]


def _user_id(record: dict[str, Any]) -> str:
    extra = record.get("extra") or json.loads(record.get("extra_json") or "{}")
    user_id = extra.get("user_id")
    if not user_id:
        raise AuthRequired("X token has no user_id; re-run network-chief auth-x")
    return str(user_id)


def _authed_headers(record: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {record['access_token']}"}


def sync_x_following(
    con: sqlite3.Connection,
    *,
    limit: int | None = None,
    max_pages: int | None = None,
) -> dict[str, Any]:
    record = _ensure_token(con)
    headers = _authed_headers(record)
    user_id = _user_id(record)
    url = FOLLOWING_URL.format(id=user_id)

    def fetcher(meta: dict[str, Any] | None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "max_results": 1000,
            "user.fields": "description,location,url,verified,public_metrics",
        }
        if meta and meta.get("page_token"):
            params["pagination_token"] = meta["page_token"]
        return request_json("GET", url, headers=headers, params=params)

    seen = values = pages = 0
    try:
        for page in paginate(fetcher, next_token_keys=("next_token",), max_pages=max_pages):
            pages += 1
            for user in page.get("data") or []:
                if limit and seen >= limit:
                    return {"people_seen": seen, "values_added": values, "pages": pages, "status": "ok"}
                handle = (user.get("username") or "").lower()
                if not handle:
                    continue
                bio = user.get("description") or None
                name = user.get("name") or handle
                location = user.get("location") or None
                person_id = upsert_person(
                    con,
                    full_name=name,
                    twitter_handle=handle,
                    location=location,
                    notes=bio,
                    confidence=0.7,
                )
                seen += 1
                add_source_fact(
                    con,
                    person_id=person_id,
                    fact_type="x_following",
                    fact_value=(bio or "")[:500],
                    source="x_api",
                    source_ref=user.get("id"),
                    confidence=0.7,
                )
                if bio:
                    for value_type, description, score in infer_connection_values_from_text(bio):
                        add_connection_value(
                            con,
                            person_id=person_id,
                            value_type=value_type,
                            description=description,
                            score=score,
                            evidence=bio[:500],
                            source="x_api",
                            source_ref=user.get("id"),
                            confidence=0.45,
                        )
                        values += 1
    except RateLimited as exc:
        return {
            "people_seen": seen,
            "values_added": values,
            "pages": pages,
            "status": "rate_limited",
            "reset_at": exc.reset_at,
        }
    return {"people_seen": seen, "values_added": values, "pages": pages, "status": "ok"}


def sync_x_mentions(
    con: sqlite3.Connection,
    *,
    since: str | None = None,
    limit: int | None = None,
    max_pages: int | None = None,
) -> dict[str, Any]:
    record = _ensure_token(con)
    headers = _authed_headers(record)
    user_id = _user_id(record)
    url = MENTIONS_URL.format(id=user_id)

    def fetcher(meta: dict[str, Any] | None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "max_results": 100,
            "expansions": "author_id",
            "user.fields": "username,name,description",
            "tweet.fields": "created_at,entities,text",
        }
        if since:
            params["start_time"] = since
        if meta and meta.get("page_token"):
            params["pagination_token"] = meta["page_token"]
        return request_json("GET", url, headers=headers, params=params)

    seen = interactions = pages = 0
    try:
        for page in paginate(fetcher, next_token_keys=("next_token",), max_pages=max_pages):
            pages += 1
            users_by_id = {u["id"]: u for u in (page.get("includes") or {}).get("users") or []}
            for tweet in page.get("data") or []:
                if limit and seen >= limit:
                    return {"mentions_seen": seen, "interactions_seen": interactions, "pages": pages, "status": "ok"}
                author_id = tweet.get("author_id")
                author = users_by_id.get(author_id) if author_id else None
                if not author:
                    continue
                handle = (author.get("username") or "").lower()
                if not handle:
                    continue
                person_id = upsert_person(
                    con,
                    full_name=author.get("name") or handle,
                    twitter_handle=handle,
                    notes=author.get("description") or None,
                    confidence=0.65,
                )
                add_interaction(
                    con,
                    person_id=person_id,
                    channel="x",
                    direction="incoming",
                    subject="X mention",
                    body_summary=(tweet.get("text") or "")[:500],
                    occurred_at=tweet.get("created_at"),
                    source="x_api",
                    source_ref=tweet.get("id"),
                )
                seen += 1
                interactions += 1
    except RateLimited as exc:
        return {
            "mentions_seen": seen,
            "interactions_seen": interactions,
            "pages": pages,
            "status": "rate_limited",
            "reset_at": exc.reset_at,
        }
    return {"mentions_seen": seen, "interactions_seen": interactions, "pages": pages, "status": "ok"}


def revoke_x(con: sqlite3.Connection, *, account: str | None = None) -> int:
    store = TokenStore(con)
    record = store.get(PROVIDER, account)
    if not record:
        return 0
    extra = record.get("extra") or json.loads(record.get("extra_json") or "{}")
    cid = extra.get("client_id") or os.environ.get("X_CLIENT_ID")
    csec = os.environ.get("X_CLIENT_SECRET") or None
    if cid:
        flow = _flow(cid, csec, port=_port(), scopes=record.get("scopes") or DEFAULT_SCOPES)
        flow.revoke(record["access_token"])
    return store.delete(PROVIDER, record["account"])
