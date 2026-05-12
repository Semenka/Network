# Network Chief

Local-first "chief of network" agent for collecting, structuring, and activating a personal network.

The first version is intentionally conservative:

- Stores all personal network memory in a local SQLite database.
- Imports LinkedIn connections from LinkedIn's official export CSV.
- Imports LinkedIn interactions, Gmail metadata, and X.com community/profile exports.
- Tracks Gmail, LinkedIn, and Telegram channel identities per person.
- Builds a private local voice profile from sent mail and approved edits.
- Maintains explicit connection-value signals: financial capital, time saving, competence, and specific knowledge.
- Builds a mind map of people, organizations, roles, resources, interactions, goals, and drafts.
- Produces a daily brief of high-leverage interactions and channel-specific engagement drafts.
- Creates draft messages only. Sending is deliberately left to an approval step.
- Ships OpenClaw workspace assets so the system can run on a separate Mac Mini later.

No private contact data is committed to this repository.

For operations guidance focused on daily interaction strategy and feedback loops, see [docs/NETWORK_HEAD_PLAYBOOK.md](docs/NETWORK_HEAD_PLAYBOOK.md).
For advanced LinkedIn, Gmail, and X.com source modules, see [docs/SOURCE_MODULES.md](docs/SOURCE_MODULES.md).

## Quick Start

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .

network-chief init
network-chief add-goal \
  --title "Reactivate AI investor network" \
  --cadence weekly \
  --capital-type financial \
  --target-segment "AI founders, angels, funds" \
  --success-metric "5 warm investor conversations"
network-chief import-linkedin --file exports/Connections.csv
network-chief import-x --file exports/x_community.json --owner-handle yourhandle
network-chief import-gmail-mbox --file exports/gmail.mbox --mailbox-owner you@example.com
network-chief sync-gmail --since-months 24 --max-threads 2000 --mailbox-owner you@example.com
network-chief sync-sources --include-downloads --mailbox-owner you@example.com
network-chief maintain-values
network-chief voice-profile rebuild --source sent_mail,approved_edits
network-chief brief --limit 10 --out data/today.md
```

Default database path:

```text
data/network.db
```

Override it with:

```bash
NETWORK_CHIEF_DB=/secure/path/network.db network-chief brief
```

## Data Model

The local database stores:

- `people`: identity, handles, emails, phones, location, confidence.
- `organizations`: companies, funds, universities, communities.
- `roles`: person-to-organization roles with source and confidence.
- `resources`: capital, knowledge, labor, health, reputation, social access.
- `connection_values`: possible value by person: financial capital, time saving, competence, specific knowledge.
- `relationships`: warmth, trust, strength, last touch, next touch.
- `interactions`: emails, messages, meetings, posts, notes.
- `goals`: weekly, monthly, quarterly network goals.
- `drafts`: proposed emails/messages/posts awaiting approval.
- `draft_events`: approve, reject, edit, sent, published, and response events with reason codes.
- `audience_metrics`: LinkedIn/X metrics and manual audience-growth notes.
- `channel_accounts`: Gmail, LinkedIn, and Telegram identities with send eligibility and confidence.
- `voice_examples`: private local samples from sent mail and approved/edited drafts.
- `voice_profile`: local style summary used to keep drafts concise, warm, specific, and non-transactional.
- `source_facts`: auditable facts and where they came from.
- `source_runs`: import and maintenance run history.

## Importing LinkedIn

Use LinkedIn's official data export:

1. LinkedIn -> Settings & Privacy -> Data privacy -> Get a copy of your data.
2. Select connections.
3. Download `Connections.csv`.
4. Run:

```bash
network-chief import-linkedin --file exports/Connections.csv
```

The importer expects common LinkedIn columns:

```text
First Name, Last Name, URL, Email Address, Company, Position, Connected On
```

LinkedIn interaction/message exports can also be imported:

```bash
network-chief import-linkedin-interactions --file exports/linkedin_messages.csv --owner-name "Andrey Semenov"
network-chief prepare-linkedin-posts --topic "AI operator network" --count 3
network-chief prepare-daily-linkedin-post --industry energy --out data/linkedin-daily-post.md
network-chief linkedin-rotation --days 7
```

To let the agent discover local exports automatically:

```bash
network-chief sync-sources --scan-dir exports --include-downloads --linkedin-owner-name "Andrey Semenov"
```

## Importing Gmail

Preferred local path:

```bash
network-chief import-gmail-mbox --file exports/gmail.mbox --mailbox-owner you@example.com
```

Connector-style JSON is also supported:

```bash
network-chief import-gmail-json --file exports/gmail_messages.json --mailbox-owner you@example.com
network-chief sync-gmail --file data/gmail-connector-sync.json --mailbox-owner you@example.com
```

Expected JSON shape:

```json
[
  {
    "from": "Alice Example <alice@example.com>",
    "to": "You <you@example.com>",
    "date": "2026-04-27T09:00:00Z",
    "subject": "Intro",
    "snippet": "Great to meet you..."
  }
]
```

Create Gmail keep-alive drafts for stale high-value connections:

```bash
network-chief prepare-gmail-keepalive --limit 10
```

For regular source maintenance, place Gmail MBOX or connector JSON files in `exports/` and run:

```bash
network-chief sync-gmail --since-months 24 --max-threads 2000 --mailbox-owner you@example.com
network-chief sync-sources --scan-dir exports --mailbox-owner you@example.com
```

`sync-gmail` is the bounded daily path: it defaults to the last 24 months, caps imports at 2,000 messages/threads per run, and excludes spam, trash, and promotions for connector JSON. By default it looks for `data/gmail-connector-sync.json`, `data/gmail-sync.json`, `exports/`, and optionally `~/Downloads`.

For unattended OpenClaw use, a local Gmail API/OAuth export job should write connector-style JSON into `data/gmail-connector-sync.json`. The Codex Gmail connector can be used interactively, but it is not available inside unattended OpenClaw jobs unless a local export path is configured.

## Channel Accounts and Voice

Store explicit per-contact channel identities:

```bash
network-chief channel-accounts --channel telegram --person-id <person-id> --handle @alice
network-chief channel-accounts --channel gmail --person-id <person-id> --email alice@example.com --send-enabled
network-chief channel-accounts --channel linkedin --person-id <person-id> --linkedin-url https://linkedin.com/in/alice --manual-only
network-chief channel-accounts --channel telegram
```

Build the private local voice profile:

```bash
network-chief voice-profile rebuild --source sent_mail,approved_edits --out data/voice-profile.md
network-chief prepare-channel-drafts --channels gmail,linkedin,telegram --limit 10
```

Telegram contact drafts are prepared only for people with explicit stored Telegram handles or chat IDs marked send-enabled. LinkedIn contact drafts remain manual-send drafts unless an official connector/API path is added later.

## Importing X.com

Import tracked community accounts, profiles, tweets, or mentions from CSV/JSON/X archive-style files:

```bash
network-chief import-x --file exports/x_community.json --owner-handle yourhandle
network-chief prepare-x-posts --topic "AI operator network" --count 3
network-chief prepare-x-comments --topic "AI operator network" --count 5
```

Example JSON shape:

```json
[
  {
    "handle": "@aliceai",
    "name": "Alice AI",
    "bio": "AI investor and automation operator.",
    "text": "Happy to share specific knowledge on machine learning.",
    "date": "2026-04-27T09:00:00Z"
  }
]
```

## Connection Value Maintenance

Refresh inferred possible value after imports:

```bash
network-chief maintain-values
network-chief connection-values --limit 30
network-chief connection-values --type financial_capital
```

Value types:

- `financial_capital`
- `time_saving`
- `competence`
- `specific_knowledge`

## Daily Brief

```bash
network-chief brief --limit 10
network-chief brief --mode audience --limit 10
```

The brief ranks people by:

- relationship staleness,
- resource relevance to active goals,
- relationship warmth and trust,
- recent interaction recency,
- available contact channel.

It also creates draft outreach records in the local DB.

## Audience Growth Cockpit

Prepare a public-network brief and channel-specific draft set in one pass:

```bash
network-chief audience-brief \
  --topic "AI operator audience" \
  --linkedin-posts 2 \
  --x-posts 2 \
  --x-comments 5 \
  --gmail-followups 3 \
  --out data/audience-today.md
