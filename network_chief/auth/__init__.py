"""OAuth scaffolding for Network Chief providers (Google, X.com, LinkedIn).

Stdlib-only. Tokens persist in the existing SQLite database via
:class:`TokenStore`. The :class:`OAuthFlow` performs the
Authorization-Code-with-PKCE dance over a one-shot loopback redirect
server, and :func:`request_json` is the thin ``urllib`` wrapper used by the
per-provider connectors.
"""

from .errors import AuthRequired, LoopbackServerError, OAuthError, RateLimited
from .http_util import paginate, request_json
from .oauth import OAuthFlow, pkce_pair
from .tokens import TokenStore

__all__ = [
    "AuthRequired",
    "LoopbackServerError",
    "OAuthError",
    "OAuthFlow",
    "RateLimited",
    "TokenStore",
    "paginate",
    "pkce_pair",
    "request_json",
]
