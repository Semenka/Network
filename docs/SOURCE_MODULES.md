# Advanced Source Modules

This guide describes the source modules that collect, maintain, and activate the local network database.

The system remains local-first:

- Use official exports or connector-generated files.
- Store imported private data under `exports/` or `data/`.
- Generate drafts first; send Gmail only after approval plus exact-text confirmation.
- Track why each connection may be valuable with auditable source evidence.

## Connection Value Model

`network-chief` now maintains explicit possible-value signals in `connection_values`.

Supported value types:

- `financial_capital`: investors, funds, angels, capital allocators.
- `time_saving`: operators, automation, systems, delivery, chief-of-staff style leverage.
- `competence`: builders, engineers, product leaders, lawyers, operators, strategic experts.
- `specific_knowledge`: AI, research, medicine, data, deep technical/domain expertise.

Run value maintenance after every import:

```bash
network-chief maintain-values
network-chief connection-values --limit 30
network-chief connection-values --type financial_capital
```

## LinkedIn Module

Purpose:

- Regularly import all connections.
- Track roles, companies, connection dates, and LinkedIn interactions.
- Prepare professional posts to keep the network engaged.

Commands:

```bash
network-chief import-linkedin --file exports/Connections.csv
network-chief import-linkedin-interactions --file exports/linkedin_messages.csv --owner-name "Andrey Semenov"
network-chief sync-sources --scan-dir exports --include-downloads --linkedin-owner-name "Andrey Semenov"
network-chief maintain-values
network-chief prepare-linkedin-posts --topic "AI operator network" --count 3
network-chief prepare-daily-linkedin-post --industry energy --out data/linkedin-daily-post.md
network-chief linkedin-rotation --days 7
```

Recommended cadence:

- Weekly: export/import LinkedIn connections.
- Weekly or after campaigns: import LinkedIn messages/interactions.
- Daily: prepare one rotating energy/AI LinkedIn post with a changing highlight, theme, CTA, and visual.
- Twice weekly: prepare additional LinkedIn post drafts for non-news audience growth.

## Gmail Module

Purpose:

- Track email interactions from Google Takeout MBOX, connector JSON, or a local Gmail API/OAuth export job.
- Keep relationship recency updated.
- Prepare Gmail drafts for high-value stale connections.
- Build a private local voice profile from sent mail and approved edits.

Commands:

```bash
network-chief import-gmail-mbox --file exports/gmail.mbox --mailbox-owner you@example.com
network-chief import-gmail-json --file exports/gmail_messages.json --mailbox-owner you@example.com
network-chief sync-gmail --since-months 24 --max-threads 2000 --mailbox-owner you@example.com --out data/gmail-sync.md
network-chief sync-sources --scan-dir exports --mailbox-owner you@example.com
network-chief maintain-values
network-chief voice-profile rebuild --source sent_mail,approved_edits --out data/voice-profile.md
network-chief prepare-gmail-keepalive --limit 10
```

`sync-gmail` defaults to recent, bounded sync: last 24 months, max 2,000 messages/threads per run, excluding spam, trash, and promotions for connector JSON. It looks for `data/gmail-connector-sync.json`, `data/gmail-sync.json`, `exports/`, or a file passed with `--file`.

Recommended cadence:

- Daily on Mac Mini if using a connector export or local Gmail API/OAuth export job.
- Weekly if using Google Takeout files manually.
- Every morning: review Gmail drafts and approve only exact messages you want sent.
- Send path: `approve-draft` first, then `send-approved-gmail --draft-id <id> --confirm-exact-text-file <file>`.

## Channel Identity Module

Purpose:

- Map each person to Gmail, LinkedIn, and Telegram identities.
- Separate known identity from send eligibility.
- Allow Telegram contact drafts only for explicit stored handles or chat IDs.

Commands:

```bash
network-chief channel-accounts --channel gmail --person-id <person-id> --email alice@example.com --send-enabled
network-chief channel-accounts --channel linkedin --person-id <person-id> --linkedin-url https://linkedin.com/in/alice --manual-only
network-chief channel-accounts --channel telegram --person-id <person-id> --handle @alice
network-chief prepare-channel-drafts --channels gmail,linkedin,telegram --limit 10
```

Rules:

- Gmail identities imported from Gmail are send-eligible, but still require draft approval and exact-text confirmation.
- LinkedIn identities imported from exports are manual-only unless an official connector/API path is added later.
- Telegram identities are send-eligible only when explicitly stored as handles/chat IDs.

## Voice Module

Purpose:

- Learn a local private style profile from sent Gmail, approved drafts, and edited drafts.
- Keep draft tone concise, warm, specific, non-transactional, and goal-aware.
- Avoid exposing raw private message text in reports.

Commands:

```bash
network-chief voice-profile rebuild --source sent_mail,approved_edits --out data/voice-profile.md
```

## X.com Module

Purpose:

- Track community accounts, mentions, replies, and lightweight interactions.
- Prepare post drafts to grow reach.
- Prepare comment-angle drafts for tracked accounts.

Supported input:

- CSV with columns such as `handle`, `name`, `bio`, `text`, `date`.
- JSON lists with profile-like records.
- X archive-style JavaScript/JSON records containing `tweet` objects and mentions.

Commands:

```bash
network-chief import-x --file exports/x_community.json --owner-handle yourhandle
network-chief maintain-values
network-chief audience-brief --topic "AI operator network" --x-posts 3 --x-comments 5
network-chief prepare-x-posts --topic "AI operator network" --count 3
network-chief prepare-x-comments --topic "AI operator network" --count 5
```

Recommended cadence:

- Weekly: import tracked community accounts or archive exports.
- Daily: prepare 1-3 post drafts and 3-5 comment-angle drafts.
- Only publish/comment manually after reviewing tone and context.

## Mac Mini Schedule

Example cron-style routine:

```text
07:20 local Gmail API/OAuth export writes data/gmail-connector-sync.json
07:25 import LinkedIn/X exports if present
08:20 network-chief sync-gmail --since-months 24 --max-threads 2000 --out data/gmail-sync.md
08:22 network-chief sync-sources --include-downloads --out data/source-sync.md
08:25 network-chief maintain-values
08:27 network-chief voice-profile rebuild --out data/voice-profile.md
08:28 network-chief prepare-daily-linkedin-post --industry energy --out data/linkedin-daily-post.md
08:30 network-chief audience-brief --limit 12 --linkedin-posts 0 --out data/audience-today.md
08:35 OpenClaw sends Telegram operator summary only
17:00 network-chief prepare-linkedin-posts --count 2
17:05 network-chief prepare-x-posts --count 2
17:10 network-chief prepare-x-comments --count 5
Sunday 18:00 network-chief scorecard --days 7 --out data/scorecard.md
```

## Review Loop

Each draft should be reviewed with one of these outcomes:

- Approve: exact draft is ready for manual sending/publishing.
- Edit: useful idea but tone, ask, or timing needs improvement.
- Reject: bad timing, weak context, wrong channel, or too transactional.

Use the weekly review to compare:

- Which value type produced replies or meetings.
- Which channel produced useful responses.
- Which templates needed the most edits.
- Which voice examples led to approvals/replies.
- Which contacts should be moved to a higher or lower priority band.
