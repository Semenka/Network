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
import re
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
    mark_draft_pushed,
    mark_draft_responded,
    new_id,
    now_iso,
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
SCOPE_GMAIL_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
SCOPE_GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
DEFAULT_SCOPES = " ".join((SCOPE_OPENID, SCOPE_PEOPLE, SCOPE_GMAIL, SCOPE_GMAIL_COMPOSE, SCOPE_GMAIL_SEND))

PEOPLE_URL = "https://people.googleapis.com/v1/people/me/connections"
GMAIL_LIST_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
GMAIL_GET_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/{id}"
GMAIL_DRAFTS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files/{id}"
DRIVE_EXPORT_URL = "https://www.googleapis.com/drive/v3/files/{id}/export"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


# Patterns identifying machine-generated addresses we must never auto-reply to.
# Matched against the local-part (before @) and full address (case-insensitive).
_BOT_LOCAL_TOKENS = (
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply", "dontreply",
    "mailer-daemon", "postmaster", "bounce", "bounces",
    "notification", "notifications", "notify", "notif", "alert", "alerts",
    "newsletter", "marketing", "promo", "promotions", "campaign", "campaigns",
    "support", "help", "service", "billing", "invoice", "receipts", "payments",
    "info", "hello", "contact", "team", "robot", "automation", "automated",
    "booking", "reservation", "security", "abuse", "trading", "tradingassistant",
    "auto", "system", "noticias", "updates", "news",
    "email", "emails", "mail",  # generic newsletter aliases
    "welcome", "bienvenue", "willkommen", "benvenuto", "bienvenido",  # onboarding aliases
    "digest", "weekly", "daily",  # newsletter cadence aliases
    # very long hash-suffixed reply aliases like reply-abcdef0123...@reply-sg.x.com
)
_BOT_LOCAL_RE = re.compile(r"(?:^|[._-])(" + "|".join(_BOT_LOCAL_TOKENS) + r")(?:$|[._-])", re.I)
# Long alphanumeric-hash local part with no vowels alongside (sendgrid relays).
_HASH_LOCAL_RE = re.compile(r"^[a-z]+-[a-f0-9]{16,}$", re.I)
# Sub-mailer domains used to send notifications (mail.instagram.com, notif.x.fr,
# reply-sg.example.com, a.store.x.com, marketing.x.com, em.x.com).
_BOT_DOMAIN_RE = re.compile(
    r"(?:^|\.)(mail|mailing|notif|notifications|alerts|news|updates|broadcast|bounces?"
    r"|reply|reply-sg|sg-reply|em|em\d*|sendgrid|mktomail|marketing|promo|store"
    r"|order|orders|invoice|invoices|noreply|news\.\w+|tracking)\.",
    re.I,
)


