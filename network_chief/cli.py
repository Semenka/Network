from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from .audience import build_scorecard, prepare_audience_brief
from .auth.errors import AuthRequired, OAuthError, RateLimited
from .auth.tokens import TokenStore
from .brief import build_daily_brief, mindmap_json
from .channels import add_or_update_channel_account, format_channel_accounts, prepare_channel_drafts
from .cleanup import delete_people, find_misclassified
from .dashboard import compute_dashboard, previous_snapshot, render_markdown, save_snapshot
from .db import (
    connect,
    create_goal,
    db_path_from_env,
    init_db,
    list_channel_accounts,
    list_connection_values,
    list_goals,
    record_audience_metric,
    record_source_run,
)
from .discovery import discover_telegram_handles, import_telegram_csv, set_telegram_handle
from .drafts import apply_draft_event, list_drafts, record_engagement_outcome, set_draft_status
from .engagement import (
    prepare_gmail_keepalive,
    prepare_linkedin_posts,
    prepare_telegram_keepalive,
    prepare_x_comments,
    prepare_x_posts,
    render_telegram_links,
)
from .efficiency import (
    build_outcome_sweep,
    build_review_queue,
    build_source_health,
    render_outcome_sweep_markdown,
    render_review_queue_markdown,
    render_source_health_markdown,
    safe_execution_route,
)
from .gmail_sync import summarize_gmail_sync, sync_gmail
from .gbrain import fetch_gbrain_context, format_gbrain_context, sync_gbrain_summaries
from .graph import render_graph_markdown
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
from .linkedin_rotation import (
    format_rotation_preview,
    prepare_rotating_linkedin_post,
    preview_rotation,
)
from .linkedin_publish import LinkedInPublishError, publish_approved_linkedin
from .next_actions import build_next_actions, format_next_actions
from .outbound import OutboundSafetyError, send_approved_gmail
from .review import compute_review, previous_review, render_review_markdown, save_review
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

    review_queue = sub.add_parser("review-queue", help="Render a grouped, approval-focused queue for pending drafts.")
    review_queue.add_argument("--limit", type=int, default=12)
    review_queue.add_argument("--out", default="data/review-queue.md", help="Write review queue markdown to a file.")

    source_health = sub.add_parser("source-health", help="Report missing source connectors, tokens, channel coverage, and gbrain readiness.")
    source_health.add_argument("--top", type=int, default=50, help="How many top-ranked people to inspect for reachability.")
    source_health.add_argument("--out", default="data/source-health.md", help="Write source health markdown to a file.")

    outcome_sweep = sub.add_parser("outcome-sweep", help="Find approved/delivered drafts that need execution, outcomes, or LinkedIn metrics.")
    outcome_sweep.add_argument("--since-days", type=int, default=7)
    outcome_sweep.add_argument("--out", default="data/outcome-sweep.md", help="Write outcome sweep markdown to a file.")

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

    event = sub.add_parser("record-draft-event", help="Record a durable lifecycle, approval, send, publish, response, or review event for a draft.")
    event.add_argument("--id", required=True, dest="draft_id")
    event.add_argument(
        "--event",
        required=True,
        choices=[
            "proposed",
            "drafted",
            "approve",
            "approved",
            "reject",
            "rejected",
            "edit",
            "send_ready",
            "publish_ready",
            "sent",
            "published",
            "response",
            "responded",
            "converted",
            "snoozed",
            "no_response",
            "outcome",
        ],
    )
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

    next_actions = sub.add_parser("next-actions", help="Rank the next best actions across relationships, audience, and memory.")
    next_actions.add_argument("--limit", type=int, default=10)
    next_actions.add_argument("--out", default="data/next-actions.md", help="Write next-action queue markdown to a file.")
    next_actions.add_argument("--no-gbrain", action="store_true", help="Skip gbrain context enrichment for this run.")

    gbrain_context = sub.add_parser("gbrain-context", help="Fetch cited context from the local gbrain knowledge base.")
    gbrain_context.add_argument("--query", required=True)
    gbrain_context.add_argument("--limit", type=int, default=5)
    gbrain_context.add_argument("--out", help="Write context markdown to a file.")

    sync_gbrain = sub.add_parser("sync-gbrain", help="Write approved/sent/published interaction summaries back to gbrain.")
    sync_gbrain.add_argument("--since-days", type=int, default=7)
    sync_gbrain.add_argument("--mode", default="auto-summary", choices=["auto-summary"])
    sync_gbrain.add_argument("--limit", type=int, default=100)
    sync_gbrain.add_argument("--dry-run", action="store_true")

    li_publish = sub.add_parser("publish-approved-linkedin", help="Publish an approved LinkedIn draft via the official API only.")
    li_publish.add_argument("--draft-id", required=True)
    li_publish.add_argument("--confirm-exact-text", help="Must exactly equal the stored draft body.")
    li_publish.add_argument("--confirm-exact-text-file", help="File whose contents must exactly equal the stored draft body.")
    li_publish.add_argument("--visibility", default="PUBLIC", choices=["PUBLIC", "CONNECTIONS"])
    li_publish.add_argument("--dry-run", action="store_true", help="Validate token/scope/body and record publish_ready only.")

    engagement_outcome = sub.add_parser("record-engagement-outcome", help="Record reply/conversation/meeting/no-response outcomes for a draft.")
    engagement_outcome.add_argument("--draft-id", required=True)
    engagement_outcome.add_argument(
        "--outcome",
        required=True,
        choices=["useful_conversation", "reply", "meeting", "no_response", "bad_fit"],
    )
    engagement_outcome.add_argument("--note")
    engagement_outcome.add_argument("--external-ref")
    engagement_outcome.add_argument("--metadata-json")

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
    auth_li.add_argument("--scopes", help="Override LinkedIn OAuth scopes.")
    auth_li.add_argument("--posting", action="store_true", help="Request official posting scope w_member_social.")
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

    review = sub.add_parser(
        "agent-review",
        help="Weekly retrospective of agent activity + ranked efficiency recommendations (read-only).",
    )
    review.add_argument("--window", type=int, default=7)
    review.add_argument("--out", help="Write markdown to a file.")
    review.add_argument("--json", dest="json_out", help="Also write the raw JSON review to this path.")
    review.add_argument("--no-snapshot", action="store_true", help="Render only; do not persist a review_snapshots row.")

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
        "sync-sources",
        "sync-gmail",
        "sync-google",
        "sync-x",
        "sync-linkedin",
        "maintain-values",
        "brief",
        "audience-brief",
        "prepare-gmail-keepalive",
        "prepare-channel-drafts",
        "prepare-linkedin-posts",
        "prepare-daily-linkedin-post",
        "prepare-x-posts",
        "prepare-x-comments",
        "send-approved-gmail",
        "record-draft-event",
        "record-engagement-outcome",
        "record-audience-metric",
        "next-actions",
        "sync-gbrain",
        "publish-approved-linkedin",
        "approve-draft",
        "reject-draft",
        "add-goal",
        "channel-accounts",
        "voice-profile",
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

    if args.command == "review-queue":
        queue = build_review_queue(con, limit=args.limit)
        _write_or_print(render_review_queue_markdown(queue), args.out)
        return 0

    if args.command == "source-health":
        health = build_source_health(con, top_n=args.top)
        _write_or_print(render_source_health_markdown(health), args.out)
        return 0

    if args.command == "outcome-sweep":
        sweep = build_outcome_sweep(con, since_days=args.since_days)
        _write_or_print(render_outcome_sweep_markdown(sweep), args.out)
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
        row = con.execute(
            """
            SELECT d.*, p.full_name, p.primary_email, p.telegram_handle
              FROM drafts d
              LEFT JOIN people p ON p.id = d.person_id
             WHERE d.id = ?
            """,
            (args.id,),
        ).fetchone()
        if row:
            print(f"Next safe route: {safe_execution_route(con, dict(row))}")
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

    if args.command == "next-actions":
        actions = build_next_actions(con, limit=args.limit, use_gbrain=not args.no_gbrain)
        _write_or_print(format_next_actions(actions), args.out)
        return 0

    if args.command == "gbrain-context":
        results = fetch_gbrain_context(args.query, limit=args.limit)
        _write_or_print(format_gbrain_context(args.query, results), args.out)
        return 0

    if args.command == "sync-gbrain":
        try:
            stats = sync_gbrain_summaries(
                con,
                since_days=args.since_days,
                mode=args.mode,
                dry_run=args.dry_run,
                limit=args.limit,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps(stats, indent=2, sort_keys=True))
        return 0

    if args.command == "publish-approved-linkedin":
        confirmation = _confirmation_text(args.confirm_exact_text, args.confirm_exact_text_file)
        try:
            result = publish_approved_linkedin(
                con,
                draft_id=args.draft_id,
                confirm_exact_text=confirmation,
                visibility=args.visibility,
                dry_run=args.dry_run,
            )
        except (LinkedInPublishError, AuthRequired, OAuthError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == "published" else 2

    if args.command == "record-engagement-outcome":
        try:
            result = record_engagement_outcome(
                con,
                draft_id=args.draft_id,
                outcome=args.outcome,
                note=args.note,
                external_ref=args.external_ref,
                metadata=_metadata(args.metadata_json),
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if result is None:
            print(f"Draft not found: {args.draft_id}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2, sort_keys=True))
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
                scopes=args.scopes,
                posting=args.posting,
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
        print(f"linkedin authorized: {result['account']} ({result.get('name')}) scopes={result.get('scopes', '')}")
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

    if args.command == "agent-review":
        rev = compute_review(con, window_days=args.window)
        prev = previous_review(con, window_days=args.window)
        if not args.no_snapshot:
            save_review(con, rev)
        markdown = render_review_markdown(rev, previous=prev)
        _write_or_print(markdown, args.out)
        if args.json_out:
            output = Path(args.json_out)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(rev, indent=2, sort_keys=True), encoding="utf-8")
            print(f"Wrote {output}")
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
