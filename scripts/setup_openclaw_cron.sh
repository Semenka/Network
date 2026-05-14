#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPENCLAW_BIN="${OPENCLAW_BIN:-OpenClaw}"
TELEGRAM_TARGET="${NETWORK_CHIEF_TELEGRAM_TARGET:-148594943}"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
TZ_NAME="${NETWORK_CHIEF_TZ:-Europe/Paris}"

job_id() {
  local name="$1"
  "$OPENCLAW_BIN" cron list --json | "$PYTHON_BIN" -c 'import json, sys
name = sys.argv[1]
data = json.load(sys.stdin)
for job in data.get("jobs", []):
    if job.get("name") == name:
        print(job.get("id", ""))
        break
' "$name"
}

upsert_job() {
  local name="$1"
  shift
  local id
  id="$(job_id "$name")"
  if [ -n "$id" ]; then
    echo "Updating OpenClaw cron job: $name"
    "$OPENCLAW_BIN" cron edit "$id" --name "$name" "$@"
    return 0
  fi
  echo "Creating OpenClaw cron job: $name"
  "$OPENCLAW_BIN" cron add --name "$name" "$@"
}

common=(
  --agent network-chief
  --model openai-codex/gpt-5.5
  --thinking medium
  --session isolated
  --announce
  --best-effort-deliver
  --channel telegram
  --to "$TELEGRAM_TARGET"
  --tz "$TZ_NAME"
  --timeout-seconds 900
)

upsert_job "network-chief-audience-morning" \
  "${common[@]}" \
  --cron "30 8 * * *" \
  --message "Run the Network Chief three-channel morning workflow. Execute: cd $ROOT && if [ -f .env ]; then set -a; . ./.env; set +a; fi; NETWORK_CHIEF_DB=\${NETWORK_CHIEF_DB:-data/network.db} $PYTHON_BIN -m network_chief source-health --out data/source-health.md 2>&1 && NETWORK_CHIEF_DB=\${NETWORK_CHIEF_DB:-data/network.db} $PYTHON_BIN -m network_chief sync-gmail --since-months 24 --max-threads 2000 --include-downloads --mailbox-owner \"\${MAILBOX_OWNER:-}\" --out data/gmail-sync.md 2>&1 && NETWORK_CHIEF_DB=\${NETWORK_CHIEF_DB:-data/network.db} $PYTHON_BIN -m network_chief sync-sources --include-downloads --mailbox-owner \"\${MAILBOX_OWNER:-}\" --linkedin-owner-name \"\${LINKEDIN_OWNER_NAME:-Andrey Semenov}\" --x-owner-handle \"\${X_OWNER_HANDLE:-}\" --out data/source-sync.md 2>&1 && NETWORK_CHIEF_DB=\${NETWORK_CHIEF_DB:-data/network.db} $PYTHON_BIN -m network_chief voice-profile rebuild --source sent_mail,approved_edits --out data/voice-profile.md 2>&1 && NETWORK_CHIEF_DB=\${NETWORK_CHIEF_DB:-data/network.db} $PYTHON_BIN -m network_chief prepare-daily-linkedin-post --industry energy --topic \"AI applications in the energy industry\" --asset-dir data --out data/linkedin-daily-post.md 2>&1 && NETWORK_CHIEF_DB=\${NETWORK_CHIEF_DB:-data/network.db} NETWORK_CHIEF_OWNER_NAMES=\"\${NETWORK_CHIEF_OWNER_NAMES:-Andrey Semenov}\" $PYTHON_BIN -m network_chief audience-brief --limit 12 --linkedin-posts 0 --out data/audience-today.md 2>&1 && NETWORK_CHIEF_DB=\${NETWORK_CHIEF_DB:-data/network.db} $PYTHON_BIN -m network_chief prepare-channel-drafts --channels gmail,linkedin,telegram --limit 8 > data/channel-drafts.txt 2>&1 && NETWORK_CHIEF_DB=\${NETWORK_CHIEF_DB:-data/network.db} $PYTHON_BIN -m network_chief next-actions --limit 10 --out data/next-actions.md 2>&1. Summarize source-health blockers, Gmail/source-sync counts, the rotating LinkedIn post draft ID, its highlight/theme/visual, top 10 next actions with gbrain citations where present, channel draft IDs, weak-context risks, and what needs exact approval. Do not send or publish anything."

upsert_job "network-chief-draft-review-evening" \
  "${common[@]}" \
  --cron "30 17 * * *" \
  --message "Run the Network Chief evening draft review. Execute: cd $ROOT && NETWORK_CHIEF_DB=data/network.db $PYTHON_BIN -m network_chief review-queue --limit 12 --out data/review-queue.md 2>&1 && cat data/review-queue.md. Ask for approve, reject, or edit reason codes for the grouped highest-leverage Gmail, LinkedIn, Telegram, X, and public-post drafts. Do not send or publish anything."

upsert_job "network-chief-weekly-scorecard" \
  "${common[@]}" \
  --cron "0 18 * * 0" \
  --message "Run the Network Chief weekly audience and relationship scorecard. Execute: cd $ROOT && NETWORK_CHIEF_DB=data/network.db $PYTHON_BIN -m network_chief outcome-sweep --since-days 7 --out data/outcome-sweep.md 2>&1 && NETWORK_CHIEF_DB=data/network.db $PYTHON_BIN -m network_chief scorecard --days 7 --out data/scorecard.md 2>&1 && NETWORK_CHIEF_DB=data/network.db $PYTHON_BIN -m network_chief agent-review --window 7 --out dashboards/agent-review-7d.md 2>&1 && NETWORK_CHIEF_DB=data/network.db $PYTHON_BIN -m network_chief sync-gbrain --since-days 7 --mode auto-summary 2>&1 && cat data/outcome-sweep.md && printf '\\n--- Scorecard ---\\n' && cat data/scorecard.md && printf '\\n--- Agent Review ---\\n' && cat dashboards/agent-review-7d.md. Summarize missing outcomes/metrics, channel health, stale high-value relationships, reply outcomes, gbrain writeback status, and the next voice/template/scoring adjustments."