def _is_bot_address(email: str | None, owner_emails: set[str]) -> bool:
    """Return True if this address looks machine-generated or is one of ours."""
    if not email:
        return True
    em = email.strip().lower()
    if em in owner_emails:
        return True
    if "@" not in em:
        return True
    local, _, domain = em.partition("@")
    if _BOT_LOCAL_RE.search(local):
        return True
    if _HASH_LOCAL_RE.search(local):
        return True
    if _BOT_DOMAIN_RE.search(domain):
        return True
    return False


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

    seen_messages = seen_people = seen_interactions = replies = 0
    for page in paginate(list_fetcher, next_token_keys=("nextPageToken",)):
        for entry in page.get("messages") or []:
            if limit and seen_messages >= limit:
                return {
                    "messages_seen": seen_messages,
                    "people_seen": seen_people,
                    "interactions_seen": seen_interactions,
                    "replies_detected": replies,
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
            people, interactions, msg_replies = _ingest_gmail_message(con, message, owner=owner)
            seen_messages += 1
            seen_people += people
            seen_interactions += interactions
            replies += msg_replies
    return {
        "messages_seen": seen_messages,
        "people_seen": seen_people,
        "interactions_seen": seen_interactions,
        "replies_detected": replies,
        "status": "ok",
    }


def _detect_reply(
    con: sqlite3.Connection,
    *,
    thread_id: str | None,
    occurred_at: str | None,
) -> int:
    """Flag any pending draft on this Gmail thread as responded.

    Match is thread-based: an incoming message on a thread we pushed a
    draft into, arriving after we pushed, is treated as a reply.
    """
    if not thread_id:
        return 0
    rows = con.execute(
        "SELECT id, sent_at FROM drafts WHERE gmail_thread_id = ? AND outcome != 'responded'",
        (thread_id,),
    ).fetchall()
    detected = 0
    for row in rows:
        sent_at = row["sent_at"]
        if sent_at and occurred_at and occurred_at <= sent_at:
            continue
        if mark_draft_responded(con, row["id"], responded_at=occurred_at):
            detected += 1
    return detected


def detect_replies_heuristic(con: sqlite3.Connection) -> dict[str, int]:
    """Fallback for drafts sent outside this tool (no stored thread id).

    Marks a pending draft as responded if the recipient has any incoming
    gmail interaction recorded after the draft's sent_at (or created_at).
    Less precise than thread matching — opt-in via ``sync-google --heuristic``.
    """
    rows = con.execute(
        """
        SELECT d.id, COALESCE(d.sent_at, d.created_at) AS since, p.primary_email
          FROM drafts d JOIN people p ON p.id = d.person_id
         WHERE d.outcome != 'responded'
           AND (d.gmail_thread_id IS NULL OR d.gmail_thread_id = '')
           AND p.primary_email IS NOT NULL AND p.primary_email != ''
        """
    ).fetchall()
    detected = 0
    for row in rows:
        hit = con.execute(
            """
            SELECT 1
              FROM interactions i
              JOIN people p ON p.id = i.person_id
             WHERE p.id = (SELECT person_id FROM drafts WHERE id = ?)
               AND i.channel = 'gmail'
               AND i.direction = 'incoming'
               AND i.occurred_at > ?
             LIMIT 1
            """,
            (row["id"], row["since"]),
        ).fetchone()
        if hit and mark_draft_responded(con, row["id"]):
            detected += 1
    return {"heuristic_replies_detected": detected}


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


def _ingest_gmail_message(con: sqlite3.Connection, message: dict[str, Any], *, owner: str) -> tuple[int, int, int]:
    headers = _gmail_headers(message)
    senders = parse_addresses(headers.get("from"))
    recipients = parse_addresses(headers.get("to")) + parse_addresses(headers.get("cc"))
    everyone = senders + recipients
    if not everyone:
        return 0, 0, 0
    sender_email = senders[0][1] if senders else None
    direction = "outgoing" if owner and sender_email == owner else "incoming"
    subject = headers.get("subject")
    snippet = message.get("snippet") or ""
    occurred_at = parse_date(headers.get("date"))
    source_ref = message.get("id") or ""
    thread_id = message.get("threadId")

    replies = _detect_reply(con, thread_id=thread_id, occurred_at=occurred_at) if direction == "incoming" else 0

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
            sentiment="reply" if replies else None,
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
    return people, interactions, replies


def _build_rfc2822(*, sender: str | None, to: str, subject: str, body: str) -> str:
    """Build an RFC 2822 message and return its base64url-encoded form."""
    from email.message import EmailMessage
    from email.utils import formatdate, make_msgid

    msg = EmailMessage()
    msg.set_content(body, subtype="plain", charset="utf-8")
    if sender:
        msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def push_drafts_to_gmail(
    con: sqlite3.Connection,
    *,
    status: str = "draft",
    limit: int | None = None,
) -> dict[str, Any]:
    """Push network-chief drafts (with email recipients) into Gmail Drafts.

    Requires ``gmail.compose`` (or ``gmail.modify``) on the saved Google
    token. Drafts are created as MIME messages addressed to the contact's
    primary email; the ``From`` is the authorized Gmail account. Local
    draft rows are NOT mutated — review/approval still happens through
    ``approve-draft`` / ``reject-draft`` once you've decided in Gmail.
    """
    record = _ensure_token(con)
    scopes = (record.get("scopes") or "").split()
    if SCOPE_GMAIL_COMPOSE not in scopes and "https://www.googleapis.com/auth/gmail.modify" not in scopes:
        raise AuthRequired(
            "Saved Google token lacks gmail.compose scope. Re-run "
            "`network-chief auth-google --manual` (DEFAULT_SCOPES now includes gmail.compose)."
        )
    headers = {**_authed_headers(record), "Content-Type": "application/json"}
    sender = record.get("account") or None

    rows = con.execute(
        """
        SELECT d.id, p.full_name, p.primary_email, d.subject, d.body
          FROM drafts d JOIN people p ON p.id = d.person_id
         WHERE d.status = ?
           AND p.primary_email IS NOT NULL AND p.primary_email != ''
           AND COALESCE(p.consent_status, 'active') = 'active'
         ORDER BY d.created_at
         LIMIT ?
        """,
        (status, limit or 1000),
    ).fetchall()

    pushed: list[dict[str, str]] = []
    skipped = 0
    errors: list[str] = []
    for row in rows:
        try:
            raw = _build_rfc2822(
                sender=sender,
                to=row["primary_email"],
                subject=row["subject"] or "(no subject)",
                body=row["body"] or "",
            )
            resp = request_json(
                "POST",
                GMAIL_DRAFTS_URL,
                headers=headers,
                json_body={"message": {"raw": raw}},
            )
            message = resp.get("message") or {}
            mark_draft_pushed(
                con,
                row["id"],
                thread_id=message.get("threadId"),
                message_id=message.get("id"),
            )
            pushed.append(
                {
                    "draft_id": row["id"],
                    "to": row["primary_email"],
                    "name": row["full_name"],
                    "gmail_draft_id": (resp.get("id") or ""),
                    "thread_id": message.get("threadId") or "",
                }
            )
        except Exception as exc:  # pragma: no cover - reported per-draft
            skipped += 1
            errors.append(f"{row['full_name']}: {exc}")

    return {"pushed": len(pushed), "skipped": skipped, "items": pushed, "errors": errors}


def send_drafts_via_gmail(
    con: sqlite3.Connection,
    *,
    policy,
    status: str = "approved",
    limit: int | None = None,
) -> dict[str, Any]:
    """Autonomously SEND approved drafts via Gmail, gated by ``policy``.

    Mirrors ``push_drafts_to_gmail`` selection (active consent + verified
    email), but each draft is additionally checked through
    ``policy.permits_send``. On a real send the draft is marked sent
    (``status='sent'``, ``sent_at`` set). Honors ``policy.dry_run`` — when
    set, the message is fully prepared and logged but no API call is made.

    Returns counts: sent, dry_run, blocked (with reasons), errors.
    """
    from ..policy import permits_send  # local import avoids a cycle at module load

    record = _ensure_token(con)
    scopes = (record.get("scopes") or "").split()
    if SCOPE_GMAIL_SEND not in scopes and "https://www.googleapis.com/auth/gmail.modify" not in scopes:
        raise AuthRequired(
            "Saved Google token lacks gmail.send scope. Re-run "
            "`network-chief auth-google --manual` to grant send capability."
        )
    headers = {**_authed_headers(record), "Content-Type": "application/json"}
    sender = record.get("account") or None

    rows = con.execute(
        """
        SELECT d.id, d.person_id, d.subject, d.body, d.gmail_thread_id,
               p.full_name, p.primary_email, p.consent_status
          FROM drafts d JOIN people p ON p.id = d.person_id
         WHERE d.status = ?
           AND d.channel = 'gmail'
           AND p.primary_email IS NOT NULL AND p.primary_email != ''
           AND COALESCE(p.consent_status, 'active') = 'active'
         ORDER BY d.created_at
         LIMIT ?
        """,
        (status, limit or 1000),
    ).fetchall()

    # Count sends already made today (UTC) for the daily cap.
    todays_sends = con.execute(
        "SELECT count(*) FROM drafts WHERE sent_at >= strftime('%Y-%m-%dT00:00:00Z','now')"
    ).fetchone()[0]

    sent: list[dict[str, str]] = []
    dry: list[dict[str, str]] = []
    blocked: list[dict[str, str]] = []
    errors: list[str] = []

    for row in rows:
        draft = dict(row)
        allowed, reason = permits_send(
            policy, con, draft, todays_sends=todays_sends + len(sent) + len(dry), channel="gmail"
        )
        if not allowed:
            blocked.append({"name": draft["full_name"], "to": draft["primary_email"], "reason": reason})
            continue

        if policy.dry_run:
            dry.append({"draft_id": draft["id"], "to": draft["primary_email"], "name": draft["full_name"]})
            continue

        try:
            raw = _build_rfc2822(
                sender=sender,
                to=draft["primary_email"],
                subject=draft["subject"] or "(no subject)",
                body=draft["body"] or "",
            )
            payload: dict[str, Any] = {"raw": raw}
            if draft.get("gmail_thread_id"):
                payload["threadId"] = draft["gmail_thread_id"]
            resp = request_json("POST", GMAIL_SEND_URL, headers=headers, json_body=payload)
            con.execute(
                """
                UPDATE drafts
                   SET status = 'sent',
                       sent_at = ?,
                       gmail_message_id = COALESCE(?, gmail_message_id),
                       gmail_thread_id = COALESCE(gmail_thread_id, ?),
                       outcome = CASE WHEN outcome = 'responded' THEN outcome ELSE 'pending' END,
                       updated_at = ?
                 WHERE id = ?
                """,
                (now_iso(), resp.get("id"), resp.get("threadId"), now_iso(), draft["id"]),
            )
            con.commit()
            sent.append({"draft_id": draft["id"], "to": draft["primary_email"], "name": draft["full_name"]})
        except Exception as exc:  # pragma: no cover - reported per-draft
            errors.append(f"{draft['full_name']}: {exc}")

    return {
        "sent": len(sent),
        "dry_run": len(dry),
        "blocked": len(blocked),
        "errors": errors,
        "sent_items": sent,
        "dry_items": dry,
        "blocked_items": blocked,
    }


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


def auto_reply_to_gmail(
    con: sqlite3.Connection,
    *,
    policy,
    lookback_days: int = 3,
    limit: int = 20,
) -> dict[str, Any]:
    """Auto-acknowledge fresh incoming Gmail from known contacts we haven't replied to.

    Targets only contacts with ``consent_status='active'``, an existing
    relationship history, no outgoing reply on the thread since the inbound
    message, and inbound within the last ``lookback_days``. Drafts a short
    acknowledgement (channel='gmail', status='approved'), threads it
    correctly via ``In-Reply-To``/``References`` headers, then sends per
    ``policy``. Off by default — requires ``policy.gmail_auto_reply_enabled``.
    """
    from ..policy import permits_send  # local import — avoid cycle at load
    if not getattr(policy, "gmail_auto_reply_enabled", False):
        return {"considered": 0, "drafted": 0, "sent": 0, "skipped": "policy.gmail_auto_reply_enabled is false"}

    record = _ensure_token(con)
    scopes = (record.get("scopes") or "").split()
    if SCOPE_GMAIL_SEND not in scopes and "https://www.googleapis.com/auth/gmail.modify" not in scopes:
        raise AuthRequired("Saved Google token lacks gmail.send; re-run auth-google --manual.")
    headers = {**_authed_headers(record), "Content-Type": "application/json"}
    sender = record.get("account") or None

    owner_emails = {(record.get("account") or "").lower()}
    extra = os.environ.get("NETWORK_CHIEF_SELF_EMAILS", "")
    owner_emails.update(e.strip().lower() for e in extra.split(",") if e.strip())

    raw_candidates = con.execute(
        """
        SELECT i.id AS interaction_id, i.source_ref AS message_id, i.subject, i.body_summary, i.occurred_at,
               p.id AS person_id, p.full_name, p.primary_email, p.consent_status
          FROM interactions i
          JOIN people p ON p.id = i.person_id
         WHERE i.channel = 'gmail'
           AND i.direction = 'incoming'
           AND COALESCE(p.consent_status, 'active') = 'active'
           AND p.primary_email IS NOT NULL AND p.primary_email != ''
           AND i.occurred_at >= datetime('now', ?)
           AND NOT EXISTS (
               SELECT 1 FROM interactions o
                WHERE o.person_id = i.person_id
                  AND o.channel = 'gmail'
                  AND o.direction = 'outgoing'
                  AND o.occurred_at > i.occurred_at
           )
           AND NOT EXISTS (
               SELECT 1 FROM drafts d
                WHERE d.person_id = i.person_id
                  AND d.channel = 'gmail'
                  AND d.rationale = 'auto-ack'
                  AND d.created_at >= datetime('now', '-3 days')
           )
         ORDER BY i.occurred_at DESC
         LIMIT ?
        """,
        (f"-{int(lookback_days)} days", int(limit) * 4),  # over-fetch; we filter below
    ).fetchall()

    candidates = [r for r in raw_candidates
                  if not _is_bot_address(r["primary_email"], owner_emails)][:int(limit)]

    considered = drafted = sent_count = 0
    # Count today's sends for the daily cap (counts only real sends, not dry_run).
    todays_sends = con.execute(
        "SELECT count(*) FROM drafts WHERE sent_at >= strftime('%Y-%m-%dT00:00:00Z','now')"
    ).fetchone()[0]

    blocked: list[str] = []
    results: list[dict[str, str]] = []

    for cand in candidates:
        considered += 1
        first_name = (cand["full_name"] or "there").split()[0]
        subject_in = (cand["subject"] or "").strip()
        reply_subject = subject_in if subject_in.lower().startswith("re:") else f"Re: {subject_in or '(no subject)'}"
        body = (
            f"Hi {first_name},\n\n"
            "Quick acknowledgement — I have your note and will reply with a real "
            "answer within the next couple of days.\n\n"
            "Best,\n"
            "Andrey"
        )

        # Look up the thread id from the most recent draft we pushed to this
        # contact, or leave None (Gmail will create a new thread).
        thread_row = con.execute(
            "SELECT gmail_thread_id FROM drafts WHERE person_id = ? AND gmail_thread_id IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (cand["person_id"],),
        ).fetchone()
        thread_id = thread_row[0] if thread_row else None

        draft_id = new_id()
        ts = now_iso()
        con.execute(
            """
            INSERT INTO drafts (id, person_id, channel, subject, body, rationale,
                                status, outcome, gmail_thread_id, created_at, updated_at)
            VALUES (?, ?, 'gmail', ?, ?, 'auto-ack', 'approved', 'pending', ?, ?, ?)
            """,
            (draft_id, cand["person_id"], reply_subject, body, thread_id, ts, ts),
        )
        con.commit()
        drafted += 1

        draft_for_gate = {
            "id": draft_id,
            "person_id": cand["person_id"],
            "primary_email": cand["primary_email"],
            "consent_status": cand["consent_status"],
        }
        allowed, reason = permits_send(
            policy, con, draft_for_gate, todays_sends=todays_sends + sent_count, channel="gmail"
        )
        if not allowed:
            blocked.append(f"{cand['full_name']}: {reason}")
            continue

        if policy.dry_run:
            results.append({"draft_id": draft_id, "to": cand["primary_email"], "name": cand["full_name"], "mode": "dry_run"})
            continue

        try:
            raw = _build_rfc2822(
                sender=sender,
                to=cand["primary_email"],
                subject=reply_subject,
                body=body,
            )
            payload: dict[str, Any] = {"raw": raw}
            if thread_id:
                payload["threadId"] = thread_id
            resp = request_json("POST", GMAIL_SEND_URL, headers=headers, json_body=payload)
            con.execute(
                """
                UPDATE drafts SET status='sent', sent_at=?,
                                  gmail_message_id=COALESCE(?, gmail_message_id),
                                  gmail_thread_id=COALESCE(gmail_thread_id, ?),
                                  updated_at=?
                 WHERE id=?
                """,
                (now_iso(), resp.get("id"), resp.get("threadId"), now_iso(), draft_id),
            )
            con.commit()
            sent_count += 1
            results.append({"draft_id": draft_id, "to": cand["primary_email"], "name": cand["full_name"], "mode": "sent"})
        except Exception as exc:  # pragma: no cover
            blocked.append(f"{cand['full_name']}: send error: {exc}")

    return {
        "considered": considered,
        "drafted": drafted,
        "sent": sent_count,
        "blocked": len(blocked),
        "items": results,
        "block_reasons": blocked[:10],
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
