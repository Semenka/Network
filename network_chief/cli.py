from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .brief import build_daily_brief, mindmap_json
from .db import connect, create_goal, init_db, list_goals
from .drafts import list_drafts, set_draft_status
from .importers.gmail import import_gmail_json, import_gmail_mbox
from .importers.linkedin import import_connections


def _connection(path: str | None):
    con = connect(path)
    init_db(con)
    return con


def _write_or_print(content: str, out: str | None) -> None:
    if out:
        output = Path(out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
        print(f"Wrote {output}")
    else:
        print(content)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="network-chief", description="Local-first chief-of-network agent.")
    parser.add_argument("--db", help="SQLite database path. Defaults to NETWORK_CHIEF_DB or data/network.db.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Initialize the local database.")

    linkedin = sub.add_parser("import-linkedin", help="Import LinkedIn official Connections.csv export.")
    linkedin.add_argument("--file", required=True, help="Path to LinkedIn Connections.csv.")

    gmail_json = sub.add_parser("import-gmail-json", help="Import connector-style Gmail JSON export.")
    gmail_json.add_argument("--file", required=True, help="Path to Gmail JSON file.")
    gmail_json.add_argument("--mailbox-owner", help="Your email address, used to classify incoming/outgoing.")
    gmail_json.add_argument("--limit", type=int, help="Optional import limit.")

    gmail_mbox = sub.add_parser("import-gmail-mbox", help="Import Google Takeout Gmail MBOX.")
    gmail_mbox.add_argument("--file", required=True, help="Path to Gmail MBOX file.")
    gmail_mbox.add_argument("--mailbox-owner", help="Your email address, used to classify incoming/outgoing.")
    gmail_mbox.add_argument("--limit", type=int, help="Optional import limit.")

    goal = sub.add_parser("add-goal", help="Create a weekly, monthly, or quarterly network goal.")
    goal.add_argument("--title", required=True)
    goal.add_argument("--cadence", required=True, choices=["weekly", "monthly", "quarterly"])
    goal.add_argument("--capital-type", choices=["financial", "human", "health", "knowledge", "labor", "reputation", "social"])
    goal.add_argument("--target-segment")
    goal.add_argument("--success-metric")
    goal.add_argument("--starts-on")
    goal.add_argument("--ends-on")

    goals = sub.add_parser("goals", help="List goals.")
    goals.add_argument("--all", action="store_true", help="Include inactive goals.")

    brief = sub.add_parser("brief", help="Build a daily network brief and create drafts.")
    brief.add_argument("--limit", type=int, default=10)
    brief.add_argument("--out", help="Write brief markdown to a file.")
    brief.add_argument("--no-drafts", action="store_true", help="Do not create draft records.")

    drafts = sub.add_parser("drafts", help="List drafts.")
    drafts.add_argument("--status", default="draft", help="Draft status or 'all'.")

    approve = sub.add_parser("approve-draft", help="Mark a draft as approved.")
    approve.add_argument("--id", required=True)

    reject = sub.add_parser("reject-draft", help="Mark a draft as rejected.")
    reject.add_argument("--id", required=True)

    mindmap = sub.add_parser("mindmap", help="Export graph-style mind map JSON.")
    mindmap.add_argument("--out", help="Write JSON to a file.")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    con = _connection(args.db)

    if args.command == "init":
        print("Initialized Network Chief database.")
        return 0

    if args.command == "import-linkedin":
        stats = import_connections(con, args.file)
        print(stats)
        return 0

    if args.command == "import-gmail-json":
        stats = import_gmail_json(con, args.file, mailbox_owner=args.mailbox_owner, limit=args.limit)
        print(stats)
        return 0

    if args.command == "import-gmail-mbox":
        stats = import_gmail_mbox(con, args.file, mailbox_owner=args.mailbox_owner, limit=args.limit)
        print(stats)
        return 0

    if args.command == "add-goal":
        goal_id = create_goal(
            con,
            title=args.title,
            cadence=args.cadence,
            capital_type=args.capital_type,
            target_segment=args.target_segment,
            success_metric=args.success_metric,
            starts_on=args.starts_on,
            ends_on=args.ends_on,
        )
        print(goal_id)
        return 0

    if args.command == "goals":
        for goal in list_goals(con, status=None if args.all else "active"):
            print(f"{goal['id']} | {goal['cadence']} | {goal['title']} | {goal['status']}")
        return 0

    if args.command == "brief":
        content = build_daily_brief(con, limit=args.limit, create_draft_records=not args.no_drafts)
        _write_or_print(content, args.out)
        return 0

    if args.command == "drafts":
        status = None if args.status == "all" else args.status
        for draft in list_drafts(con, status=status):
            print(f"{draft['id']} | {draft['status']} | {draft['channel']} | {draft.get('full_name') or 'unknown'} | {draft.get('subject') or ''}")
        return 0

    if args.command == "approve-draft":
        if not set_draft_status(con, args.id, "approved"):
            print(f"Draft not found: {args.id}", file=sys.stderr)
            return 1
        print(f"Approved draft {args.id}")
        return 0

    if args.command == "reject-draft":
        if not set_draft_status(con, args.id, "rejected"):
            print(f"Draft not found: {args.id}", file=sys.stderr)
            return 1
        print(f"Rejected draft {args.id}")
        return 0

    if args.command == "mindmap":
        _write_or_print(mindmap_json(con), args.out)
        return 0

    raise AssertionError(f"Unhandled command: {args.command}")
