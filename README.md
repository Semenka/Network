# Network Chief

Local-first "chief of network" agent for collecting, structuring, and activating a personal network.

The first version is intentionally conservative:

- Stores all personal network memory in a local SQLite database.
- Imports LinkedIn connections from LinkedIn's official export CSV.
- Imports LinkedIn interactions, Gmail metadata, and X.com community/profile exports.
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
python3 -m venv .venv
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
network-chief maintain-values
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
```

## Importing Gmail

Preferred local path:

```bash
network-chief import-gmail-mbox --file exports/gmail.mbox --mailbox-owner you@example.com
```

Connector-style JSON is also supported:

```bash
network-chief import-gmail-json --file exports/gmail_messages.json --mailbox-owner you@example.com
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
```

The brief ranks people by:

- relationship staleness,
- resource relevance to active goals,
- relationship warmth and trust,
- recent interaction recency,
- available contact channel.

It also creates draft outreach records in the local DB.

## Draft Approval

List drafts:

```bash
network-chief drafts
```

Approve or reject:

```bash
network-chief approve-draft --id <draft-id>
network-chief reject-draft --id <draft-id>
```

Approved means "ready for you or an OpenClaw channel to send." The CLI itself does not send.

## Mind Map Export

```bash
network-chief mindmap --out data/network-map.json
```

This produces nodes and edges suitable for a graph UI.

## OpenClaw Deployment

Install OpenClaw on the Mac Mini, clone this repo, then run:

```bash
bash scripts/install_openclaw_workspace.sh
```

See [docs/DEPLOY_OPENCLAW_MAC_MINI.md](docs/DEPLOY_OPENCLAW_MAC_MINI.md).

## Privacy Principles

- Keep `data/`, `exports/`, `.env`, and database files out of git.
- Store source, confidence, and timestamps for inferred facts.
- Treat social/messaging accounts as approval-gated channels.
- Draft first, send only after explicit approval.
- Avoid scraping platforms where official export/API routes are available.
