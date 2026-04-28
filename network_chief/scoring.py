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


VALUE_KEYWORDS: dict[str, tuple[int, tuple[str, ...]]] = {
    "financial_capital": (
        80,
        (
            "angel",
            "capital",
            "fund",
            "investor",
            "investment",
            "lp",
            "private equity",
            "vc",
            "venture",
        ),
    ),
    "time_saving": (
        65,
        (
            "automation",
            "chief of staff",
            "consultant",
            "delivery",
            "integrator",
            "ops",
            "operator",
            "operations",
            "productivity",
            "systems",
        ),
    ),
    "competence": (
        70,
        (
            "architect",
            "builder",
            "developer",
            "engineer",
            "founder",
            "lawyer",
            "operator",
            "principal",
            "product",
            "scientist",
            "strategy",
        ),
    ),
    "specific_knowledge": (
        75,
        (
            "ai",
            "analytics",
            "biotech",
            "crypto",
            "data",
            "deeptech",
            "expert",
            "machine learning",
            "medicine",
            "phd",
            "professor",
            "research",
        ),
    ),
}


VALUE_ALIASES: dict[str, tuple[str, ...]] = {
    "financial": ("financial_capital", "financial capital", "capital", "investor"),
    "financial_capital": ("financial_capital", "financial capital", "capital", "investor"),
    "knowledge": ("specific_knowledge", "specific knowledge", "knowledge", "expert"),
    "specific_knowledge": ("specific_knowledge", "specific knowledge", "knowledge", "expert"),
    "human": ("competence", "competence", "talent", "operator"),
    "labor": ("time_saving", "time saving", "execution", "operator"),
    "time_saving": ("time_saving", "time saving", "automation", "operator"),
    "competence": ("competence", "competence", "expertise", "operator"),
}


def infer_resources_from_text(text: str | None) -> list[tuple[str, str]]:
    if not text:
        return []
    haystack = text.lower()
    resources: list[tuple[str, str]] = []
    for resource_type, keywords in RESOURCE_KEYWORDS.items():
        if any(_keyword_in_text(haystack, keyword) for keyword in keywords):
            description = f"Inferred {resource_type} resource from: {text[:140]}"
            resources.append((resource_type, description))
    return resources


def _keyword_in_text(haystack: str, keyword: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(keyword.lower())}(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


def infer_connection_values_from_text(text: str | None) -> list[tuple[str, str, int]]:
    if not text:
        return []
    haystack = text.lower()
    values: list[tuple[str, str, int]] = []
    for value_type, (score, keywords) in VALUE_KEYWORDS.items():
        matches = [keyword for keyword in keywords if _keyword_in_text(haystack, keyword)]
        if matches:
            evidence = ", ".join(matches[:4])
            description = f"Inferred {value_type.replace('_', ' ')} signal from: {text[:140]}"
            values.append((value_type, description, score + min(15, len(matches) * 3)))
    return values


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
            group_concat(DISTINCT connection_values.value_type || ': ' || connection_values.description) AS connection_values,
            MAX(connection_values.score) AS max_connection_value_score,
            group_concat(DISTINCT organizations.name) AS organizations,
            group_concat(DISTINCT roles.title) AS titles
        FROM people p
        LEFT JOIN relationships rel ON rel.person_id = p.id
        LEFT JOIN resources ON resources.person_id = p.id
        LEFT JOIN connection_values ON connection_values.person_id = p.id
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
        if capital_type:
            aliases = VALUE_ALIASES.get(capital_type, (capital_type, capital_type.replace("_", " ")))
            if any(alias in text for alias in aliases):
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
    value_score = min(25, int(person.get("max_connection_value_score") or 0) // 4)
    goal_score, goal_reasons, goal = _goal_match_score(person, goals)
    score = staleness_score + relationship_score + contact_score + value_score + goal_score

    reasons = []
    if days >= 90:
        reasons.append(f"no recorded interaction for {days} days")
    elif days >= 30:
        reasons.append(f"relationship is getting stale: {days} days")
    if person.get("resources"):
        reasons.append("has mapped resources")
    if person.get("connection_values"):
        reasons.append("has explicit connection-value signals")
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
