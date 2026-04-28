"""LinkedIn OIDC auth + guided manual export + DMA stub.

LinkedIn does NOT expose first-degree connections to general developer apps.
This module:

* ``auth_linkedin_owner`` — OIDC Sign-In to capture the verified owner
  identity (name + email + LinkedIn ``sub``).
* ``guided_linkedin_export`` — opens the LinkedIn data-download page and
  watches a directory for the resulting ZIP/CSV, then re-runs the existing
  ``import_connections`` / ``import_linkedin_interactions`` importers.
* ``sync_linkedin_dma`` — placeholder for DMA Member Data Portability;
  raises a clear error until LinkedIn approves the app.
"""

from __future__ import annotations

import os
import sqlite3
import time
import webbrowser
import zipfile
from pathlib import Path
from typing import Any

from ..auth.errors import AuthRequired, OAuthError
from ..auth.http_util import request_json
from ..auth.oauth import OAuthFlow
from ..auth.tokens import TokenStore
from ..db import add_source_fact, record_source_run, upsert_person
from .linkedin import import_connections, import_linkedin_interactions


PROVIDER = "linkedin"
AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
DOWNLOAD_DATA_URL = "https://www.linkedin.com/mypreferences/d/download-my-data"

OIDC_SCOPES = "openid profile email"
DMA_SCOPES = "r_dma_portability_3rd_party"


class LinkedInDMARequired(OAuthError):
    """Raised when DMA-only sync paths are called without DMA approval."""


def _port() -> int:
    return int(os.environ.get("NETWORK_CHIEF_OAUTH_PORT_LINKEDIN", "47320"))


def _flow(client_id: str, client_secret: str, *, port: int, scopes: str = OIDC_SCOPES) -> OAuthFlow:
    return OAuthFlow(
        provider=PROVIDER,
        client_id=client_id,
        client_secret=client_secret,
        auth_url=AUTH_URL,
        token_url=TOKEN_URL,
        scopes=scopes,
        redirect_port=port,
        # LinkedIn historically rejects PKCE for confidential clients; use client secret.
        use_pkce=False,
        use_basic_auth=False,
    )


def auth_linkedin_owner(
    con: sqlite3.Connection,
    *,
    client_id: str | None = None,
    client_secret: str | None = None,
    open_browser: bool = True,
) -> dict[str, Any]:
    cid = client_id or os.environ.get("LINKEDIN_CLIENT_ID")
    csec = client_secret or os.environ.get("LINKEDIN_CLIENT_SECRET")
    if not cid or not csec:
        raise AuthRequired(
            "LINKEDIN_CLIENT_ID/LINKEDIN_CLIENT_SECRET not set. Register an app at "
            "https://www.linkedin.com/developers/apps and add redirect URI "
            f"http://127.0.0.1:{_port()}/callback. Request the 'Sign In with LinkedIn using OpenID Connect' product."
        )
    flow = _flow(cid, csec, port=_port(), scopes=OIDC_SCOPES)
    token = flow.authorize_blocking(open_browser=open_browser)
    info = request_json(
        "GET",
        USERINFO_URL,
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )
    sub = info.get("sub") or "linkedin"
    name = info.get("name") or info.get("given_name") or "LinkedIn Owner"
    email = (info.get("email") or "").lower() or None

    person_id = upsert_person(
        con,
        full_name=name,
        email=email,
        notes="OWNER PROFILE (LinkedIn OIDC)",
        confidence=0.95,
    )
    add_source_fact(
        con,
        person_id=person_id,
        fact_type="linkedin_owner_identity",
        fact_value=email or sub,
        source="linkedin_oidc",
        source_ref=sub,
        confidence=0.95,
    )

    store = TokenStore(con)
    store.save(
        provider=PROVIDER,
        account=email or sub,
        access_token=token["access_token"],
        refresh_token=token.get("refresh_token"),
        expires_at=token.get("_expires_at"),
        scopes=token.get("scope") or OIDC_SCOPES,
        token_type=token.get("token_type", "Bearer"),
        extra={"client_id": cid, "sub": sub, "name": name},
    )
    return {"account": email or sub, "name": name, "sub": sub}