```

Audience mode prioritizes active goals, LinkedIn/X context, competence and specific-knowledge signals, recent public interactions, and conversation potential. It still flags weak public context and stale relationships before recommending engagement.

For a daily energy-industry LinkedIn cadence, use the rotating post generator:

```bash
network-chief prepare-daily-linkedin-post \
  --industry energy \
  --topic "AI applications in the energy industry" \
  --asset-dir data \
  --out data/linkedin-daily-post.md
network-chief linkedin-rotation --days 14
```

The rotation changes the highlight, theme, CTA, and visual style each day. Current slots include company proof points, AI power demand, agentic operations, HPC/industrial data, conference-to-field, low-carbon optimization, and partnership maps. The generated visual stays in `data/` beside the draft report.

## Draft Approval

List drafts:

```bash
network-chief drafts
```

Approve or reject:

```bash
network-chief approve-draft --id <draft-id> --reason-code good_timing
network-chief reject-draft --id <draft-id> --reason-code weak_context
network-chief record-draft-event --id <draft-id> --event published --external-ref x:123
network-chief record-draft-event --id <draft-id> --event response --note "Booked follow-up call"
```

Approved means "the stored draft text is acceptable." Gmail still requires a second exact-text confirmation before local sending:

```bash
network-chief send-approved-gmail \
  --draft-id <draft-id> \
  --confirm-exact-text-file data/exact-approved-body.txt
```

Without `GMAIL_ACCESS_TOKEN`, this validates the approval boundary and records `send_ready` only. With `GMAIL_ACCESS_TOKEN`, it creates and sends the Gmail draft through the official Gmail API. LinkedIn publishing and LinkedIn DMs remain manual unless an official connector/API path is configured. Telegram is primarily the operator cockpit; contact sends require an explicit stored Telegram account.

Record public-network metrics and build the weekly scorecard:

```bash
network-chief record-audience-metric --channel x --metric-type replies --value 3
network-chief scorecard --days 7 --out data/scorecard.md
```

## Mind Map Export

```bash
network-chief mindmap --out data/network-map.json
```

This produces nodes and edges suitable for a graph UI.

## OpenClaw Deployment

Install OpenClaw on the Mac Mini, clone this repo, then run:

```bash
make openclaw-preflight
bash scripts/install_openclaw_workspace.sh
bash scripts/setup_openclaw_cron.sh
```

See [docs/DEPLOY_OPENCLAW_MAC_MINI.md](docs/DEPLOY_OPENCLAW_MAC_MINI.md).

## Privacy Principles

- Keep `data/`, `exports/`, `.env`, and database files out of git.
- Store source, confidence, and timestamps for inferred facts.
- Treat social/messaging accounts as approval-gated channels.
- Draft first, send only after explicit approval.
- Avoid scraping platforms where official export/API routes are available.
