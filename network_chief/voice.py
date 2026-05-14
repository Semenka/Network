from __future__ import annotations

import json
import re
import sqlite3
from statistics import median
from typing import Any

from .db import new_id, now_iso, rows_to_dicts


DEFAULT_VOICE_SUMMARY = (
    "Concise, warm, specific, and non-transactional. Lead with real context, make one clear ask, "
    "and include a useful give-back when possible."
)


def rebuild_voice_profile(
    con: sqlite3.Connection,
    *,
    sources: list[str] | None = None,
    limit: int = 120,
    profile_name: str = "default",
) -> dict[str, Any]:
    sources = sources or ["sent_mail", "approved_edits"]
    normalized_sources = [source.strip() for source in sources if source.strip()]
    if not normalized_sources:
        normalized_sources = ["sent_mail", "approved_edits"]

    placeholders = ",".join("?" for _ in normalized_sources)
    con.execute(f"DELETE FROM voice_examples WHERE source IN ({placeholders})", normalized_sources)

    examples: list[dict[str, Any]] = []
    if "sent_mail" in normalized_sources:
        examples.extend(_sent_mail_examples(con, limit=limit))
    if "approved_edits" in normalized_sources:
        examples.extend(_approved_edit_examples(con, limit=limit))

    for example in examples[:limit]:
        con.execute(
            """
            INSERT INTO voice_examples (
                id, source, channel, direction, draft_id, interaction_id,
                text_sample, accepted, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                example["source"],
                example["channel"],
                example.get("direction"),
                example.get("draft_id"),
                example.get("interaction_id"),
                example["text_sample"],
                1 if example.get("accepted") else 0,
                now_iso(),
            ),
        )

    profile = _analyze_voice(examples[:limit])
    ts = now_iso()
    existing = con.execute("SELECT id FROM voice_profile WHERE name = ?", (profile_name,)).fetchone()
    if existing:
        con.execute(
            """
            UPDATE voice_profile
               SET summary = ?, style_json = ?, examples_count = ?, updated_at = ?
             WHERE id = ?
            """,
            (
                profile["summary"],
                json.dumps(profile["style"], sort_keys=True),
                profile["examples_count"],
                ts,
                existing["id"],
            ),
        )
        profile_id = str(existing["id"])
    else:
        profile_id = new_id()
        con.execute(
            """
            INSERT INTO voice_profile (id, name, summary, style_json, examples_count, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                profile_name,
                profile["summary"],
                json.dumps(profile["style"], sort_keys=True),
                profile["examples_count"],
                ts,
            ),
        )
    con.commit()
    return {"id": profile_id, **profile, "sources": normalized_sources, "updated_at": ts}


def get_voice_profile_summary(con: sqlite3.Connection, *, profile_name: str = "default") -> str:
    row = con.execute("SELECT summary FROM voice_profile WHERE name = ?", (profile_name,)).fetchone()
    return str(row["summary"]) if row else DEFAULT_VOICE_SUMMARY


def format_voice_profile(profile: dict[str, Any]) -> str:
    lines = [
        "# Network Chief Voice Profile",
        "",
        f"- profile: {profile.get('id')}",
        f"- examples: {profile.get('examples_count', 0)}",
        f"- sources: {', '.join(profile.get('sources', []))}",
        "",
        "## Summary",
        profile.get("summary") or DEFAULT_VOICE_SUMMARY,
    ]
    return "\n".join(lines)


def _sent_mail_examples(con: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT id, channel, direction, subject, body_summary
          FROM interactions
         WHERE channel = 'gmail'
           AND direction = 'outgoing'
           AND body_summary IS NOT NULL
           AND body_summary != ''
         ORDER BY occurred_at DESC, created_at DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    examples = []
    for row in rows_to_dicts(rows):
        text = _clean_sample(" ".join(part for part in (row.get("subject"), row.get("body_summary")) if part))
        if text:
            examples.append(
                {
                    "source": "sent_mail",
                    "channel": "gmail",
                    "direction": "outgoing",
                    "interaction_id": row["id"],
                    "text_sample": text,
                    "accepted": True,
                }
            )
    return examples


def _approved_edit_examples(con: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT DISTINCT drafts.id, drafts.channel, drafts.body
          FROM drafts
          LEFT JOIN draft_events ON draft_events.draft_id = drafts.id
         WHERE drafts.body IS NOT NULL
           AND drafts.body != ''
           AND (
                drafts.status IN ('approved', 'sent', 'published', 'responded')
                OR draft_events.event_type IN ('approve', 'edit')
           )
         ORDER BY drafts.updated_at DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    examples = []
    for row in rows_to_dicts(rows):
        text = _clean_sample(str(row.get("body") or ""))
        if text:
            examples.append(
                {
                    "source": "approved_edits",
                    "channel": row["channel"],
                    "draft_id": row["id"],
                    "text_sample": text,
                    "accepted": True,
                }
            )
    edit_rows = con.execute(
        """
        SELECT draft_events.note, drafts.id AS draft_id, drafts.channel
          FROM draft_events
          JOIN drafts ON drafts.id = draft_events.draft_id
         WHERE draft_events.event_type = 'edit'
           AND draft_events.note IS NOT NULL
           AND draft_events.note != ''
         ORDER BY draft_events.created_at DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    for row in rows_to_dicts(edit_rows):
        text = _clean_sample(str(row.get("note") or ""))
        if text:
            examples.append(
                {
                    "source": "approved_edits",
                    "channel": row["channel"],
                    "draft_id": row["draft_id"],
                    "text_sample": text,
                    "accepted": True,
                }
            )
    return examples[:limit]


def _clean_sample(text: str, *, limit: int = 1200) -> str:
    return " ".join(text.split())[:limit]


def _analyze_voice(examples: list[dict[str, Any]]) -> dict[str, Any]:
    texts = [str(example.get("text_sample") or "") for example in examples if example.get("text_sample")]
    if not texts:
        return {
            "summary": DEFAULT_VOICE_SUMMARY,
            "examples_count": 0,
            "style": {"avg_words": 0, "median_words": 0, "signals": []},
        }
    word_counts = [len(re.findall(r"\w+", text)) for text in texts]
    avg_words = int(sum(word_counts) / max(1, len(word_counts)))
    median_words = int(median(word_counts))
    signals = _style_signals(texts, avg_words)
    summary = (
        "Use the private local voice profile: concise, warm, specific, non-transactional, and goal-aware. "
        f"Typical sample length is around {median_words} words. "
        "Open with concrete context, avoid generic flattery, make one crisp ask, and offer help or a useful next step."
    )
    if signals:
        summary += " Observed style signals: " + "; ".join(signals[:4]) + "."
    return {
        "summary": summary,
        "examples_count": len(texts),
        "style": {"avg_words": avg_words, "median_words": median_words, "signals": signals},
    }


def _style_signals(texts: list[str], avg_words: int) -> list[str]:
    joined = "\n".join(texts).lower()
    signals: list[str] = []
    if avg_words <= 90:
        signals.append("keeps messages short")
    if "happy to" in joined or "glad to" in joined:
        signals.append("offers help naturally")
    if "quick" in joined:
        signals.append("uses lightweight asks")
    if "thinking about" in joined or "focused on" in joined:
        signals.append("anchors messages in current context")
    if "best," in joined or "andrey" in joined:
        signals.append("uses simple sign-offs")
    return signals

