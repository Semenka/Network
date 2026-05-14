#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="${NETWORK_CHIEF_DB:-$ROOT/data/network.db}"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"

if [[ ! -f "$DB" ]]; then
  echo "Database not found: $DB" >&2
  exit 1
fi

DRAFT_ID=$(sqlite3 "$DB" "SELECT id FROM drafts WHERE channel='linkedin_post' ORDER BY created_at DESC LIMIT 1;")
if [[ -z "$DRAFT_ID" ]]; then
  echo "No LinkedIn post draft found. Generate one first:"
  echo "  NETWORK_CHIEF_DB=$DB $PYTHON_BIN -m network_chief prepare-daily-linkedin-post --industry energy --out data/linkedin-daily-post.md"
  exit 0
fi

POSTED_REF=$(sqlite3 "$DB" "SELECT external_ref FROM draft_events WHERE draft_id='$DRAFT_ID' AND event_type='published' ORDER BY created_at DESC LIMIT 1;")

if [[ -z "$POSTED_REF" ]]; then
  echo "Reminder for Andrey: review and post today's LinkedIn energy draft."
  echo "Draft ID: $DRAFT_ID"
  echo "After posting, capture metrics: impressions, reactions, comments, reposts, follows/profile views, useful replies/DMs, follow-up candidates."
  echo "Then record outcome + metrics with:"
  echo "  NETWORK_CHIEF_DB=$DB $PYTHON_BIN -m network_chief record-draft-event --id $DRAFT_ID --event published --external-ref linkedin:<url-or-post-id>"
  echo "  NETWORK_CHIEF_DB=$DB $PYTHON_BIN -m network_chief record-audience-metric --channel linkedin --metric-type impressions --value <n> --draft-id $DRAFT_ID"
  echo "  NETWORK_CHIEF_DB=$DB $PYTHON_BIN -m network_chief record-engagement-outcome --draft-id $DRAFT_ID --outcome useful_conversation --note '<what worked>'"
  exit 0
fi

echo "LinkedIn draft already marked published: $DRAFT_ID"
echo "Post ref: $POSTED_REF"
echo "Please provide latest metrics: impressions, reactions, comments, reposts, follows/profile views, useful replies/DMs, follow-up candidates."
echo "After recording metrics, run:"
echo "  NETWORK_CHIEF_DB=$DB $PYTHON_BIN -m network_chief scorecard --days 7 --out data/scorecard.md"
echo "Suggested adaptation rule: reuse the CTA option that generated the highest comment-to-impression ratio."
