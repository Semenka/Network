import base64
import hashlib
import json
import threading
import time
import unittest
import urllib.request
from contextlib import contextmanager
from io import BytesIO
from unittest import mock
from urllib.error import HTTPError

from network_chief.auth import http_util
from network_chief.auth.errors import LoopbackServerError, OAuthError, RateLimited
from network_chief.auth.http_util import request_json
from network_chief.auth.oauth import OAuthFlow, pkce_pair


class PKCETest(unittest.TestCase):
    def test_challenge_is_sha256_of_verifier(self) -> None:
        verifier, challenge = pkce_pair()
        recomputed = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(challenge, recomputed)
        self.assertNotIn("=", challenge)


class FakeResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None):
        self.status = status
        self._body = body
        self.headers = headers or {"Content-Type": "application/json"}

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._body


@contextmanager
def patch_urlopen(handler):
    with mock.patch.object(urllib.request, "urlopen", handler), mock.patch.object(http_util.request, "urlopen", handler):
        yield


class RequestJsonTest(unittest.TestCase):
    def test_returns_json(self) -> None:
        body = json.dumps({"hello": "world"}).encode()
        with patch_urlopen(lambda req, timeout=30: FakeResponse(200, body)):
            self.assertEqual(request_json("GET", "https://example.com"), {"hello": "world"})

    def test_429_then_success(self) -> None:
        attempts = {"n": 0}

        def handler(req, timeout=30):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise HTTPError(req.full_url, 429, "Too Many", {"Retry-After": "0"}, BytesIO(b'{"error":"rate"}'))
            return FakeResponse(200, b'{"ok":true}')

        with patch_urlopen(handler):
            self.assertEqual(request_json("GET", "https://example.com"), {"ok": True})
        self.assertEqual(attempts["n"], 2)

    def test_429_exhaustion_raises_rate_limited(self) -> None:
        def handler(req, timeout=30):
            raise HTTPError(req.full_url, 429, "Too Many", {"Retry-After": "0"}, BytesIO(b'{"error":"rate"}'))

        with patch_urlopen(handler):
            with self.assertRaises(RateLimited):
                request_json("GET", "https://example.com", max_retries=1)


_PORT_COUNTER = [47900]


def _next_port() -> int:
    _PORT_COUNTER[0] += 1
    return _PORT_COUNTER[0]


def _make_flow(port: int) -> OAuthFlow:
    return OAuthFlow(
        provider="test",
        client_id="cid",
        client_secret="csec",
        auth_url="https://example.com/authorize",
        token_url="https://example.com/token",
        scopes="a b",
        redirect_port=port,
        use_pkce=True,
    )


def _trigger_callback(*, port: int, params: str, delay: float = 0.4) -> None:
    def worker():
        time.sleep(delay)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?{params}", timeout=2) as resp:
                resp.read()
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()


class OAuthFlowTest(unittest.TestCase):
    def test_authorize_blocking_happy_path(self) -> None:
        port = _next_port()
        flow = _make_flow(port)
        captured = {}

        def fake_token_call(method, url, headers=None, params=None, data=None, **kwargs):
            captured["data"] = data
            return {"access_token": "a", "refresh_token": "r", "expires_in": 3600, "scope": "a b"}

        with mock.patch("network_chief.auth.oauth.request_json", side_effect=fake_token_call):
            with mock.patch("webbrowser.open", return_value=True):
                with mock.patch("secrets.token_urlsafe", return_value="FIXEDSTATE"):
                    _trigger_callback(port=port, params="code=abc&state=FIXEDSTATE", delay=0.3)
                    token = flow.authorize_blocking(open_browser=False, timeout_s=5)

        self.assertEqual(token["access_token"], "a")
        self.assertEqual(token["refresh_token"], "r")
        self.assertIsNotNone(token["_expires_at"])
        self.assertEqual(captured["data"]["grant_type"], "authorization_code")
        self.assertEqual(captured["data"]["code"], "abc")
        # PKCE verifier must round-trip into the token exchange.
        self.assertIn("code_verifier", captured["data"])
        self.assertTrue(captured["data"]["code_verifier"])

    def test_state_mismatch_raises(self) -> None:
        port = _next_port()
        flow = _make_flow(port)
        with mock.patch("network_chief.auth.oauth.request_json"):
            with mock.patch("webbrowser.open", return_value=True):
                with mock.patch("secrets.token_urlsafe", return_value="STATE-A"):
                    _trigger_callback(port=port, params="code=abc&state=STATE-B", delay=0.3)
                    with self.assertRaises(OAuthError):
                        flow.authorize_blocking(open_browser=False, timeout_s=5)

    def test_loopback_timeout_raises(self) -> None:
        port = _next_port()
        flow = _make_flow(port)
        with mock.patch("webbrowser.open", return_value=True):
            with self.assertRaises(LoopbackServerError):
                flow.authorize_blocking(open_browser=False, timeout_s=0.5)

    def test_refresh_posts_grant(self) -> None:
        flow = _make_flow(_next_port())
        seen = {}

        def fake_call(method, url, headers=None, params=None, data=None, **kwargs):
            seen["data"] = data
            return {"access_token": "newA", "expires_in": 3600}

        with mock.patch("network_chief.auth.oauth.request_json", side_effect=fake_call):
            token = flow.refresh("R")
            self.assertEqual(token["access_token"], "newA")
            self.assertEqual(seen["data"]["grant_type"], "refresh_token")
            self.assertEqual(seen["data"]["refresh_token"], "R")


if __name__ == "__main__":
    unittest.main()
