from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .brief import build_daily_brief, mindmap_json
from .db import connect, create_goal, init_db, list_connection_values, list_goals, record_source_run
from .drafts import list_drafts, set_draft_status
from .engagement import prepare_gmail_keepalive, prepare_linkedin_posts, prepare_x_comments, prepare_x_posts
from .importers.gmail import import_gmail_json, import_gmail_mbox
from .importers.linkedin import import_connections, import_linkedin_interactions
from .importers.x import import_x_export
from .value import maintain_connection_values


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

    linkedin_interactions = sub.add_parser("import-linkedin-interactions", help="Import LinkedIn message/interactions CSV or JSON export.")
    linkedin_interactions.add_argument("--file", required=True, help="Path to LinkedIn messages/interactions export.")
    linkedin_interactions.add_argument("--owner-name", help="Your LinkedIn display name, used to classify incoming/outgoing.")
    linkedin_interactions.add_argument("--limit", type=int, help="Optional import limit.")

    gmail_json = sub.add_parser("import-gmail-json", help="Import connector-style Gmail JSON export.")
    gmail_json.add_argument("--file", required=True, help="Path to Gmail JSON file.")
    gmail_json.add_argument("--mailbox-owner", help="Your email address, used to classify incoming/outgoing.")
    gmail_json.add_argument("--limit", type=int, help="Optional import limit.")

    gmail_mbox = sub.add_parser("import-gmail-mbox", help="Import Google Takeout Gmail MBOX.")
    gmail_mbox.add_argument("--file", required=True, help="Path to Gmail MBOX file.")
    gmail_mbox.add_argument("--mailbox-owner", help="Your email address, used to classify incoming/outgoing.")
    gmail_mbox.add_argument("--limit", type=int, help="Optional import limit.")

    x_import = sub.add_parser("import-x", help="Import X.com community, following, tweet, or interaction CSV/JSON export.")
    x_import.add_argument("--file", required=True, help="Path to X export CSV/JSON/JS file.")
    x_import.add_argument("--owner-handle", help="Your X handle, used to skip yourself.")
    x_import.add_argument("--limit", type=int, help="Optional import limit.")

    goal = sub.add_parser("add-goal", help="Create a weekly, monthly, or quarterly network goal.")
    goal.add_argument("--title", required=True)
    goal.add_argument("--cadence", required=True, choices=["weekly", "monthly", "quarterly"])
    goal.add_argument(
        "--capital-type",
        choices=[
            "financial",
            "financial_capital",
            "human",
            "health",
            "knowledge",
            "specific_knowledge",
            "labor",
            "time_saving",
            "competence",
            "reputation",
            "social",
        ],
    )
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

    values = sub.add_parser("connection-values", help="List inferred possible value for connections.")
    values.add_argument("--type", help="Filter by value type.")
    values.add_argument("--limit", type=int, default=25)

    maintain = sub.add_parser("maintain-values", help="Recompute possible value signals from stored connection evidence.")
    maintain.add_argument("--limit", type=int, help="Optional person scan limit.")

    gmail_keepalive = sub.add_parser("prepare-gmail-keepalive", help="Create Gmail drafts for stale, high-value connections.")
    gmail_keepalive.add_argument("--limit", type=int, default=10)

    linkedin_posts = sub.add_parser("prepare-linkedin-posts", help="Create professional LinkedIn post drafts.")
    linkedin_posts.add_argument("--topic")
    linkedin_posts.add_argument("--count", type=int, default=3)

    x_posts = sub.add_parser("prepare-x-posts", help="Create X.com post drafts for community engagement.")
    x_posts.add_argument("--topic")
    x_posts.add_argument("--count", type=int, default=3)

    x_comments = sub.add_parser("prepare-x-comments", help="Create X.com comment angle drafts for tracked accounts.")
    x_comments.add_argument("--topic")
    x_comments.add_argument("--count", type=int, default=5)

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
        record_source_run(con, source="linkedin_connections", source_ref=args.file, status="ok", stats=stats)
        print(stats)
        return 0

    if args.command == "import-linkedin-interactions":
        stats = import_linkedin_interactions(con, args.file, owner_name=args.owner_name, limit=args.limit)
        record_source_run(con, source="linkedin_interactions", source_ref=args.file, status="ok", stats=stats)
        print(stats)
        return 0

    if args.command == "import-gmail-json":
        stats = import_gmail_json(con, args.file, mailbox_owner=args.mailbox_owner, limit=args.limit)
        record_source_run(con, source="gmail_json", source_ref=args.file, status="ok", stats=stats)
        print(stats)
        return 0

    if args.command == "import-gmail-mbox":
        stats = import_gmail_mbox(con, args.file, mailbox_owner=args.mailbox_owner, limit=args.limit)
        record_source_run(con, source="gmail_mbox", source_ref=args.file, status="ok", stats=stats)
        print(stats)
        return 0

    if args.command == "import-x":
        stats = import_x_export(con, args.file, owner_handle=args.owner_handle, limit=args.limit)
        record_source_run(con, source="x_export", source_ref=args.file, status="ok", stats=stats)
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

    if args.command == "connection-values":
        for value in list_connection_values(con, value_type=args.type, limit=args.limit):
            handle = f"@{value['twitter_handle']}" if value.get("twitter_handle") else value.get("primary_email") or ""
            print(f"{value['score']:3d} | {value['value_type']} | {value['full_name']} | {handle} | {value['description']}")
        return 0

    if args.command == "maintain-values":
        stats = maintain_connection_values(con, limit=args.limit)
        record_source_run(con, source="value_maintenance", source_ref=None, status="ok", stats=stats)
        print(stats)
        return 0

    if args.command == "prepare-gmail-keepalive":
        for draft_id in prepare_gmail_keepalive(con, limit=args.limit):
            print(draft_id)
        return 0

    if args.command == "prepare-linkedin-posts":
        for draft_id in prepare_linkedin_posts(con, topic=args.topic, count=args.count):
            print(draft_id)
        return 0

    if args.command == "prepare-x-posts":
        for draft_id in prepare_x_posts(con, topic=args.topic, count=args.count):
            print(draft_id)
        return 0

    if args.command == "prepare-x-comments":
        for draft_id in prepare_x_comments(con, topic=args.topic, count=args.count):
            print(draft_id)
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
