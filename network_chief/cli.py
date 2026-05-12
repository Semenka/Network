from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .audience import build_scorecard, prepare_audience_brief
from .brief import build_daily_brief, mindmap_json
from .channels import add_or_update_channel_account, format_channel_accounts, prepare_channel_drafts
from .db import (
    connect,
    create_goal,
    init_db,
    list_channel_accounts,
    list_connection_values,
    list_goals,
    record_audience_metric,
    record_source_run,
)
from .drafts import apply_draft_event, list_drafts, set_draft_status
from .engagement import prepare_gmail_keepalive, prepare_linkedin_posts, prepare_x_comments, prepare_x_posts
from .gmail_sync import summarize_gmail_sync, sync_gmail
from .importers.gmail import import_gmail_json, import_gmail_mbox
from .importers.linkedin import import_connections, import_linkedin_interactions
from .importers.x import import_x_export
from .linkedin_rotation import (
    format_rotation_preview,
    prepare_rotating_linkedin_post,
    preview_rotation,
)
from .outbound import OutboundSafetyError, send_approved_gmail
from .sync import summarize_sync, sync_sources
from .value import maintain_connection_values
from .voice import format_voice_profile, rebuild_voice_profile


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


def _metadata(raw: str | None) -> dict[str, object] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid --metadata-json: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("--metadata-json must be a JSON object.")
    return parsed


def _confirmation_text(value: str | None, file_path: str | None) -> str:
    if value and file_path:
        raise SystemExit("Use either --confirm-exact-text or --confirm-exact-text-file, not both.")
    if file_path:
        return Path(file_path).read_text(encoding="utf-8")
    if value is None:
        raise SystemExit("--confirm-exact-text or --confirm-exact-text-file is required.")
    return value


def _account_ref(args: argparse.Namespace) -> str | None:
    return args.account_ref or args.email or args.linkedin_url or args.handle or args.chat_id


