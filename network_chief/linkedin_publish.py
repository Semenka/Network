from __future__ import annotations

import os
import sqlite3
from typing import Any, Callable

from .auth.errors import AuthRequired, OAuthError
from .auth.http_util import request_json
from .auth.tokens import TokenStore, is_expired
from .db import now_iso, record_draft_event


UGC_POSTS_URL = "https://api.linkedin.com/v2/ugcPosts"
POSTING_SCOPES = ("w_member_social", "w_organization_social")


class LinkedInPublishError(OAuthError):
    """Raised when official LinkedIn publishing cannot proceed safely."""


RequestFn = Callable[..., dict[str, Any]]


def publish_approved_linkedin(
    con: sqlite3.Connection,
    *,
    draft_id: str,
    confirm_exact_text: str,
    visibility: str = "PUBLIC",
    dry_run: bool = False,
    request_fn: RequestFn | None = None,
) -> dict[str, Any]:
    draft = _load_draft(con, draft_id)
    if draft is None:
        raise LinkedInPublishError(f"Draft not found: {draft_id}")
    if draft["status"] != "approved":
        raise LinkedInPublishError("LinkedIn publish requires an approved draft.")
    if draft["channel"] not in {"linkedin_post", "linkedin"}:
        raise LinkedInPublishError(f"Draft channel must be linkedin_post or linkedin, got {draft['channel']}.")
    if draft["body"] != confirm_exact_text:
        raise LinkedInPublishError("Exact text confirmation does not match the stored draft body.")

    token = TokenStore(con).get("linkedin")
    if token is None:
        raise AuthRequired("Manual publish required: no saved LinkedIn OAuth token.")
    scopes = _scope_set(token.get("scopes") or "")
    if not any(scope in scopes for scope in POSTING_SCOPES):
        raise AuthRequired(
            "Manual publish required: saved LinkedIn token lacks w_member_social or w_organization_social."
        )
    if is_expired(token.get("expires_at")):
        raise AuthRequired("Manual publish required: saved LinkedIn token is expired; re-run auth-linkedin.")
    author_urn = _author_urn(token)
    if not author_urn:
        raise AuthRequired(
            "Manual publish required: set LINKEDIN_AUTHOR_URN or re-authorize LinkedIn with owner identity."
        )

    payload = _ugc_payload(author_urn=author_urn, text=draft["body"], visibility=visibility)
    if dry_run:
        event_id = record_draft_event(
            con,
            draft_id=draft_id,
            event_type="publish_ready",
            reason_code="linkedin_official_api_dry_run",
            metadata={"author_urn": author_urn, "visibility": visibility},
        )
        return {"status": "dry_run", "draft_id": draft_id, "event_id": event_id, "author": author_urn}

    request_fn = request_fn or request_json
    response = request_fn(
        "POST",
        UGC_POSTS_URL,
        headers={
            "Authorization": f"Bearer {token['access_token']}",
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        },
        json_body=payload,
    )
    post_urn = _post_urn(response)
    event_id = record_draft_event(
        con,
        draft_id=draft_id,
        event_type="published",
        reason_code="linkedin_official_api",
        external_ref=post_urn,
        metadata={"author_urn": author_urn, "visibility": visibility, "response": response},
    )
    con.execute("UPDATE drafts SET status = 'published', updated_at = ? WHERE id = ?", (now_iso(), draft_id))
    con.commit()
    return {"status": "published", "draft_id": draft_id, "event_id": event_id, "external_ref": post_urn}


def _load_draft(con: sqlite3.Connection, draft_id: str) -> dict[str, Any] | None:
    row = con.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    return dict(row) if row else None


def _scope_set(scopes: str) -> set[str]:
    return {part.strip() for part in scopes.replace(",", " ").split() if part.strip()}


def _author_urn(token: dict[str, Any]) -> str | None:
    env = os.environ.get("LINKEDIN_AUTHOR_URN")
    if env:
        return env
    extra = token.get("extra") or {}
    if extra.get("author_urn"):
        return str(extra["author_urn"])
    sub = extra.get("sub")
    if sub:
        return f"urn:li:person:{sub}"
    return None


def _ugc_payload(*, author_urn: str, text: str, visibility: str) -> dict[str, Any]:
    return {
        "author": author_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"attributes": [], "text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": visibility},
    }


def _post_urn(response: dict[str, Any]) -> str:
    for key in ("id", "x-restli-id", "x_restli_id", "urn"):
        if response.get(key):
            return str(response[key])
    headers = response.get("_headers")
    if isinstance(headers, dict):
        for key in ("x-restli-id", "X-Restli-Id", "x-restli-id".title()):
            if headers.get(key):
                return str(headers[key])
    return str(response.get("_raw") or "linkedin:published")
