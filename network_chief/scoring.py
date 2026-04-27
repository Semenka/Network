from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from typing import Any

from .db import rows_to_dicts


RESOURCE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "financial": (
        "angel",
        "capital",
        "fund",
        "investor",
        "investment",
        "partner",
        "pe",
        "private equity",
        "vc",
        "venture",
    ),
    "knowledge": (
        "advisor",
        "ai",
        "analytics",
        "data",
        "doctor",
        "engineer",
        "expert",
        "founder",
        "machine learning",
        "professor",
        "research",
        "scientist",
    ),
    "labor": (
        "builder",
        "consultant",
        "developer",
        "engineer",
        "freelance",
        "operator",
        "product",
        "recruiter",
        "talent",
    ),
    "health": (
        "bio",
        "coach",
        "doctor",
        "fitness",
        "health",
        "medical",
        "nutrition",
        "physician",
        "therapy",
        "wellness",
    ),
    "reputation": (
        "author",
        "community",
        "creator",
        "editor",
        "journalist",
        "media",
        "podcast",
        "speaker",
        "writer",
    ),
    "social": (
        "association",
        "club",
        "community",
        "connector",
        "event",
        "network",
        "organizer",
        "partnership",
    ),
}


def infer_resources_from_text(text: str | None) -> list[tuple[str, str]]:
    if not text:
        return []
    haystack = text.lower()
    resources: list[tuple[str, str]] = []
    for resource_type, keywords in RESOURCE_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            description = f"Inferred {resource_type} resource from: {text[:140]}"
            resources.append((resource_type, description))
    return resources


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _days_since(value: str | None) -> int:
    parsed = _parse_time(value)
    if parsed is None:
        return 365
    return max(0, (datetime.now(UTC) - parsed).days)


def fetch_people_for_ranking(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT
            p.*,
            rel.strength,
            rel.warmth,
            rel.trust,
            rel.last_interaction_at,
            group_concat(DISTINCT resources.resource_type || ': ' || resources.description) AS resources,
            group_concat(DISTINCT organizations.name) AS organizations,
            group_concat(DISTINCT roles.title) AS titles
        FROM people p
        LEFT JOIN relationships rel ON rel.person_id = p.id
        LEFT JOIN resources ON resources.person_id = p.id
        LEFT JOIN roles ON roles.person_id = p.id
        LEFT JOIN organizations ON organizations.id = roles.organization_id
        GROUP BY p.id
        """
    ).fetchall()
    return rows_to_dicts(rows)


def _goal_match_score(person: dict[str, Any], goals: list[dict[str, Any]]) -> tuple[int, list[str], dict[str, Any] | None]:
    text = " ".join(
        str(person.get(key) or "")
        for key in ("full_name", "resources", "organizations", "titles", "notes", "location")
    ).lower()
    best_score = 0
    best_goal: dict[str, Any] | None = None
    reasons: list[str] = []
    for goal in goals:
        score = 0
        capital_type = (goal.get("capital_type") or "").lower()
        target = (goal.get("target_segment") or "").lower()
        title = (goal.get("title") or "").lower()
        if capital_type and capital_type in text:
            score += 35
        for token in re.findall(r"[a-zA-Z0-9]{3,}", f"{target} {title}"):
            if token in text:
                score += 4
        if score > best_score:
            best_score = min(score, 60)
            best_goal = goal
    if best_goal:
        reasons.append(f"matches active goal: {best_goal['title']}")
    return best_score, reasons, best_goal


def score_person(person: dict[str, Any], goals: list[dict[str, Any]]) -> dict[str, Any]:
    days = _days_since(person.get("last_interaction_at"))
    staleness_score = min(45, int(days / 4))
    warmth = int(person.get("warmth") or 35)
    trust = int(person.get("trust") or 35)
    relationship_score = int((warmth + trust) / 5)
    contact_score = 10 if person.get("primary_email") else 4
    goal_score, goal_reasons, goal = _goal_match_score(person, goals)
    score = staleness_score + relationship_score + contact_score + goal_score

    reasons = []
    if days >= 90:
        reasons.append(f"no recorded interaction for {days} days")
    elif days >= 30:
        reasons.append(f"relationship is getting stale: {days} days")
    if person.get("resources"):
        reasons.append("has mapped resources")
    reasons.extend(goal_reasons)
    if not reasons:
        reasons.append("useful light-touch relationship maintenance")

    enriched = dict(person)
    enriched["score"] = score
    enriched["staleness_days"] = days
    enriched["rationale"] = "; ".join(reasons)
    enriched["goal"] = goal
    return enriched


def rank_people(con: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    goals = rows_to_dicts(con.execute("SELECT * FROM goals WHERE status = 'active'").fetchall())
    ranked = [score_person(person, goals) for person in fetch_people_for_ranking(con)]
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[:limit]