def _date_arg(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise SystemExit("--date must be YYYY-MM-DD.") from exc


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

    sync = sub.add_parser("sync-sources", help="Discover and import local LinkedIn, Gmail, and X export files.")
    sync.add_argument("--scan-dir", action="append", help="Directory to scan. Defaults to exports/. Can be repeated.")
    sync.add_argument("--include-downloads", action="store_true", help="Also scan ~/Downloads for export files.")
    sync.add_argument("--mailbox-owner", help="Your email address, used to classify Gmail incoming/outgoing.")
    sync.add_argument("--linkedin-owner-name", help="Your LinkedIn display name, used to classify LinkedIn incoming/outgoing.")
    sync.add_argument("--x-owner-handle", help="Your X handle, used to skip yourself.")
    sync.add_argument("--limit", type=int, help="Optional per-file import limit for interaction-heavy exports.")
    sync.add_argument("--out", help="Write sync report markdown to a file.")

    gmail_sync = sub.add_parser("sync-gmail", help="Import bounded recent Gmail contacts/interactions from local connector JSON or MBOX.")
    gmail_sync.add_argument("--file", help="Specific Gmail connector JSON or MBOX file.")
    gmail_sync.add_argument("--scan-dir", action="append", help="Directory to scan. Defaults to data/ and exports/. Can be repeated.")
    gmail_sync.add_argument("--include-downloads", action="store_true", help="Also scan ~/Downloads for Gmail exports.")
    gmail_sync.add_argument("--mailbox-owner", help="Your email address, used to classify incoming/outgoing.")
    gmail_sync.add_argument("--since-months", type=int, default=24)
    gmail_sync.add_argument("--max-threads", type=int, default=2000)
    gmail_sync.add_argument("--out", default="data/gmail-sync.md", help="Write Gmail sync report markdown to a file.")

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
    brief.add_argument("--mode", choices=["relationship", "audience"], default="relationship")
    brief.add_argument("--out", help="Write brief markdown to a file.")
    brief.add_argument("--no-drafts", action="store_true", help="Do not create draft records.")

    audience_brief = sub.add_parser("audience-brief", help="Build the audience-growth brief and prepare public engagement drafts.")
    audience_brief.add_argument("--limit", type=int, default=10)
    audience_brief.add_argument("--topic")
    audience_brief.add_argument("--linkedin-posts", type=int, default=2)
    audience_brief.add_argument("--x-posts", type=int, default=2)
    audience_brief.add_argument("--x-comments", type=int, default=5)
    audience_brief.add_argument("--gmail-followups", type=int, default=3)
    audience_brief.add_argument("--out", help="Write brief markdown to a file.")
    audience_brief.add_argument("--no-drafts", action="store_true", help="Do not create ranked-person draft records.")

    drafts = sub.add_parser("drafts", help="List drafts.")
    drafts.add_argument("--status", default="draft", help="Draft status or 'all'.")

    values = sub.add_parser("connection-values", help="List inferred possible value for connections.")
    values.add_argument("--type", help="Filter by value type.")
    values.add_argument("--limit", type=int, default=25)

    maintain = sub.add_parser("maintain-values", help="Recompute possible value signals from stored connection evidence.")
    maintain.add_argument("--limit", type=int, help="Optional person scan limit.")

    gmail_keepalive = sub.add_parser("prepare-gmail-keepalive", help="Create Gmail drafts for stale, high-value connections.")
    gmail_keepalive.add_argument("--limit", type=int, default=10)

    channel_drafts = sub.add_parser("prepare-channel-drafts", help="Create Gmail, LinkedIn, and Telegram contact drafts where safe.")
    channel_drafts.add_argument("--channels", default="gmail,linkedin,telegram")
    channel_drafts.add_argument("--limit", type=int, default=10)

    linkedin_posts = sub.add_parser("prepare-linkedin-posts", help="Create professional LinkedIn post drafts.")
    linkedin_posts.add_argument("--topic")
    linkedin_posts.add_argument("--count", type=int, default=3)

    daily_linkedin = sub.add_parser("prepare-daily-linkedin-post", help="Create one rotating daily LinkedIn post with a matching visual.")
    daily_linkedin.add_argument("--industry", default="energy", choices=["energy"])
    daily_linkedin.add_argument("--topic", default="AI applications in the energy industry")
    daily_linkedin.add_argument("--date", help="Post date in YYYY-MM-DD. Defaults to today.")
    daily_linkedin.add_argument("--asset-dir", default="data")
    daily_linkedin.add_argument("--out", default="data/linkedin-daily-post.md", help="Write post report markdown to a file.")
    daily_linkedin.add_argument("--rotation-index", type=int, help="Override rotation index for deterministic testing or manual selection.")

    rotation = sub.add_parser("linkedin-rotation", help="Preview the daily LinkedIn highlight/theme/visual rotation.")
    rotation.add_argument("--days", type=int, default=14)
    rotation.add_argument("--start", help="Start date in YYYY-MM-DD. Defaults to today.")

    x_posts = sub.add_parser("prepare-x-posts", help="Create X.com post drafts for community engagement.")
    x_posts.add_argument("--topic")
    x_posts.add_argument("--count", type=int, default=3)

    x_comments = sub.add_parser("prepare-x-comments", help="Create X.com comment angle drafts for tracked accounts.")
    x_comments.add_argument("--topic")
    x_comments.add_argument("--count", type=int, default=5)

    approve = sub.add_parser("approve-draft", help="Mark a draft as approved.")
    approve.add_argument("--id", required=True)
    approve.add_argument("--reason-code")
    approve.add_argument("--note")
    approve.add_argument("--external-ref")
    approve.add_argument("--metadata-json")

    reject = sub.add_parser("reject-draft", help="Mark a draft as rejected.")
    reject.add_argument("--id", required=True)
    reject.add_argument("--reason-code")
    reject.add_argument("--note")
    reject.add_argument("--external-ref")
    reject.add_argument("--metadata-json")

    send_gmail = sub.add_parser("send-approved-gmail", help="Send an approved Gmail draft after exact-text confirmation.")
    send_gmail.add_argument("--draft-id", required=True)
    send_gmail.add_argument("--confirm-exact-text", help="Must exactly equal the stored draft body.")
    send_gmail.add_argument("--confirm-exact-text-file", help="File whose contents must exactly equal the stored draft body.")
    send_gmail.add_argument("--dry-run", action="store_true", help="Validate and record send_ready without calling Gmail API.")

    event = sub.add_parser("record-draft-event", help="Record a durable approval, send, publish, response, or review event for a draft.")
    event.add_argument("--id", required=True, dest="draft_id")
    event.add_argument("--event", required=True, choices=["approve", "reject", "edit", "sent", "published", "response"])
    event.add_argument("--reason-code")
    event.add_argument("--note")
    event.add_argument("--external-ref")
    event.add_argument("--metadata-json")

    metric = sub.add_parser("record-audience-metric", help="Record LinkedIn/X audience metrics or manual outcome notes.")
    metric.add_argument("--channel", required=True)
    metric.add_argument("--metric-type", required=True)
    metric.add_argument("--value", type=int, default=1)
    metric.add_argument("--date", dest="metric_date")
    metric.add_argument("--draft-id")
    metric.add_argument("--person-id")
    metric.add_argument("--goal-id")
    metric.add_argument("--note")
    metric.add_argument("--external-ref")
    metric.add_argument("--metadata-json")

    scorecard = sub.add_parser("scorecard", help="Build the audience-growth scorecard.")
    scorecard.add_argument("--days", type=int, default=7)
    scorecard.add_argument("--out", help="Write scorecard markdown to a file.")

    accounts = sub.add_parser("channel-accounts", help="List or store per-contact Gmail, LinkedIn, and Telegram identities.")
    accounts.add_argument("--channel", choices=["gmail", "linkedin", "telegram"])
    accounts.add_argument("--person-id")
    accounts.add_argument("--account-ref")
    accounts.add_argument("--email")
    accounts.add_argument("--linkedin-url")
    accounts.add_argument("--handle")
    accounts.add_argument("--chat-id")
    accounts.add_argument("--display-name")
    accounts.add_argument("--send-enabled", action="store_true", help="Allow approved outbound on this account.")
    accounts.add_argument("--manual-only", action="store_true", help="Store identity but keep outbound disabled.")
    accounts.add_argument("--limit", type=int, default=50)

    voice = sub.add_parser("voice-profile", help="Build or inspect the private local voice profile.")
    voice_sub = voice.add_subparsers(dest="voice_command", required=True)
    voice_rebuild = voice_sub.add_parser("rebuild", help="Rebuild voice profile from sent mail and approved edits.")
    voice_rebuild.add_argument("--source", default="sent_mail,approved_edits")
    voice_rebuild.add_argument("--limit", type=int, default=120)
    voice_rebuild.add_argument("--out", help="Write voice profile report markdown to a file.")

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

    if args.command == "sync-sources":
        scan_dirs = [Path(item) for item in args.scan_dir] if args.scan_dir else None
        stats = sync_sources(
            con,
            scan_dirs=scan_dirs,
            include_downloads=args.include_downloads,
            mailbox_owner=args.mailbox_owner,
            linkedin_owner_name=args.linkedin_owner_name,
            x_owner_handle=args.x_owner_handle,
            limit=args.limit,
        )
        _write_or_print(summarize_sync(stats), args.out)
        return 0

    if args.command == "sync-gmail":
        scan_dirs = [Path(item) for item in args.scan_dir] if args.scan_dir else None
        stats = sync_gmail(
            con,
            file=args.file,
            scan_dirs=scan_dirs,
            include_downloads=args.include_downloads,
            mailbox_owner=args.mailbox_owner,
            since_months=args.since_months,
            max_threads=args.max_threads,
        )
        _write_or_print(summarize_gmail_sync(stats), args.out)
        return 1 if stats["errors"] else 0

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
        content = build_daily_brief(con, limit=args.limit, create_draft_records=not args.no_drafts, mode=args.mode)
        _write_or_print(content, args.out)
        return 0

    if args.command == "audience-brief":
        content = prepare_audience_brief(
            con,
            limit=args.limit,
            topic=args.topic,
            linkedin_posts=args.linkedin_posts,
            x_posts=args.x_posts,
            x_comments=args.x_comments,
            gmail_followups=args.gmail_followups,
            create_draft_records=not args.no_drafts,
        )
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

    if args.command == "prepare-channel-drafts":
        channels = [channel.strip() for channel in args.channels.split(",") if channel.strip()]
        try:
            prepared = prepare_channel_drafts(con, channels=channels, limit=args.limit)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        for channel, draft_ids in prepared.items():
            print(f"{channel}: {len(draft_ids)}")
            for draft_id in draft_ids:
                print(f"  {draft_id}")
        return 0

    if args.command == "prepare-linkedin-posts":
        for draft_id in prepare_linkedin_posts(con, topic=args.topic, count=args.count):
            print(draft_id)
        return 0

    if args.command == "prepare-daily-linkedin-post":
        try:
            result = prepare_rotating_linkedin_post(
                con,
                industry=args.industry,
                post_date=_date_arg(args.date),
                topic=args.topic,
                asset_dir=args.asset_dir,
                out=args.out,
                rotation_index=args.rotation_index,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.out:
            print(f"Wrote {args.out}")
        print(result["draft_id"])
        print(result.get("png_path") or result["svg_path"])
        return 0

    if args.command == "linkedin-rotation":
        print(format_rotation_preview(preview_rotation(days=args.days, start=_date_arg(args.start))))
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
        if not set_draft_status(
            con,
            args.id,
            "approved",
            reason_code=args.reason_code,
            note=args.note,
            external_ref=args.external_ref,
            metadata=_metadata(args.metadata_json),
        ):
            print(f"Draft not found: {args.id}", file=sys.stderr)
            return 1
        print(f"Approved draft {args.id}")
        return 0

    if args.command == "reject-draft":
        if not set_draft_status(
            con,
            args.id,
            "rejected",
            reason_code=args.reason_code,
            note=args.note,
            external_ref=args.external_ref,
            metadata=_metadata(args.metadata_json),
        ):
            print(f"Draft not found: {args.id}", file=sys.stderr)
            return 1
        print(f"Rejected draft {args.id}")
        return 0

    if args.command == "send-approved-gmail":
        confirmation = _confirmation_text(args.confirm_exact_text, args.confirm_exact_text_file)
        try:
            result = send_approved_gmail(
                con,
                draft_id=args.draft_id,
                confirm_exact_text=confirmation,
                dry_run=args.dry_run,
            )
        except OutboundSafetyError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == "sent" else 2

    if args.command == "record-draft-event":
        event_id = apply_draft_event(
            con,
            draft_id=args.draft_id,
            event_type=args.event,
            reason_code=args.reason_code,
            note=args.note,
            external_ref=args.external_ref,
            metadata=_metadata(args.metadata_json),
        )
        if not event_id:
            print(f"Draft not found: {args.draft_id}", file=sys.stderr)
            return 1
        print(event_id)
        return 0

    if args.command == "record-audience-metric":
        metric_id = record_audience_metric(
            con,
            channel=args.channel,
            metric_type=args.metric_type,
            value=args.value,
            metric_date=args.metric_date,
            draft_id=args.draft_id,
            person_id=args.person_id,
            goal_id=args.goal_id,
            note=args.note,
            external_ref=args.external_ref,
            metadata=_metadata(args.metadata_json),
        )
        print(metric_id)
        return 0

    if args.command == "scorecard":
        _write_or_print(build_scorecard(con, days=args.days), args.out)
        return 0

    if args.command == "channel-accounts":
        if args.send_enabled and args.manual_only:
            print("Use either --send-enabled or --manual-only, not both.", file=sys.stderr)
            return 1
        account_ref = _account_ref(args)
        if args.person_id or account_ref:
            if not args.channel or not args.person_id or not account_ref:
                print("Adding an account requires --channel, --person-id, and an account reference.", file=sys.stderr)
                return 1
            send_enabled = None
            if args.send_enabled:
                send_enabled = True
            if args.manual_only:
                send_enabled = False
            account_id = add_or_update_channel_account(
                con,
                person_id=args.person_id,
                channel=args.channel,
                account_ref=account_ref,
                display_name=args.display_name,
                send_enabled=send_enabled,
            )
            print(account_id)
            return 0
        print(
            format_channel_accounts(
                list_channel_accounts(con, channel=args.channel, limit=args.limit)
            )
        )
        return 0

    if args.command == "voice-profile":
        if args.voice_command == "rebuild":
            profile = rebuild_voice_profile(
                con,
                sources=[source.strip() for source in args.source.split(",") if source.strip()],
                limit=args.limit,
            )
            _write_or_print(format_voice_profile(profile), args.out)
            return 0

    if args.command == "mindmap":
        _write_or_print(mindmap_json(con), args.out)
        return 0

    raise AssertionError(f"Unhandled command: {args.command}")
