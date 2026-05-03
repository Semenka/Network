from __future__ import annotations

import json as _json
import time
from datetime import UTC, datetime
from typing import Any, Callable, Iterator
from urllib import parse, request, error

from .errors import RateLimited


REDACTED_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key"}


def _build_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None,
    params: dict[str, Any] | None,
    data: bytes | None,
) -> request.Request:
    if params:
        encoded = parse.urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{encoded}" if encoded else url
    req = request.Request(url, data=data, method=method.upper())
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    return req


def _safe_headers(req: request.Request) -> dict[str, str]:
    return {k: ("<redacted>" if k.lower() in REDACTED_HEADERS else v) for k, v in req.headers.items()}


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    json_body: Any | None = None,
    timeout: float = 30.0,
    max_retries: int = 3,
    retry_backoff_s: float = 1.5,
) -> dict[str, Any]:
    """JSON-decoded HTTP call with retries on 429/5xx.

    ``data`` is form-encoded; ``json_body`` is JSON-encoded. They are mutually
    exclusive. Returns the parsed JSON body (or ``{"_raw": text}`` if the
    response is not JSON). Raises :class:`RateLimited` after retries.
    """

    body: bytes | None = None
    merged_headers = dict(headers or {})
    if data is not None and json_body is not None:
        raise ValueError("Provide either data or json_body, not both.")
    if data is not None:
        body = parse.urlencode({k: v for k, v in data.items() if v is not None}, doseq=True).encode()
        merged_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif json_body is not None:
        body = _json.dumps(json_body).encode("utf-8")
        merged_headers.setdefault("Content-Type", "application/json")
    merged_headers.setdefault("Accept", "application/json")
    merged_headers.setdefault("User-Agent", "network-chief/0.1 (+https://github.com/Semenka/Network)")

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        req = _build_request(method, url, headers=merged_headers, params=params, data=body)
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                text = raw.decode("utf-8", errors="replace")
                ctype = resp.headers.get("Content-Type", "")
                if "application/json" in ctype or text.strip().startswith(("{", "[")):
                    try:
                        return _json.loads(text) if text else {}
                    except _json.JSONDecodeError:
                        return {"_raw": text}
                return {"_raw": text}
        except error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            if exc.code == 429 and attempt < max_retries:
                wait = _wait_seconds(exc.headers, attempt, retry_backoff_s)
                time.sleep(wait)
                continue
            if 500 <= exc.code < 600 and attempt < max_retries:
                time.sleep(retry_backoff_s * (2 ** attempt))
                continue
            if exc.code == 429:
                reset = exc.headers.get("x-rate-limit-reset") or exc.headers.get("Retry-After")
                raise RateLimited(
                    f"{method} {url} rate-limited (reset={reset})",
                    reset_at=reset,
                    body=text[:500],
                ) from exc
            last_error = error.HTTPError(exc.url, exc.code, f"{exc.reason}: {text[:200]}", exc.headers, None)
            raise last_error
        except (error.URLError, TimeoutError) as exc:
            if attempt < max_retries:
                time.sleep(retry_backoff_s * (2 ** attempt))
                last_error = exc
                continue
            raise

    raise last_error or RuntimeError(f"{method} {url} exhausted retries")


def _wait_seconds(headers: Any, attempt: int, base: float) -> float:
    retry_after = headers.get("Retry-After") if headers else None
    reset = headers.get("x-rate-limit-reset") if headers else None
    if retry_after:
        try:
            return max(1.0, float(retry_after))
        except ValueError:
            pass
    if reset:
        try:
            target = float(reset)
            now = datetime.now(UTC).timestamp()
            return max(1.0, target - now)
        except ValueError:
            pass
    return base * (2 ** attempt)


def paginate(
    fetcher: Callable[[dict[str, Any] | None], dict[str, Any]],
    *,
    next_token_keys: tuple[str, ...] = ("nextPageToken", "next_token"),
    items_key: str | None = None,
    max_pages: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Walk a cursor-paginated endpoint.

    ``fetcher(meta)`` is called with ``None`` for the first page, then with
    ``{"page_token": ...}`` for subsequent pages. Yields each raw page dict.
    Caller extracts items via ``items_key`` or directly from the payload.
    """

    pages = 0
    meta: dict[str, Any] | None = None
    while True:
        page = fetcher(meta)
        yield page
        pages += 1
        if max_pages and pages >= max_pages:
            return
        token = None
        for key in next_token_keys:
            token = page.get(key)
            if token:
                break
        if not token:
            # Some APIs return meta.next_token (e.g. X v2)
            meta_block = page.get("meta") if isinstance(page.get("meta"), dict) else {}
            for key in next_token_keys:
                token = meta_block.get(key)
                if token:
                    break
        if not token:
            return
        meta = {"page_token": token}
