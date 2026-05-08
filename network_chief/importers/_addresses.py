from __future__ import annotations

from datetime import UTC, datetime
from email.utils import getaddresses, parsedate_to_datetime


def parse_addresses(value: str | None) -> list[tuple[str, str]]:
    """Return ``(name, address)`` pairs from a free-form address header."""
    if not value:
        return []
    parsed: list[tuple[str, str]] = []
    for name, address in getaddresses([value]):
        address = address.strip().lower()
        if not address:
            continue
        parsed.append((name.strip() or address.split("@")[0], address))
    return parsed


def parse_date(value: str | None) -> str | None:
    """Best-effort RFC2822 / ISO8601 → ISO8601 UTC normalization."""
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
