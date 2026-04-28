from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import threading
import urllib.parse
import webbrowser
from typing import Any

from .errors import LoopbackServerError, OAuthError
from .http_util import request_json
from .tokens import expires_at_from_seconds


def _b64url_no_pad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` per RFC 7636."""
    verifier = _b64url_no_pad(secrets.token_bytes(64))
    challenge = _b64url_no_pad(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


_SUCCESS_HTML = (
    b"<!doctype html><html><head><meta charset='utf-8'>"
    b"<title>Network Chief</title></head><body style='font-family:system-ui;padding:40px'>"
    b"<h1>Network Chief authorized.</h1>"
    b"<p>You can close this tab and return to your terminal.</p></body></html>"
)
_ERROR_HTML = (
    b"<!doctype html><html><head><meta charset='utf-8'>"
    b"<title>Network Chief</title></head><body style='font-family:system-ui;padding:40px'>"
    b"<h1>Authorization failed.</h1>"
    b"<p>Check the terminal for details, then try again.</p></body></html>"
)


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """One-shot redirect handler. Stores ``code``, ``state``, ``error`` on server."""

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Not found.")
            return
        params = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        self.server.callback_params = params  # type: ignore[attr-defined]
        if params.get("error"):
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_ERROR_HTML)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_SUCCESS_HTML)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (override)
        # Silence default stderr logging; tokens never appear in stdout/stderr.
        return


class OAuthFlow:
    """Authorization-Code (with optional PKCE) over a loopback redirect URI."""

    def __init__(
        self,
        *,
        provider: str,
        client_id: str,
        client_secret: str | None,
        auth_url: str,
        token_url: str,
        scopes: str,
        redirect_port: int,
        revoke_url: str | None = None,
        extra_auth_params: dict[str, str] | None = None,
        use_pkce: bool = True,
        use_basic_auth: bool = False,
    ) -> None:
        if not client_id:
            raise OAuthError(f"{provider}: client_id required")
        self.provider = provider
        self.client_id = client_id
        self.client_secret = client_secret
        self.auth_url = auth_url
        self.token_url = token_url
        self.revoke_url = revoke_url
        self.scopes = scopes
        self.redirect_port = redirect_port
        self.extra_auth_params = extra_auth_params or {}
        self.use_pkce = use_pkce
        self.use_basic_auth = use_basic_auth

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.redirect_port}/callback"

    def authorize_blocking(
        self,
        *,
        open_browser: bool = True,
        timeout_s: float = 300.0,
    ) -> dict[str, Any]:
        verifier, challenge = pkce_pair() if self.use_pkce else ("", "")
        state = secrets.token_urlsafe(32)
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.scopes,
            "state": state,
        }
        if self.use_pkce:
            params["code_challenge"] = challenge
            params["code_challenge_method"] = "S256"
        params.update(self.extra_auth_params)
        authorize_url = f"{self.auth_url}?{urllib.parse.urlencode(params)}"

        server = http.server.HTTPServer(("127.0.0.1", self.redirect_port), _CallbackHandler)
        server.callback_params = None  # type: ignore[attr-defined]
        server.timeout = timeout_s
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        if open_browser:
            try:
                webbrowser.open(authorize_url, new=1, autoraise=True)
            except Exception:  # pragma: no cover
                pass
        print(f"[{self.provider}] Authorize at: {authorize_url}")
        print(f"[{self.provider}] Listening on {self.redirect_uri} for up to {int(timeout_s)}s.")

        thread.join(timeout=timeout_s + 5)
        server.server_close()
        cb = getattr(server, "callback_params", None)
        if not cb:
            raise LoopbackServerError(f"{self.provider}: timed out waiting for callback")
        if cb.get("error"):
            raise OAuthError(
                f"{self.provider}: {cb['error']} {cb.get('error_description', '')}".strip()
            )
        if cb.get("state") != state:
            raise OAuthError(f"{self.provider}: state mismatch (CSRF check failed)")
        code = cb.get("code")
        if not code:
            raise OAuthError(f"{self.provider}: no code in callback")

        token = self._exchange_code(code, verifier)
        token.setdefault("scope", self.scopes)
        token["_expires_at"] = expires_at_from_seconds(token.get("expires_in"))
        return token

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
        }
        headers: dict[str, str] = {}
        if self.use_basic_auth and self.client_secret:
            headers["Authorization"] = self._basic_auth_header()
        elif self.client_secret:
            data["client_secret"] = self.client_secret
        token = request_json("POST", self.token_url, headers=headers, data=data)
        token["_expires_at"] = expires_at_from_seconds(token.get("expires_in"))
        return token

    def revoke(self, token: str) -> None:
        if not self.revoke_url:
            return
        data = {"token": token, "client_id": self.client_id}
        headers: dict[str, str] = {}
        if self.use_basic_auth and self.client_secret:
            headers["Authorization"] = self._basic_auth_header()
        elif self.client_secret:
            data["client_secret"] = self.client_secret
        try:
            request_json("POST", self.revoke_url, headers=headers, data=data, max_retries=1)
        except Exception:
            # Best-effort: provider revocation can fail silently.
            return

    def _exchange_code(self, code: str, verifier: str) -> dict[str, Any]:
        data: dict[str, Any] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
        }
        if self.use_pkce:
            data["code_verifier"] = verifier
        headers: dict[str, str] = {}
        if self.use_basic_auth and self.client_secret:
            headers["Authorization"] = self._basic_auth_header()
        elif self.client_secret:
            data["client_secret"] = self.client_secret
        return request_json("POST", self.token_url, headers=headers, data=data)

    def _basic_auth_header(self) -> str:
        raw = f"{self.client_id}:{self.client_secret or ''}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")
