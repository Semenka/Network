from __future__ import annotations

import base64
import json
import os
import sqlite3
import urllib.error
import urllib.request
from email.message import EmailMessage
from typing import Any

from .db import rows_to_dicts
from .drafts import apply_draft_event


class OutboundSafetyError(ValueError):
    pass


def prepare_gmail_send_payload(
    con: sqlite3.Connection,
    *,
    draft_id: str,
    confirm_exact_text: str,
) -> dict[str, Any]:
    row = con.execute(
        """
        SELECT drafts.*, people.full_name, people.primary_email
          FROM drafts
          LEFT JOIN people ON people.id = drafts.person_id
         WHERE drafts.id = ?
        """,
        (draft_id,),
    ).fetchone()
    if not row:
        raise OutboundSafetyError(f"Draft not found: {draft_id}")
    draft = dict(row)
    if draft["channel"] != "gmail":
        raise OutboundSafetyError(f"Draft {draft_id} is channel={draft['channel']}; only gmail drafts can be sent here.")
    if draft["status"] != "approved":
        raise OutboundSafetyError(f"Draft {draft_id} must be approved before send; current status is {draft['status']}.")
    if confirm_exact_text != draft["body"]:
        raise OutboundSafetyError("Exact text confirmation did not match the stored draft body.")
    recipient = draft.get("primary_email") or _first_gmail_account(con, draft.get("person_id"))
    if not recipient:
        raise OutboundSafetyError(f"Draft {draft_id} has no Gmail recipient.")
    return {
        "draft_id": draft_id,
        "to": recipient,
        "recipient_name": draft.get("full_name"),
        "subject": draft.get("subject") or "",
        "body": draft["body"],
    }


def send_approved_gmail(
    con: sqlite3.Connection,
    *,
    draft_id: str,
    confirm_exact_text: str,
    access_token: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    payload = prepare_gmail_send_payload(con, draft_id=draft_id, confirm_exact_text=confirm_exact_text)
    token = access_token or os.environ.get("GMAIL_ACCESS_TOKEN")
    if dry_run or not token:
        event_id = apply_draft_event(
            con,
            draft_id=draft_id,
            event_type="send_ready",
            reason_code="needs_gmail_token" if not token else "dry_run",
            metadata={"to": payload["to"], "subject": payload["subject"]},
        )
        return {"status": "dry_run" if dry_run else "needs_gmail_token", "event_id": event_id, "payload": payload}

    gmail_draft = _create_gmail_draft(token, payload)
    sent = _send_gmail_draft(token, str(gmail_draft["id"]))
    event_id = apply_draft_event(
        con,
        draft_id=draft_id,
        event_type="sent",
        external_ref=str(sent.get("id") or gmail_draft.get("id")),
        metadata={"gmail_draft": gmail_draft, "gmail_sent": sent, "to": payload["to"]},
    )
    return {"status": "sent", "event_id": event_id, "gmail_draft": gmail_draft, "gmail_sent": sent}


def _first_gmail_account(con: sqlite3.Connection, person_id: str | None) -> str | None:
    if not person_id:
        return None
    rows = con.execute(
        """
        SELECT account_ref
          FROM channel_accounts
         WHERE person_id = ?
           AND channel = 'gmail'
         ORDER BY send_enabled DESC, updated_at DESC
         LIMIT 1
        """,
        (person_id,),
    ).fetchall()
    accounts = rows_to_dicts(rows)
    return str(accounts[0]["account_ref"]) if accounts else None


def _create_gmail_draft(token: str, payload: dict[str, Any]) -> dict[str, Any]:
    message = EmailMessage()
    message["To"] = payload["to"]
    message["Subject"] = payload["subject"]
    message.set_content(payload["body"])
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    return _gmail_json_request(
        token,
        "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
        {"message": {"raw": raw}},
    )


def _send_gmail_draft(token: str, gmail_draft_id: str) -> dict[str, Any]:
    return _gmail_json_request(
        token,
        "https://gmail.googleapis.com/gmail/v1/users/me/drafts/send",
        {"id": gmail_draft_id},
    )


def _gmail_json_request(token: str, url: str, body: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OutboundSafetyError(f"Gmail API request failed: HTTP {exc.code} {detail}") from exc