def sync_linkedin_dma(con: sqlite3.Connection, *, limit: int | None = None) -> dict[str, Any]:
    """Placeholder for the LinkedIn DMA Member Data Portability API.

    LinkedIn requires explicit app whitelisting before granting the
    ``r_dma_portability_3rd_party`` scope. Once approved, replace this stub
    with the documented flow:

    1. Re-run OIDC with ``DMA_SCOPES``.
    2. POST to LinkedIn's member-archive trigger endpoint, poll until ready.
    3. Download the archive, extract, and re-route to ``import_connections``.
    """

    if os.environ.get("LINKEDIN_DMA_ENABLED") != "1":
        raise LinkedInDMARequired(
            "LinkedIn DMA scope not granted. Apply for the Member Data Portability product at "
            "https://www.linkedin.com/developers/apps and set LINKEDIN_DMA_ENABLED=1 once approved. "
            "Until then, use: network-chief sync-linkedin --guided-export"
        )
    raise LinkedInDMARequired(
        "DMA flow not yet implemented. Stub kept so an approved user can wire it up "
        "in network_chief/importers/linkedin_api.py:sync_linkedin_dma."
    )


def guided_linkedin_export(
    con: sqlite3.Connection,
    *,
    watch_dir: str | Path = "exports",
    timeout_s: float = 900.0,
    open_browser: bool = True,
) -> dict[str, Any]:
    """Open LinkedIn's data-download page and ingest CSVs as they appear."""
    watch = Path(watch_dir)
    watch.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    if open_browser:
        try:
            webbrowser.open(DOWNLOAD_DATA_URL, new=1, autoraise=True)
        except Exception:  # pragma: no cover
            pass
    print("[linkedin] Steps:")
    print("  1. On the LinkedIn page, choose 'Want something in particular?' → select")
    print("     Connections + Messages, then click 'Request archive'.")
    print("  2. Wait for the email from LinkedIn (usually <10 min).")
    print(f"  3. Save the ZIP into {watch.resolve()}/ — this command will pick it up automatically.")

    deadline = started_at + timeout_s
    seen: set[Path] = set()
    stats: dict[str, Any] = {"connections": None, "interactions": None, "files": []}

    while time.time() < deadline:
        candidates = []
        for entry in sorted(watch.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if entry.is_file() and entry.stat().st_mtime >= started_at and entry not in seen:
                candidates.append(entry)
        for path in candidates:
            seen.add(path)
            extracted = _maybe_extract(path, watch / f"linkedin_{int(started_at)}")
            connections = _find_first(extracted, ("Connections.csv", "connections.csv"))
            messages = _find_first(extracted, ("messages.csv", "Messages.csv"))
            if connections:
                conn_stats = import_connections(con, str(connections))
                record_source_run(
                    con, source="linkedin_connections", source_ref=str(connections), status="ok", stats=conn_stats
                )
                stats["connections"] = conn_stats
                stats["files"].append(str(connections))
            if messages:
                msg_stats = import_linkedin_interactions(con, str(messages))
                record_source_run(
                    con, source="linkedin_interactions", source_ref=str(messages), status="ok", stats=msg_stats
                )
                stats["interactions"] = msg_stats
                stats["files"].append(str(messages))
            if connections or messages:
                stats["status"] = "ok"
                return stats
        time.sleep(5)

    stats["status"] = "timeout"
    stats["hint"] = (
        "No new LinkedIn export detected. Re-run network-chief sync-linkedin --guided-export "
        "once the archive lands in your downloads folder."
    )
    return stats


def _maybe_extract(path: Path, dest: Path) -> list[Path]:
    if path.suffix.lower() != ".zip":
        return [path]
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as zf:
        zf.extractall(dest)
    return [p for p in dest.rglob("*") if p.is_file()]


def _find_first(paths: list[Path], names: tuple[str, ...]) -> Path | None:
    targets = {name.lower() for name in names}
    for path in paths:
        if path.name.lower() in targets:
            return path
    return None
