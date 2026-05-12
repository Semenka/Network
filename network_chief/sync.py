from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .db import record_source_run
from .importers.gmail import import_gmail_json, import_gmail_mbox
from .importers.linkedin import import_connections, import_linkedin_interactions
from .importers.x import import_x_export
from .value import maintain_connection_values


SYNC_DIR_NAMES = ("exports",)
LINKEDIN_CONNECTION_NAMES = ("connections.csv",)
LINKEDIN_INTERACTION_HINTS = ("message", "messages", "conversation", "inmail")
GMAIL_JSON_HINTS = ("gmail", "google_mail", "mail")
X_HINTS = ("x_", "twitter", "tweet")


def default_scan_dirs(*, include_downloads: bool = False) -> list[Path]:
    dirs = [Path(name) for name in SYNC_DIR_NAMES]
    if include_downloads:
        dirs.append(Path.home() / "Downloads")
    return dirs


def discover_source_files(scan_dirs: list[Path], *, max_depth: int = 4) -> dict[str, list[Path]]:
    discovered: dict[str, list[Path]] = {
        "linkedin_connections": [],
        "linkedin_interactions": [],
        "gmail_mbox": [],
        "gmail_json": [],
        "x_export": [],
    }
    for root in scan_dirs:
        if not root.exists():
            continue
        root = root.expanduser()
        for path in _iter_files(root, max_depth=max_depth):
            kind = classify_source_file(path)
            if kind:
                discovered[kind].append(path)
    return {kind: _dedupe_paths(paths) for kind, paths in discovered.items()}


def classify_source_file(path: Path) -> str | None:
    name = path.name.lower()
    suffix = path.suffix.lower()
    parts = {part.lower() for part in path.parts}
    parent_text = " ".join(path.parts).lower()
    if name in LINKEDIN_CONNECTION_NAMES and ("linkedin" in parent_text or "basic_linkedindataexport" in parent_text):
        return "linkedin_connections"
    if suffix == ".csv" and "linkedin" in parent_text and any(hint in name for hint in LINKEDIN_INTERACTION_HINTS):
        return "linkedin_interactions"
    if suffix == ".json" and "linkedin" in parent_text and any(hint in name for hint in LINKEDIN_INTERACTION_HINTS):
        return "linkedin_interactions"
    if suffix == ".mbox":
        return "gmail_mbox"
    if suffix == ".json" and (parts & {"gmail", "mail"} or any(hint in name for hint in GMAIL_JSON_HINTS)):
        return "gmail_json"
    if suffix in {".csv", ".json", ".js"} and any(hint in name for hint in X_HINTS):
        return "x_export"
    return None


def sync_sources(
    con: sqlite3.Connection,
    *,
    scan_dirs: list[Path] | None = None,
    include_downloads: bool = False,
    mailbox_owner: str | None = None,
    linkedin_owner_name: str | None = None,
    x_owner_handle: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    dirs = scan_dirs or default_scan_dirs(include_downloads=include_downloads)
    discovered = discover_source_files(dirs)
    imported: dict[str, list[dict[str, Any]]] = {kind: [] for kind in discovered}
    errors: list[dict[str, str]] = []

    for kind, files in discovered.items():
        for path in files:
            try:
                stats = _import_one(
                    con,
                    kind=kind,
                    path=path,
                    mailbox_owner=mailbox_owner,
                    linkedin_owner_name=linkedin_owner_name,
                    x_owner_handle=x_owner_handle,
                    limit=limit,
                )
            except Exception as exc:
                record_source_run(con, source=kind, source_ref=str(path), status="error", stats={"error": str(exc)})
                errors.append({"source": kind, "path": str(path), "error": str(exc)})
                continue
            record_source_run(con, source=kind, source_ref=str(path), status="ok", stats=stats)
            imported[kind].append({"path": str(path), "stats": stats})

    value_stats = maintain_connection_values(con)
    return {
        "scan_dirs": [str(path) for path in dirs],
        "found": {kind: len(paths) for kind, paths in discovered.items()},
        "imported": imported,
        "errors": errors,
        "value_maintenance": value_stats,
    }


def summarize_sync(stats: dict[str, Any]) -> str:
    lines = ["# Network Chief Source Sync", ""]
    lines.append("## Scan")
    for path in stats["scan_dirs"]:
        lines.append(f"- {path}")
    lines.extend(["", "## Found"])
    for kind, count in stats["found"].items():
        lines.append(f"- {kind}: {count}")
    lines.extend(["", "## Imported"])
    imported_any = False
    for kind, items in stats["imported"].items():
        if not items:
            continue
        imported_any = True
        total = _sum_stats(items)
        lines.append(f"- {kind}: {len(items)} file(s), {total}")
    if not imported_any:
        lines.append("- none")
    lines.extend(["", "## Maintenance"])
    maintenance = stats["value_maintenance"]
    lines.append(f"- people_scanned: {maintenance['people_scanned']}")
    lines.append(f"- values_seen: {maintenance['values_seen']}")
    if stats["errors"]:
        lines.extend(["", "## Errors"])
        for error in stats["errors"]:
            lines.append(f"- {error['source']}: {error['path']} ({error['error']})")
    missing = [kind for kind, count in stats["found"].items() if count == 0 and kind in {"linkedin_connections", "gmail_mbox", "gmail_json"}]
    if missing:
        lines.extend(["", "## Missing Expected Exports"])
        if "linkedin_connections" in missing:
            lines.append("- LinkedIn Connections.csv was not found in the scanned directories.")
        if "gmail_mbox" in missing and "gmail_json" in missing:
            lines.append("- Gmail MBOX or connector JSON was not found in the scanned directories.")
    return "\n".join(lines)


def _import_one(
    con: sqlite3.Connection,
    *,
    kind: str,
    path: Path,
    mailbox_owner: str | None,
    linkedin_owner_name: str | None,
    x_owner_handle: str | None,
    limit: int | None,
) -> dict[str, int]:
    if kind == "linkedin_connections":
        return import_connections(con, path)
    if kind == "linkedin_interactions":
        return import_linkedin_interactions(con, path, owner_name=linkedin_owner_name, limit=limit)
    if kind == "gmail_mbox":
        return import_gmail_mbox(con, path, mailbox_owner=mailbox_owner, limit=limit)
    if kind == "gmail_json":
        return import_gmail_json(con, path, mailbox_owner=mailbox_owner, limit=limit)
    if kind == "x_export":
        return import_x_export(con, path, owner_handle=x_owner_handle, limit=limit)
    raise ValueError(f"Unsupported source kind: {kind}")


def _iter_files(root: Path, *, max_depth: int) -> list[Path]:
    files: list[Path] = []
    root = root.expanduser()
    for path in root.rglob("*"):
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        depth = len(path.relative_to(root).parts)
        if depth <= max_depth:
            files.append(path)
    return files


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    deduped: list[Path] = []
    for path in sorted(paths, key=lambda item: str(item)):
        key = str(path.expanduser())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _sum_stats(items: list[dict[str, Any]]) -> str:
    totals: dict[str, int] = {}
    for item in items:
        for key, value in item["stats"].items():
            totals[key] = totals.get(key, 0) + int(value)
    return ", ".join(f"{key}={value}" for key, value in sorted(totals.items()))
