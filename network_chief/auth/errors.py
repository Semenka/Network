from __future__ import annotations


class OAuthError(RuntimeError):
    """Base class for OAuth-related failures."""


class AuthRequired(OAuthError):
    """No usable token for the requested provider/account."""


class LoopbackServerError(OAuthError):
    """The local redirect server failed to receive a valid callback."""


class RateLimited(OAuthError):
    """Provider returned 429 after exhausting retries."""

    def __init__(self, message: str, *, reset_at: str | None = None, body: str | None = None):
        super().__init__(message)
        self.reset_at = reset_at
        self.body = body
