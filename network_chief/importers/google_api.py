"""Google OAuth + People/Gmail/Drive sync.

People API (``contacts.readonly``) gives us connections; Gmail API
(``gmail.readonly``) gives us interaction signal. The Drive API
(``drive``) is used opportunistically — only to pull a specific file
the user points at by id, e.g. a LinkedIn ``Connections.csv`` they
uploaded to Drive.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from ..auth.errors import AuthRequired
from ..auth.http_util import paginate, request_json
from ..auth.oauth import OAuthFlow
from ..auth.tokens import TokenStore, expires_at_from_seconds, is_expired
from ..db import (
    add_connection_value,
    add_interaction,
    add_role,
    add_source_fact,
    get_or_create_org,
    upsert_person,
)
from ..scoring import infer_connection_values_from_text
from ._addresses import parse_addresses, parse_date


PROVIDER = "google"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

SCOPE_OPENID = "openid email"
SCOPE_PEOPLE = "https://www.googleapis.com/auth/contacts.readonly"
SCOPE_GMAIL = "https://www.googleapis.com/auth/gmail.readonly"
DEFAULT_SCOPES = " ".join((SCOPE_OPENID, SCOPE_PEOPLE, SCOPE_GMAIL))

PEOPLE_URL = "https://people.googleapis.com/v1/people/me/connections"
GMAIL_LIST_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
GMAIL_GET_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/{id}"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files/{id}"
DRIVE_EXPORT_URL = "https://www.googleapis.com/drive/v3/files/{id}/export"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def _flow(client_id: str, client_secret: str, *, port: int, scopes: str = DEFAULT_SCOPES) -> OAuthFlow:
    return OAuthFlow(
        provider=PROVIDER,
        client_id=client_id,
        client_secret=client_secret,
        auth_url=AUTH_URL,
        token_url=TOKEN_URL,
        revoke_url=REVOKE_URL,
        scopes=scopes,
        redirect_port=port,
        # access_type=offline + prompt=consent forces a fresh refresh_token.
        extra_auth_params={"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"},
        use_pkce=True,
        use_basic_auth=False,
    )


def _decode_id_token_email(id_token: str | None) -> str | None:
    if not id_token:
        return None
    try:
        _, payload, _ = id_token.split(".")
    except ValueError:
        return None
    padded = payload + "=" * (-len(payload) % 4)
    try:
        body = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    email = body.get("email")
    return email.lower() if isinstance(email, str) else None


def _port() -> int:
    return int(os.environ.get("NETWORK_CHIEF_OAUTH_PORT_GOOGLE", "47318"))


def auth_google(
    con: sqlite3.Connection,
    *,
    client_id: str | None = None,
    client_secret: str | None = None,
    scopes: str | None = None,
    open_browser: bool = True,
    manual: bool = False,
    redirect_url: str | None = None,
) -> dict[str, Any]:
    """Run the Google OAuth flow and persist the resulting token."""
    cid = client_id or os.environ.get("GOOGLE_CLIENT_ID")
    csec = client_secret or os.environ.get("GOOGLE_CLIENT_SECRET")
    if not cid or not csec:
        raise AuthRequired(
            "GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET not set. Register an OAuth client at "
            "https://console.cloud.google.com/apis/credentials and add redirect URI "
            f"http://127.0.0.1:{_port()}/callback"
        )
    flow = _flow(cid, csec, port=_port(), scopes=scopes or DEFAULT_SCOPES)
    if redirect_url:
        token = flow.authorize_finish(redirect_url)
    elif manual:
        info = flow.authorize_start()
        return {"manual_step": "open_url", **info}
    else:
        token = flow.authorize_blocking(open_browser=open_browser)
    account = _decode_id_token_email(token.get("id_token")) or "google"
    if account == "google":
        try:
            info = request_json(
                "GET", USERINFO_URL,
                headers={"Authorization": f"Bearer {token['access_token']}"},
            )
            account = (info.get("email") or "google").lower()
        except Exception:
            pass

    store = TokenStore(con)
    store.save(
        provider=PROVIDER,
        account=account,
        access_token=token["access_token"],
        refresh_token=token.get("refresh_token"),
        expires_at=token.get("_expires_at"),
        scopes=token.get("scope") or DEFAULT_SCOPES,
        token_type=token.get("token_type", "Bearer"),
        extra={"client_id": cid, "id_token": token.get("id_token")},
    )
    return {"account": account, "scopes": token.get("scope") or DEFAULT_SCOPES, "expires_at": token.get("_expires_at")}


def _ensure_token(con: sqlite3.Connection, *, account: str | None = None) -> dict[str, Any]:
    store = TokenStore(con)
    record = store.get(PROVIDER, account)
    if not record:
        raise AuthRequired("No Google token. Run: network-chief auth-google")
    if is_expired(record.get("expires_at")):
        record = _refresh(con, record)
    return record


def _refresh(con: sqlite3.Connection, record: dict[str, Any]) -> dict[str, Any]:
    refresh_token = record.get("refresh_token")
    if not refresh_token:
        raise AuthRequired("Google token expired and no refresh_token. Re-run: network-chief auth-google")
    extra = record.get("extra") or json.loads(record.get("extra_json") or "{}")
    cid = extra.get("client_id") or os.environ.get("GOOGLE_CLIENT_ID")
    csec = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not cid or not csec:
        raise AuthRequired("Cannot refresh Google token without GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET")
    flow = _flow(cid, csec, port=_port(), scopes=record.get("scopes") or DEFAULT_SCOPES)
    token = flow.refresh(refresh_token)
    store = TokenStore(con)
    store.mark_refreshed(
        record["id"],
        access_token=token["access_token"],
        expires_at=token.get("_expires_at"),
        refresh_token=token.get("refresh_token"),
    )
    record = store.get(PROVIDER, record["account"])
    return record  # type: ignore[return-value]


def _authed_headers(record: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {record['access_token']}"}


def sync_google_contacts(
    con: sqlite3.Connection,
    *,
    limit: int | None = None,
    page_size: int = 200,
) -> dict[str, int]:
    """Pull Google People API ``connections`` and upsert them as people."""
    record = _ensure_token(con)
    headers = _authed_headers(record)
    person_fields = ",".join(
        ("names", "emailAddresses", "phoneNumbers", "organizations", "urls", "locations", "biographies", "metadata")
    )

    def fetcher(meta: dict[str, Any] | None) -> dict[str, Any]:
        params = {"personFields": person_fields, "pageSize": page_size}
        if meta and meta.get("page_token"):
            params["pageToken"] = meta["page_token"]
        return request_json("GET", PEOPLE_URL, headers=headers, params=params)

    seen_people = roles_added = values_added = pages = 0
    for page in paginate(fetcher, next_token_keys=("nextPageToken",)):
        pages += 1
        for connection in page.get("connections") or []:
            if limit and seen_people >= limit:
                return _stats(seen_people, roles_added, values_added, pages, "ok")
            person_id, role_count, value_count = _upsert_google_connection(con, connection)
            if person_id:
                seen_people += 1
                roles_added += role_count
                values_added += value_count
    return _stats(seen_people, roles_added, values_added, pages, "ok")


def _stats(people: int, roles: int, values: int, pages: int, status: str) -> dict[str, int]:
    return {"people_seen": people, "roles_added": roles, "values_added": values, "pages": pages, "status": status}


def _first(items: list[dict[str, Any]] | None, key: str) -> str | None:
    if not items:
        return None
    primary = next((item for item in items if (item.get("metadata") or {}).get("primary")), items[0])
    value = primary.get(key)
    return str(value).strip() if value else None


def _linkedin_url_from_urls(urls: list[dict[str, Any]] | None) -> str | None:
    for url in urls or []:
        value = url.get("value") or ""
        if "linkedin.com/in/" in value:
            return value
    return None


def _upsert_google_connection(con: sqlite3.Connection, connection: dict[str, Any]) -> tuple[str | None, int, int]:
    full_name = _first(connection.get("names"), "displayName") or "Unknown"
    email = _first(connection.get("emailAddresses"), "value")
    phone = _first(connection.get("phoneNumbers"), "value")
    bio = _first(connection.get("biographies"), "value")
    location = _first(connection.get("locations"), "value")
    linkedin = _linkedin_url_from_urls(connection.get("urls"))
    resource_name = connection.get("resourceName") or ""

    person_id = upsert_person(
        con,
        full_name=full_name,
        email=email,
        phone=phone,
        linkedin_url=linkedin,
        location=location,
        notes=bio,
        confidence=0.8,
    )

    roles_added = 0
    for org in connection.get("organizations") or []:
        org_name = (org.get("name") or "").strip()
        title = (org.get("title") or "").strip() or None
        if not org_name and not title:
            continue
        org_id = get_or_create_org(con, org_name) if org_name else None
        add_role(
            con,
            person_id=person_id,
            organization_id=org_id,
            title=title,
            source="google_people",
            source_ref=resource_name,
            confidence=0.8,
        )
        roles_added += 1

    add_source_fact(
        con,
        person_id=person_id,
        fact_type="google_contact",
        fact_value=resource_name or email or full_name,
        source="google_people",
        source_ref=resource_name,
        confidence=0.8,
    )

    haystack_parts: list[str] = [full_name, bio or ""]
    haystack_parts.extend(((org.get("name") or "") + " " + (org.get("title") or "")).strip() for org in connection.get("organizations") or [])
    haystack = " ".join(part for part in haystack_parts if part)
    values_added = 0
    for value_type, description, score in infer_connection_values_from_text(haystack):
        add_connection_value(
            con,
            person_id=person_id,
            value_type=value_type,
            description=description,
            score=score,
            evidence=haystack[:500],
            source="google_people",
            source_ref=resource_name,
            confidence=0.55,
        )
        values_added += 1

    return person_id, roles_added, values_added


def sync_gmail_messages(
    con: sqlite3.Connection,
    *,
    since: str | None = None,
    limit: int | None = 100,
    query: str | None = None,
) -> dict[str, int]:
    """Pull recent Gmail message metadata via the Gmail API."""
    record = _ensure_token(con)
    headers = _authed_headers(record)
    owner = (record.get("account") or "").lower()

    q_parts: list[str] = []
    if query:
        q_parts.append(query)
    if since:
        q_parts.append(f"after:{_to_unix(since)}")
    elif not query:
        q_parts.append("newer_than:30d")
    q = " ".join(q_parts)

    def list_fetcher(meta: dict[str, Any] | None) -> dict[str, Any]:
        params: dict[str, Any] = {"maxResults": min(500, limit or 100), "q": q}
        if meta and meta.get("page_token"):
            params["pageToken"] = meta["page_token"]
        return request_json("GET", GMAIL_LIST_URL, headers=headers, params=params)

    seen_messages = seen_people = seen_interactions = 0
    for page in paginate(list_fetcher, next_token_keys=("nextPageToken",)):
        for entry in page.get("messages") or []:
            if limit and seen_messages >= limit:
                return {
                    "messages_seen": seen_messages,
                    "people_seen": seen_people,
                    "interactions_seen": seen_interactions,
                    "status": "ok",
                }
            message = request_json(
                "GET",
                GMAIL_GET_URL.format(id=entry["id"]),
                headers=headers,
                params={
                    "format": "metadata",
                    "metadataHeaders": ["From", "To", "Cc", "Subject", "Date"],
                },
            )
            people, interactions = _ingest_gmail_message(con, message, owner=owner)
            seen_messages += 1
            seen_people += people
            seen_interactions += interactions
    return {
        "messages_seen": seen_messages,
        "people_seen": seen_people,
        "interactions_seen": seen_interactions,
        "status": "ok",
    }


def _to_unix(value: str) -> int:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return int(value) if value.isdigit() else 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())


def _gmail_headers(message: dict[str, Any]) -> dict[str, str]:
    payload = message.get("payload") or {}
    out: dict[str, str] = {}
    for header in payload.get("headers") or []:
        name = (header.get("name") or "").lower()
        out[name] = header.get("value") or ""
    return out


def _ingest_gmail_message(con: sqlite3.Connection, message: dict[str, Any], *, owner: str) -> tuple[int, int]:
    headers = _gmail_headers(message)
    senders = parse_addresses(headers.get("from"))
    recipients = parse_addresses(headers.get("to")) + parse_addresses(headers.get("cc"))
    everyone = senders + recipients
    if not everyone:
        return 0, 0
    sender_email = senders[0][1] if senders else None
    direction = "outgoing" if owner and sender_email == owner else "incoming"
    subject = headers.get("subject")
    snippet = message.get("snippet") or ""
    occurred_at = parse_date(headers.get("date"))
    source_ref = message.get("id") or ""

    people = interactions = 0
    for name, email in everyone:
        if owner and email == owner:
            continue
        person_id = upsert_person(con, full_name=name, email=email, confidence=0.65)
        people += 1
        add_interaction(
            con,
            person_id=person_id,
            channel="gmail",
            direction=direction,
            subject=subject or None,
            body_summary=snippet[:500] if snippet else None,
            occurred_at=occurred_at,
            source="gmail_api",
            source_ref=source_ref,
        )
        add_source_fact(
            con,
            person_id=person_id,
            fact_type="gmail_contact",
            fact_value=email,
            source="gmail_api",
            source_ref=source_ref,
            confidence=0.7,
        )
        interactions += 1
        for value_type, description, score in infer_connection_values_from_text(" ".join(p for p in (subject, snippet) if p)):
            add_connection_value(
                con,
                person_id=person_id,
                value_type=value_type,
                description=description,
                score=score,
                evidence=(snippet or subject or "")[:500],
                source="gmail_api",
                source_ref=source_ref,
                confidence=0.4,
            )
    return people, interactions


_GOOGLE_NATIVE_EXPORTS: dict[str, tuple[str, str]] = {
    # mime → (export_mime, default_extension)
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.document": ("text/plain", ".txt"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
}


def _drive_url(record: dict[str, Any], file_id: str, *, export_mime: str | None) -> str:
    if export_mime:
        params = urllib.parse.urlencode({"mimeType": export_mime})
        return f"{DRIVE_EXPORT_URL.format(id=file_id)}?{params}"
    return f"{DRIVE_FILES_URL.format(id=file_id)}?{urllib.parse.urlencode({'alt': 'media'})}"


def download_drive_file(
    con: sqlite3.Connection,
    *,
    file_id: str,
    dest: str | Path | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Download a file from Google Drive using the saved OAuth token.

    Google-native types (Sheets/Docs/Slides) are auto-exported via the
    ``/export`` endpoint; everything else streams via ``alt=media``.
    Returns metadata dict including the resolved on-disk path.
    """
    record = _ensure_token(con)
    headers = _authed_headers(record)
    meta = request_json(
        "GET",
        DRIVE_FILES_URL.format(id=file_id),
        headers=headers,
        params={"fields": "id,name,mimeType,size"},
    )
    name = meta.get("name") or f"drive-{file_id}"
    mime = meta.get("mimeType") or "application/octet-stream"

    export_mime: str | None = None
    extension_hint = ""
    if mime in _GOOGLE_NATIVE_EXPORTS:
        export_mime, extension_hint = _GOOGLE_NATIVE_EXPORTS[mime]

    if dest is None:
        dest_path = Path("exports") / name
        if extension_hint and dest_path.suffix.lower() != extension_hint:
            dest_path = dest_path.with_suffix(dest_path.suffix + extension_hint)
    else:
        dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    url = _drive_url(record, file_id, export_mime=export_mime)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {record['access_token']}")
    req.add_header("User-Agent", "network-chief/0.1 (+https://github.com/Semenka/Network)")
    with urllib.request.urlopen(req, timeout=timeout) as resp, dest_path.open("wb") as out:
        shutil.copyfileobj(resp, out)

    return {
        "id": meta.get("id"),
        "name": name,
        "mime_type": mime,
        "size_bytes": dest_path.stat().st_size,
        "path": str(dest_path),
    }


def revoke_google(con: sqlite3.Connection, *, account: str | None = None) -> int:
    store = TokenStore(con)
    record = store.get(PROVIDER, account)
    if not record:
        return 0
    cid = (record.get("extra") or {}).get("client_id") or os.environ.get("GOOGLE_CLIENT_ID")
    csec = os.environ.get("GOOGLE_CLIENT_SECRET")
    if cid and csec:
        flow = _flow(cid, csec, port=_port(), scopes=record.get("scopes") or DEFAULT_SCOPES)
        flow.revoke(record["access_token"])
    return store.delete(PROVIDER, record["account"])
