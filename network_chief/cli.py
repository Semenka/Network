from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .auth.errors import AuthRequired, OAuthError, RateLimited
from .auth.tokens import TokenStore
from .brief import build_daily_brief, mindmap_json
from .cleanup import delete_people, find_misclassified
from .dashboard import compute_dashboard, previous_snapshot, render_markdown, save_snapshot
from .db import connect, create_goal, db_path_from_env, init_db, list_connection_values, list_goals, record_source_run
from .graph import render_graph_markdown
from .drafts import list_drafts, set_draft_status
from .discovery import discover_telegram_handles, import_telegram_csv, set_telegram_handle
from .engagement import (
    prepare_gmail_keepalive,
    prepare_linkedin_posts,
    prepare_telegram_keepalive,
    prepare_x_comments,
    prepare_x_posts,
    render_telegram_links,
)
from .importers.gmail import import_gmail_json, import_gmail_mbox
from .importers.google_api import (
    auth_google,
    download_drive_file,
    push_drafts_to_gmail,
    revoke_google,
    sync_gmail_messages,
    sync_google_contacts,
)
from .importers.linkedin import import_connections, import_linkedin_interactions
from .importers.linkedin_api import (
    LinkedInDMARequired,
    auth_linkedin_owner,
    guided_linkedin_export,
    sync_linkedin_dma,
)
from .importers.x import import_x_export
from .importers.x_api import auth_x, revoke_x, sync_x_following, sync_x_mentions
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
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Skip auto-refreshing dashboards/dashboard-30d.md after state-changing commands.",
    )
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

    # --- OAuth: auth + sync ----------------------------------------------------
    auth_g = sub.add_parser("auth-google", help="Authorize Google (Gmail + People API). Browser-based.")
    auth_g.add_argument("--client-id")
    auth_g.add_argument("--client-secret")
    auth_g.add_argument("--scopes")
    auth_g.add_argument("--no-browser", action="store_true", help="Print URL only, don't open a browser.")
    auth_g.add_argument("--manual", action="store_true", help="Skip the loopback server. Prints URL; finish with --redirect-url.")
    auth_g.add_argument("--redirect-url", help="Paste the full URL your browser was redirected to (after a --manual start).")

    auth_x_p = sub.add_parser("auth-x", help="Authorize X.com (OAuth 2.0 PKCE). Browser-based.")
    auth_x_p.add_argument("--client-id")
    auth_x_p.add_argument("--client-secret")
    auth_x_p.add_argument("--scopes")
    auth_x_p.add_argument("--no-browser", action="store_true")
    auth_x_p.add_argument("--manual", action="store_true")
    auth_x_p.add_argument("--redirect-url")

    auth_li = sub.add_parser("auth-linkedin", help="Authorize LinkedIn (OIDC owner identity).")
    auth_li.add_argument("--client-id")
    auth_li.add_argument("--client-secret")
    auth_li.add_argument("--no-browser", action="store_true")
    auth_li.add_argument("--manual", action="store_true")
    auth_li.add_argument("--redirect-url")

    sub.add_parser("auth-status", help="List stored OAuth tokens (no secrets shown).")

    auth_revoke = sub.add_parser("auth-revoke", help="Revoke and delete an OAuth token.")
    auth_revoke.add_argument("--provider", required=True, choices=["google", "x", "linkedin"])
    auth_revoke.add_argument("--account")

    sync_g = sub.add_parser("sync-google", help="Pull Google People connections + recent Gmail metadata.")
    sync_g.add_argument("--limit", type=int)
    sync_g.add_argument("--since", help="ISO date or unix seconds; only used for Gmail.")
    sync_g.add_argument("--skip-people", action="store_true")
    sync_g.add_argument("--skip-gmail", action="store_true")

    sync_x_p = sub.add_parser("sync-x", help="Pull X.com following list + recent mentions.")
    sync_x_p.add_argument("--limit", type=int)
    sync_x_p.add_argument("--since", help="ISO 8601 start_time for mentions.")
    sync_x_p.add_argument("--max-pages", type=int, help="Cap on paginated requests per endpoint.")
    sync_x_p.add_argument("--skip-following", action="store_true")
    sync_x_p.add_argument("--skip-mentions", action="store_true")

    sync_li = sub.add_parser("sync-linkedin", help="Capture LinkedIn owner identity; optionally walk you through a CSV export.")
    sync_li.add_argument("--guided-export", action="store_true", help="Open data-download page and watch exports/ for the archive.")
    sync_li.add_argument("--watch-dir", default="exports")
    sync_li.add_argument("--timeout", type=int, default=900)
    sync_li.add_argument("--no-browser", action="store_true")

    dash = sub.add_parser("dashboard", help="Render performance dashboard with deltas vs previous snapshot.")
    dash.add_argument("--window", type=int, default=30, help="Time window in days (default 30).")
    dash.add_argument("--out", help="Write markdown to a file.")
    dash.add_argument("--json", dest="json_out", help="Also write the raw JSON snapshot to this path.")
    dash.add_argument("--no-snapshot", action="store_true", help="Render only; do not persist a kpi_snapshots row.")
    dash.add_argument("--graph-limit", type=int, default=40, help="Top-N people in the embedded Mermaid graph.")

    graph = sub.add_parser("graph", help="Render the top-N network as a Mermaid graph (renders inline on GitHub).")
    graph.add_argument("--limit", type=int, default=40)
    graph.add_argument("--out", help="Write the Mermaid markdown to a file.")

    cleanup = sub.add_parser(
        "cleanup-people",
        help="Find (and optionally delete) person rows whose full_name is an email, domain, or org name.",
    )
    cleanup.add_argument("--delete", action="store_true", help="Actually delete; default is dry-run.")
    cleanup.add_argument("--limit", type=int, help="Only show/delete the first N candidates.")

    push = sub.add_parser(
        "push-drafts",
        help="Push pending network-chief drafts into the user's Gmail Drafts (read-and-write Google scope required).",
    )
    push.add_argument("--limit", type=int, help="Cap on how many drafts to push.")
    push.add_argument("--status", default="draft", help="Local draft status to filter on (default: draft).")

    disc_tg = sub.add_parser(
        "discover-telegram",
        help="Scan all per-person text for Telegram handles (t.me/<h>, tg:<h>); update people.telegram_handle where empty.",
    )

    set_tg = sub.add_parser(
        "set-telegram",
        help="Manually set the telegram_handle for a single person (look up by id, email, linkedin_url, or full_name).",
    )
    set_tg.add_argument("--handle", required=True, help="Telegram handle (with or without leading @, or full t.me/<h>).")
    set_tg_group = set_tg.add_mutually_exclusive_group(required=True)
    set_tg_group.add_argument("--id")
    set_tg_group.add_argument("--email")
    set_tg_group.add_argument("--linkedin-url")
    set_tg_group.add_argument("--name")

    bulk_tg = sub.add_parser(
        "import-telegram",
        help="Bulk import telegram handles from a CSV (columns: <lookup>, handle).",
    )
    bulk_tg.add_argument("--file", required=True)
    bulk_tg.add_argument("--lookup", choices=["email", "linkedin_url", "full_name"], default="email")

    tg_keep = sub.add_parser(
        "prepare-telegram-keepalive",
        help="Create Telegram drafts for stale, high-value contacts who have a telegram handle.",
    )
    tg_keep.add_argument("--limit", type=int, default=10)

    tg_links = sub.add_parser(
        "telegram-links",
        help="Render pending Telegram drafts as clickable t.me deep-links (no bot needed).",
    )
    tg_links.add_argument("--limit", type=int, help="Cap on how many drafts to render.")
    tg_links.add_argument("--out", help="Write the markdown to a file (default stdout).")
    tg_links.add_argument("--status", default="draft")

    drv = sub.add_parser(
        "import-drive",
        help="Download a Google Drive file via the saved Google token and ingest it.",
    )
    drv.add_argument("--file-id", required=True, help="Google Drive file id (the long string in the share URL).")
    drv.add_argument("--out", help="Where to save the downloaded file (default exports/<name>).")
    drv.add_argument(
        "--treat-as",
        choices=["linkedin", "linkedin-interactions", "gmail-json", "x", "none"],
        default="linkedin",
        help="Which importer to run after download. 'none' just downloads.",
    )
    drv.add_argument("--owner", help="Mailbox owner / LinkedIn handle / X handle, passed through to the importer.")
    drv.add_argument("--limit", type=int, help="Optional row limit for the chosen importer.")

    return parser


