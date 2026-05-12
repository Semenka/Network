from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .db import record_source_run
from .importers.gmail import import_gmail_json, import_gmail_mbox
from .sync import classify_source_file, discover_source_files
from .value import maintain_connection_values


DEFAULT_GMAIL_SYNC_FILES = (Path("data/gmail-connector-sync.json"), Path("data/gmail-sync.json"))


def sync_gmail(
    con: sqlite3.Connection,
    *,
    file: str | Path | None = None,
    scan_dirs: list[Path] | None = None,
    include_downloads: bool = False,
    mailbox_owner: str | None = None,
    since_months: int = 24,
    max_threads: int = 2000,
) -> dict[str, Any]:
    files = _source_files(file=file, scan_dirs=scan_dirs, include_downloads=include_downloads)
    imported: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for path in files:
        kind = classify_source_file(path)
        if kind not in {"gmail_json", "gmail_mbox"}:
            kind = "gmail_mbox" if path.suffix.lower() == ".mbox" else "gmail_json"
        try:
            stats = _import_gmail_file(
                con,
                kind=kind,
                path=path,
                mailbox_owner=mailbox_owner,
                since_months=since_months,
                max_threads=max_threads,
            )
        except Exception as exc:
            record_source_run(con, source=kind, source_ref=str(path), status="error", stats={"error": str(exc)})
            errors.append({"source": kind, "path": str(path), "error": str(exc)})
            continue
        record_source_run(con, source=kind, source_ref=str(path), status="ok", stats=stats)
        imported.append({"source": kind, "path": str(path), "stats": stats})

    value_stats = maintain_connection_values(con)
    return {
        "scope": {"since_months": since_months, "max_threads": max_threads},
        "found": len(files),
        "files": [str(path) for path in files],
        "imported": imported,
        "errors": errors,
        "value_maintenance": value_stats,
    }


def summarize_gmail_sync(stats: dict[str, Any]) -> str:
    scope = stats["scope"]
    lines = [
        "# Network Chief Gmail Sync",
        "",
        "## Scope",
        f"- since_months: {scope['since_months']}",
        f"- max_threads: {scope['max_threads']}",
        "- default filters: spam, trash, and promotions excluded for connector JSON",
        "",
        "## Sources",
    ]
    if stats["files"]:
        lines.extend(f"- {path}" for path in stats["files"])
    else:
        lines.append("- none found")

    lines.extend(["", "## Imported"])
    if stats["imported"]:
        for item in stats["imported"]:
            stat_text = ", ".join(f"{key}={value}" for key, value in sorted(item["stats"].items()))
            lines.append(f"- {item['source']}: {item['path']} ({stat_text})")
    else:
        lines.append("- none")

    lines.extend(["", "## Maintenance"])
    maintenance = stats["value_maintenance"]
    lines.append(f"- people_scanned: {maintenance['people_scanned']}")
    lines.append(f"- values_seen: {maintenance['values_seen']}")

    if stats["errors"]:
        lines.extend(["", "## Errors"])
        for error in stats["errors"]:
            lines.append(f"- {error['source']}: {error['path']} ({error['error']})")

    if not stats["files"]:
        lines.extend(
            [
                "",
                "## Missing Local Gmail Source",
                "- Put connector JSON at data/gmail-connector-sync.json or pass --file.",
                "- For unattended OpenClaw runs, configure a local Gmail API/OAuth export job that writes the same JSON shape.",
            ]
        )
    return "\n".join(lines)


def _source_files(
    *,
    file: str | Path | None,
    scan_dirs: list[Path] | None,
    include_downloads: bool,
) -> list[Path]:
    if file:
        return [Path(file).expanduser()]

    files = [path for path in DEFAULT_GMAIL_SYNC_FILES if path.exists()]
    dirs = scan_dirs or [Path("exports"), Path("data")]
    if include_downloads:
        dirs.append(Path.home() / "Downloads")
    discovered = discover_source_files(dirs)
    files.extend(discovered.get("gmail_json", []))
    files.extend(discovered.get("gmail_mbox", []))

    seen: set[str] = set()
    deduped: list[Path] = []
    for path in files:
        key = str(path.expanduser())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _import_gmail_file(
    con: sqlite3.Connection,
    *,
    kind: str,
    path: Path,
    mailbox_owner: str | None,
    since_months: int,
    max_threads: int,
) -> dict[str, int]:
    if kind == "gmail_mbox":
        return import_gmail_mbox(con, path, mailbox_owner=mailbox_owner, limit=max_threads, since_months=since_months)
    return import_gmail_json(
        con,
        path,
        mailbox_owner=mailbox_owner,
        limit=max_threads,
        since_months=since_months,
        exclude_default_labels=True,
    )

