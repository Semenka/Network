"""Autonomy policy for the autopilot loop.

The policy is the single decision seam between the agent preparing outbound
messages and actually sending them. It is deliberately conservative by
default: shipped with ``level=0`` (prepare only) and ``dry_run=True`` so
that merely installing or scheduling the autopilot can never send a message
to a real contact. Raising the level is an explicit operator action via
``network-chief policy set``.

Guardrails (daily cap, active-consent-only, verified-email-only, quiet
hours, minimum days between touches) apply at *every* level — they bound
volume and protect opted-out / unverified recipients, and are never relaxed
even at full-autonomy L2.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .db import db_path_from_env


LEVEL_PREPARE_ONLY = 0
LEVEL_BOUNDED = 1
LEVEL_FULL = 2


@dataclass
class AutonomyPolicy:
    """Outbound autonomy configuration. Safe-by-default."""

    level: int = 0  # 0 prepare-only · 1 bounded auto-send · 2 full auto-send
    dry_run: bool = True  # when True, send paths log intent but make no API call
    daily_send_cap: int = 5
    min_days_between_touches: int = 30
    require_existing_contact: bool = True  # enforced at L1; dropped at L2
    require_prior_reply: bool = True  # enforced at L1; dropped at L2
    channels_enabled: tuple[str, ...] = ("gmail",)
    quiet_hours: tuple[int, int] = (21, 8)  # (start_hour, end_hour) local; no sends within
    # Per-channel auto-action switches. All default off; enabling each is a
    # deliberate operator action even at L2.
    gmail_auto_reply_enabled: bool = False
    x_post_enabled: bool = False
    x_reply_enabled: bool = False

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        # JSON has no tuples; store as lists and normalize on load.
        data["channels_enabled"] = list(self.channels_enabled)
        data["quiet_hours"] = list(self.quiet_hours)
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "AutonomyPolicy":
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in (data or {}).items() if k in known}
        if "channels_enabled" in kwargs:
            kwargs["channels_enabled"] = tuple(kwargs["channels_enabled"])
        if "quiet_hours" in kwargs:
            kwargs["quiet_hours"] = tuple(kwargs["quiet_hours"])
        return cls(**kwargs)


def _policy_path(db_path: str | None = None) -> Path:
    """Co-locate the policy file with the database directory (private)."""
    resolved = db_path_from_env(db_path)
    if resolved == ":memory:":
        return Path("data/autopilot_policy.json")
    return Path(resolved).parent / "autopilot_policy.json"


def load_policy(db_path: str | None = None) -> AutonomyPolicy:
    path = _policy_path(db_path)
    if not path.exists():
        return AutonomyPolicy()
    try:
        return AutonomyPolicy.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError):
        return AutonomyPolicy()


def save_policy(policy: AutonomyPolicy, db_path: str | None = None) -> Path:
    path = _policy_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(policy.to_json(), indent=2, sort_keys=True), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def _in_quiet_hours(hour: int, quiet: tuple[int, int]) -> bool:
    start, end = quiet
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    # Wraps midnight, e.g. (21, 8): quiet from 21:00 to 08:00.
    return hour >= start or hour < end


def _has_prior_reply(con: sqlite3.Connection, person_id: str | None) -> bool:
    if not person_id:
        return False
    row = con.execute(
        """
        SELECT 1
          FROM interactions
         WHERE person_id = ? AND direction = 'incoming'
         LIMIT 1
        """,
        (person_id,),
    ).fetchone()
    if row:
        return True
    # A draft already marked responded also counts as prior reply.
    row = con.execute(
        "SELECT 1 FROM drafts WHERE person_id = ? AND outcome = 'responded' LIMIT 1",
        (person_id,),
    ).fetchone()
    return row is not None


def _days_since_last_touch(con: sqlite3.Connection, person_id: str | None) -> float | None:
    if not person_id:
        return None
    row = con.execute(
        "SELECT max(sent_at) FROM drafts WHERE person_id = ? AND sent_at IS NOT NULL",
        (person_id,),
    ).fetchone()
    last = row[0] if row else None
    if not last:
        return None
    try:
        parsed = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (datetime.now(UTC) - parsed).total_seconds() / 86400.0


def permits_send(
    policy: AutonomyPolicy,
    con: sqlite3.Connection,
    draft: dict[str, Any],
    *,
    now: datetime | None = None,
    todays_sends: int = 0,
    channel: str = "gmail",
) -> tuple[bool, str]:
    """Return (allowed, reason). The single gating decision for any send.

    Guardrails enforced at every level >= 1:
      - channel must be enabled
      - recipient consent_status must be 'active'
      - recipient must have a verified/usable address (caller-checked email)
      - daily_send_cap not exceeded
      - outside quiet hours
      - min_days_between_touches respected
    L1 additionally requires an existing contact with a prior reply.
    L2 drops the prior-reply / existing-contact requirement but keeps all
    the volume + consent + quiet-hour guardrails.
    """
    now = now or datetime.now(UTC)

    if policy.level <= LEVEL_PREPARE_ONLY:
        return False, "prepare-only (level 0): queued for approval, not sent"

    if channel not in policy.channels_enabled:
        return False, f"channel '{channel}' not enabled in policy"

    if (draft.get("consent_status") or "active") != "active":
        return False, "recipient consent_status is not 'active'"

    if not (draft.get("primary_email") or "").strip():
        return False, "no usable recipient address"

    if todays_sends >= policy.daily_send_cap:
        return False, f"daily_send_cap reached ({policy.daily_send_cap})"

    if _in_quiet_hours(now.hour, policy.quiet_hours):
        return False, f"within quiet hours {policy.quiet_hours}"

    days = _days_since_last_touch(con, draft.get("person_id"))
    if days is not None and days < policy.min_days_between_touches:
        return False, f"touched {days:.1f}d ago (< {policy.min_days_between_touches}d)"

    if policy.level == LEVEL_BOUNDED:
        if policy.require_prior_reply and not _has_prior_reply(con, draft.get("person_id")):
            return False, "level 1 requires a prior reply from this contact"

    return True, "ok"
