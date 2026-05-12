from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .db import add_source_fact, now_iso, record_source_run, rows_to_dicts


Runner = Callable[[list[str], str | None], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class GBrainResult:
    slug: str
    score: float | None
    text: str


class GBrainAdapter:
    """Small CLI adapter for the local gbrain knowledge base.

    The adapter intentionally shells out to the local `gbrain` binary instead
    of importing internals. That keeps Network Chief decoupled from gbrain
    release internals and makes tests easy to fake.
    """

    def __init__(
        self,
        *,
        binary: str | None = None,
        timeout_s: float = 20.0,
        runner: Runner | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.binary = binary or os.environ.get("GBRAIN_BIN") or _default_binary()
        self.timeout_s = timeout_s
        self._runner = runner
        self.enabled = enabled if enabled is not None else os.environ.get("NETWORK_CHIEF_GBRAIN", "1") != "0"

    def available(self) -> bool:
        return self.enabled and bool(shutil.which(self.binary))

    def search(self, query: str, *, limit: int = 5) -> list[GBrainResult]:
        return self._read(["search", query], limit=limit)

    def query(self, query: str, *, limit: int = 5) -> list[GBrainResult]:
        return self._read(["query", query], limit=limit)

    def get_page(self, slug: str) -> str | None:
        result = self._run(["get", slug])
        if result.returncode != 0:
            return None
        return result.stdout

    def put_page(self, slug: str, content: str, *, dry_run: bool = False) -> dict[str, Any]:
        if dry_run:
            return {"status": "dry_run", "slug": slug, "bytes": len(content.encode("utf-8"))}
        result = self._run(["put", slug], stdin=content)
        return {"status": "ok" if result.returncode == 0 else "error", "slug": slug, "stderr": result.stderr.strip()}

    def add_timeline_entry(self, slug: str, date: str, text: str, *, dry_run: bool = False) -> dict[str, Any]:
        if dry_run:
            return {"status": "dry_run", "slug": slug, "date": date, "text": text}
        result = self._run(["timeline-add", slug, date, text])
        return {"status": "ok" if result.returncode == 0 else "error", "slug": slug, "stderr": result.stderr.strip()}

    def sync(self, *, dry_run: bool = False) -> dict[str, Any]:
        if dry_run:
            return {"status": "dry_run"}
        result = self._run(["sync", "--no-pull"])
        return {"status": "ok" if result.returncode == 0 else "error", "stderr": result.stderr.strip()}

    def _read(self, args: list[str], *, limit: int) -> list[GBrainResult]:
        result = self._run(args)
        if result.returncode != 0:
            return []
        return _parse_results(result.stdout)[:limit]

    def _run(self, args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        if not self.available() and self._runner is None:
            return subprocess.CompletedProcess([self.binary, *args], 127, "", "gbrain unavailable")
        if self._runner is not None:
            return self._runner([self.binary, *args], stdin)
        try:
            return subprocess.run(
                [self.binary, *args],
                input=stdin,
                text=True,
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess([self.binary, *args], 1, "", str(exc))


def _default_binary() -> str:
    if shutil.which("gbrain"):
        return "gbrain"
    bun_path = Path.home() / ".bun" / "bin" / "gbrain"
    if bun_path.exists():
        return str(bun_path)
    return "gbrain"


def _parse_results(output: str) -> list[GBrainResult]:
    rows: list[GBrainResult] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("[ai.gateway]"):
            continue
        match = re.match(r"^\[(?P<score>[0-9.]+)\]\s+(?P<slug>[^\s]+)\s+--\s+(?P<text>.*)$", line)
        if match:
            rows.append(
                GBrainResult(
                    slug=match.group("slug"),
                    score=float(match.group("score")),
                    text=match.group("text").strip(),
                )
            )
            continue
        if " -- " in line:
            slug, text = line.split(" -- ", 1)
            rows.append(GBrainResult(slug=slug.strip(), score=None, text=text.strip()))
    return rows


def format_gbrain_context(query: str, results: list[GBrainResult]) -> str:
    lines = [f"# GBrain Context: {query}", ""]
    if not results:
        lines.append("- No gbrain context found or gbrain is unavailable.")
        return "\n".join(lines)
    for item in results:
        score = f"{item.score:.3f}" if item.score is not None else "-"
        lines.append(f"- `{item.slug}` ({score}): {item.text}")
    return "\n".join(lines)


def fetch_gbrain_context(
    query: str,
    *,
    adapter: GBrainAdapter | None = None,
    limit: int = 5,
) -> list[GBrainResult]:
    adapter = adapter or GBrainAdapter()
    results = adapter.search(query, limit=limit)
    if len(results) < min(2, limit):
        seen = {item.slug for item in results}
        for item in adapter.query(query, limit=limit):
            if item.slug not in seen:
                results.append(item)
                seen.add(item.slug)
            if len(results) >= limit:
                break
    return results[:limit]


def attach_gbrain_context_to_person(
    con: sqlite3.Connection,
    *,
    person_id: str,
    query: str,
    adapter: GBrainAdapter | None = None,
    limit: int = 3,
) -> list[GBrainResult]:
    results = fetch_gbrain_context(query, adapter=adapter, limit=limit)
    for item in results:
        add_source_fact(
            con,
            person_id=person_id,
            fact_type="gbrain_context",
            fact_value=item.text[:500] or item.slug,
            source="gbrain",
            source_ref=item.slug,
            confidence=0.75,
        )
    return results


def sync_gbrain_summaries(
    con: sqlite3.Connection,
    *,
    since_days: int = 7,
    mode: str = "auto-summary",
    adapter: GBrainAdapter | None = None,
    dry_run: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    if mode != "auto-summary":
        raise ValueError("Only mode='auto-summary' is supported.")
    adapter = adapter or GBrainAdapter()
    cutoff = (datetime.now(UTC) - timedelta(days=since_days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    events = rows_to_dicts(
        con.execute(
            """
            SELECT
                draft_events.*,
                drafts.channel,
                drafts.subject,
                drafts.person_id,
                people.full_name,
                people.primary_email,
                people.linkedin_url,
                people.telegram_handle
              FROM draft_events
              JOIN drafts ON drafts.id = draft_events.draft_id
              LEFT JOIN people ON people.id = drafts.person_id
             WHERE draft_events.created_at >= ?
               AND draft_events.event_type IN (
                   'approve', 'approved', 'sent', 'published', 'response',
                   'responded', 'converted', 'outcome'
               )
             ORDER BY draft_events.created_at
             LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
    )
    stats: dict[str, Any] = {"events_seen": len(events), "written": 0, "skipped": 0, "dry_run": dry_run, "items": []}
    for event in events:
        event_id = str(event["id"])
        slug = _slug_for_event(event)
        fact_value = f"{event_id}:{slug}"
        already = con.execute(
            """
            SELECT id FROM source_facts
             WHERE fact_type = 'gbrain_writeback'
               AND fact_value = ?
               AND source = 'gbrain'
               AND source_ref = ?
            """,
            (fact_value, slug),
        ).fetchone()
        if already:
            stats["skipped"] += 1
            continue

        summary = _event_summary(event)
        existing = None if dry_run else adapter.get_page(slug)
        content = _page_content(slug=slug, title=_title_for_event(event), summary=summary, existing=existing)
        put_result = adapter.put_page(slug, content, dry_run=dry_run)
        timeline_result = adapter.add_timeline_entry(slug, str(event["created_at"])[:10], summary, dry_run=dry_run)
        if put_result.get("status") in {"ok", "dry_run"} and timeline_result.get("status") in {"ok", "dry_run"}:
            if not dry_run:
                add_source_fact(
                    con,
                    person_id=event.get("person_id"),
                    fact_type="gbrain_writeback",
                    fact_value=fact_value,
                    source="gbrain",
                    source_ref=slug,
                    confidence=0.8,
                )
            stats["written"] += 1
            stats["items"].append({"event_id": event_id, "slug": slug, "summary": summary})
        else:
            stats["skipped"] += 1
            stats["items"].append({"event_id": event_id, "slug": slug, "error": put_result.get("stderr") or timeline_result.get("stderr")})
    if stats["written"]:
        adapter.sync(dry_run=dry_run)
    record_source_run(con, source="gbrain_writeback", source_ref=None, status="ok", stats=stats)
    return stats


def _slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return value or "network-chief"


def _slug_for_event(event: dict[str, Any]) -> str:
    if event.get("full_name"):
        return f"people/{_slugify(str(event['full_name']))}"
    return "projects/network-chief"


def _title_for_event(event: dict[str, Any]) -> str:
    return str(event.get("full_name") or "Network Chief")


def _event_summary(event: dict[str, Any]) -> str:
    pieces = [
        f"Network Chief {event['event_type']} event",
        f"channel={event.get('channel') or 'unknown'}",
    ]
    if event.get("subject"):
        pieces.append(f"subject={event['subject']}")
    if event.get("reason_code"):
        pieces.append(f"reason={event['reason_code']}")
    if event.get("note"):
        pieces.append(f"note={str(event['note'])[:220]}")
    if event.get("external_ref"):
        pieces.append(f"ref={event['external_ref']}")
    return "; ".join(pieces)


def _page_content(*, slug: str, title: str, summary: str, existing: str | None = None) -> str:
    entry = f"- {now_iso()}: {summary} [Source: network-chief]"
    if existing:
        if summary in existing:
            return existing
        return existing.rstrip() + "\n\n## Network Chief Summary\n\n" + entry + "\n"
    page_type = "person" if slug.startswith("people/") else "project"
    return "\n".join(
        [
            "---",
            f'type: "{page_type}"',
            f'title: "{title}"',
            'tags: ["network-chief", "auto-summary"]',
            "---",
            "",
            f"# {title}",
            "",
            "## Network Chief Summary",
            "",
            entry,
            "",
        ]
    )