_STATE_CHANGING_COMMANDS = frozenset(
    {
        "import-linkedin",
        "import-linkedin-interactions",
        "import-gmail-json",
        "import-gmail-mbox",
        "import-x",
        "import-drive",
        "sync-google",
        "sync-x",
        "sync-linkedin",
        "maintain-values",
        "brief",
        "prepare-gmail-keepalive",
        "prepare-linkedin-posts",
        "prepare-x-posts",
        "prepare-x-comments",
        "approve-draft",
        "reject-draft",
        "add-goal",
        "cleanup-people",
        "push-drafts",
        "discover-telegram",
        "set-telegram",
        "import-telegram",
        "prepare-telegram-keepalive",
    }
)


def _maybe_auto_refresh(args, con, db_path: str) -> None:
    """After a successful state-changing command, refresh dashboards/dashboard-30d.md.

    Skipped for read-only / OAuth / dashboard commands, in-memory test DBs,
    when --no-dashboard was passed, or when NETWORK_CHIEF_NO_DASHBOARD=1 in env.
    """
    if getattr(args, "no_dashboard", False):
        return
    if os.environ.get("NETWORK_CHIEF_NO_DASHBOARD") == "1":
        return
    if db_path == ":memory:":
        return
    if args.command not in _STATE_CHANGING_COMMANDS:
        return
    try:
        snapshot = compute_dashboard(con, window_days=30)
        prev = previous_snapshot(con, window_days=30)
        save_snapshot(con, snapshot)
        markdown = render_markdown(snapshot, previous=prev, con=con)
        out_dir = Path(os.environ.get("NETWORK_CHIEF_DASHBOARDS_DIR", "dashboards"))
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "dashboard-30d.md").write_text(markdown, encoding="utf-8")
        (out_dir / "dashboard-30d.json").write_text(
            json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8"
        )
    except Exception as exc:  # pragma: no cover - best-effort hook, never break the parent command
        print(f"[dashboard] auto-refresh failed: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = db_path_from_env(args.db)
    con = _connection(args.db)
    rc = _dispatch(args, con)
    if rc == 0:
        _maybe_auto_refresh(args, con, db_path)
    return rc


def _dispatch(args, con) -> int:
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

    if args.command == "auth-google":
        try:
            result = auth_google(
                con,
                client_id=args.client_id,
                client_secret=args.client_secret,
                scopes=args.scopes,
                open_browser=not args.no_browser,
                manual=args.manual,
                redirect_url=args.redirect_url,
            )
        except (AuthRequired, OAuthError) as exc:
            print(f"auth-google failed: {exc}", file=sys.stderr)
            return 2
        if result.get("manual_step") == "open_url":
            print(f"[google] Open this URL in your browser, then paste the resulting redirect URL:")
            print(result["authorize_url"])
            print(f"[google] Finish with: network-chief auth-google --redirect-url '<paste here>'")
            return 0
        print(f"google authorized: {result['account']} (scopes={result.get('scopes', '')})")
        return 0

    if args.command == "auth-x":
        try:
            result = auth_x(
                con,
                client_id=args.client_id,
                client_secret=args.client_secret,
                scopes=args.scopes,
                open_browser=not args.no_browser,
                manual=args.manual,
                redirect_url=args.redirect_url,
            )
        except (AuthRequired, OAuthError) as exc:
            print(f"auth-x failed: {exc}", file=sys.stderr)
            return 2
        if result.get("manual_step") == "open_url":
            print(f"[x] Open this URL in your browser, then paste the resulting redirect URL:")
            print(result["authorize_url"])
            print(f"[x] Finish with: network-chief auth-x --redirect-url '<paste here>'")
            return 0
        print(f"x authorized: @{result['account']} (user_id={result.get('user_id')})")
        return 0

    if args.command == "auth-linkedin":
        try:
            result = auth_linkedin_owner(
                con,
                client_id=args.client_id,
                client_secret=args.client_secret,
                open_browser=not args.no_browser,
                manual=args.manual,
                redirect_url=args.redirect_url,
            )
        except (AuthRequired, OAuthError) as exc:
            print(f"auth-linkedin failed: {exc}", file=sys.stderr)
            return 2
        if result.get("manual_step") == "open_url":
            print(f"[linkedin] Open this URL in your browser, then paste the resulting redirect URL:")
            print(result["authorize_url"])
            print(f"[linkedin] Finish with: network-chief auth-linkedin --redirect-url '<paste here>'")
            return 0
        print(f"linkedin authorized: {result['account']} ({result.get('name')})")
        return 0

    if args.command == "auth-status":
        rows = TokenStore(con).list()
        if not rows:
            print("no oauth tokens stored. run: network-chief auth-google | auth-x | auth-linkedin")
            return 0
        for row in rows:
            print(
                f"{row['provider']:9s} | {row['account']:32s} | scopes={row['scopes']} | "
                f"expires_at={row['expires_at'] or '-'} | refreshable={'yes' if row.get('refresh_token') else 'no'}"
            )
        return 0

    if args.command == "auth-revoke":
        revoker = {"google": revoke_google, "x": revoke_x}.get(args.provider)
        if revoker is not None:
            removed = revoker(con, account=args.account)
        else:
            removed = TokenStore(con).delete(args.provider, args.account)
        print(f"removed {removed} token(s) for provider={args.provider}")
        return 0

    if args.command == "sync-google":
        combined: dict[str, int] = {}
        try:
            if not args.skip_people:
                stats = sync_google_contacts(con, limit=args.limit)
                record_source_run(con, source="google_people", source_ref=None, status=stats.get("status", "ok"), stats=stats)
                combined["people"] = stats
                print(f"google people: {stats}")
            if not args.skip_gmail:
                stats = sync_gmail_messages(con, since=args.since, limit=args.limit)
                record_source_run(con, source="gmail_api", source_ref=None, status=stats.get("status", "ok"), stats=stats)
                combined["gmail"] = stats
                print(f"gmail messages: {stats}")
        except AuthRequired as exc:
            print(f"sync-google: {exc}", file=sys.stderr)
            return 2
        return 0

    if args.command == "sync-x":
        try:
            if not args.skip_following:
                stats = sync_x_following(con, limit=args.limit, max_pages=args.max_pages)
                record_source_run(con, source="x_api_following", source_ref=None, status=stats.get("status", "ok"), stats=stats)
                print(f"x following: {stats}")
            if not args.skip_mentions:
                stats = sync_x_mentions(con, since=args.since, limit=args.limit, max_pages=args.max_pages)
                record_source_run(con, source="x_api_mentions", source_ref=None, status=stats.get("status", "ok"), stats=stats)
                print(f"x mentions: {stats}")
        except AuthRequired as exc:
            print(f"sync-x: {exc}", file=sys.stderr)
            return 2
        except RateLimited as exc:
            print(f"sync-x rate-limited (reset_at={exc.reset_at}): {exc}", file=sys.stderr)
            return 0
        return 0

    if args.command == "dashboard":
        snapshot = compute_dashboard(con, window_days=args.window)
        prev = previous_snapshot(con, window_days=args.window)
        if not args.no_snapshot:
            save_snapshot(con, snapshot)
        markdown = render_markdown(snapshot, previous=prev, con=con, graph_limit=args.graph_limit)
        _write_or_print(markdown, args.out)
        if args.json_out:
            output = Path(args.json_out)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
            print(f"Wrote {output}")
        return 0

    if args.command == "graph":
        markdown = render_graph_markdown(con, limit=args.limit)
        _write_or_print(markdown, args.out)
        return 0

    if args.command == "discover-telegram":
        stats = discover_telegram_handles(con)
        record_source_run(
            con,
            source="telegram_discovery",
            source_ref=None,
            status="ok",
            stats={k: v for k, v in stats.items() if k != "samples"},
        )
        print(stats)
        return 0

    if args.command == "set-telegram":
        result = set_telegram_handle(
            con,
            handle=args.handle,
            person_id=args.id,
            email=args.email,
            linkedin_url=args.linkedin_url,
            full_name=args.name,
        )
        if not result["matched"]:
            print(f"set-telegram: {result.get('reason', 'no match')}", file=sys.stderr)
            return 1
        print(f"set telegram_handle for {result['full_name']} → @{result['handle']}")
        return 0

    if args.command == "import-telegram":
        stats = import_telegram_csv(con, args.file, lookup=args.lookup)
        record_source_run(
            con, source="telegram_csv_import", source_ref=args.file, status="ok",
            stats={"matched": stats["matched"], "unmatched": stats["unmatched"]},
        )
        print(stats)
        return 0

    if args.command == "prepare-telegram-keepalive":
        for draft_id in prepare_telegram_keepalive(con, limit=args.limit):
            print(draft_id)
        return 0

    if args.command == "telegram-links":
        markdown = render_telegram_links(con, status=args.status, limit=args.limit)
        _write_or_print(markdown, args.out)
        return 0

    if args.command == "push-drafts":
        try:
            stats = push_drafts_to_gmail(con, status=args.status, limit=args.limit)
        except AuthRequired as exc:
            print(f"push-drafts: {exc}", file=sys.stderr)
            return 2
        record_source_run(con, source="gmail_drafts_push", source_ref=None, status="ok", stats={
            "pushed": stats["pushed"], "skipped": stats["skipped"], "errors": len(stats["errors"]),
        })
        for item in stats["items"]:
            print(f"  pushed: {item['name']} <{item['to']}> → gmail_draft={item['gmail_draft_id']}")
        for err in stats["errors"]:
            print(f"  skipped: {err}", file=sys.stderr)
        print(f"\n{stats['pushed']} drafts pushed to Gmail; {stats['skipped']} skipped.")
        return 0

    if args.command == "cleanup-people":
        candidates = find_misclassified(con)
        if args.limit:
            candidates = candidates[: args.limit]
        if not candidates:
            print("No misclassified person rows found.")
            return 0
        for cand in candidates:
            print(
                f"  {cand['id'][:8]}  reason={cand['reason']:22s}  full_name={cand['full_name']!r}"
            )
        print(f"\n{len(candidates)} candidates", "(dry-run)" if not args.delete else "(deleting)")
        if args.delete:
            removed = delete_people(con, [c["id"] for c in candidates])
            print(f"Deleted {removed} people (cascade cleaned roles/interactions/values).")
        return 0

    if args.command == "import-drive":
        try:
            meta = download_drive_file(con, file_id=args.file_id, dest=args.out)
        except (AuthRequired, OAuthError) as exc:
            print(f"import-drive: {exc}", file=sys.stderr)
            return 2
        print(f"downloaded: {meta['name']} → {meta['path']} ({meta['size_bytes']} bytes, {meta['mime_type']})")

        treat = args.treat_as
        path = meta["path"]
        if treat == "linkedin":
            stats = import_connections(con, path)
            record_source_run(con, source="linkedin_connections", source_ref=path, status="ok", stats=stats)
        elif treat == "linkedin-interactions":
            stats = import_linkedin_interactions(con, path, owner_name=args.owner, limit=args.limit)
            record_source_run(con, source="linkedin_interactions", source_ref=path, status="ok", stats=stats)
        elif treat == "gmail-json":
            stats = import_gmail_json(con, path, mailbox_owner=args.owner, limit=args.limit)
            record_source_run(con, source="gmail_json", source_ref=path, status="ok", stats=stats)
        elif treat == "x":
            stats = import_x_export(con, path, owner_handle=args.owner, limit=args.limit)
            record_source_run(con, source="x_export", source_ref=path, status="ok", stats=stats)
        else:
            stats = {"status": "downloaded_only"}
        print(stats)
        return 0

    if args.command == "sync-linkedin":
        try:
            if not TokenStore(con).get("linkedin"):
                auth_linkedin_owner(con, open_browser=not args.no_browser)
            if args.guided_export:
                stats = guided_linkedin_export(
                    con,
                    watch_dir=args.watch_dir,
                    timeout_s=args.timeout,
                    open_browser=not args.no_browser,
                )
                print(f"linkedin guided export: {stats}")
            else:
                try:
                    stats = sync_linkedin_dma(con)
                    print(f"linkedin dma: {stats}")
                except LinkedInDMARequired as exc:
                    print(str(exc))
                    print("Hint: pass --guided-export to walk through a CSV download instead.")
        except (AuthRequired, OAuthError) as exc:
            print(f"sync-linkedin: {exc}", file=sys.stderr)
            return 2
        return 0

    raise AssertionError(f"Unhandled command: {args.command}")
