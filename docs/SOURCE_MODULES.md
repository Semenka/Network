# Advanced Source Modules

This guide describes the source modules that collect, maintain, and activate the local network database.

The system remains local-first:

- Use official exports or connector-generated files.
- Store imported private data under `exports/` or `data/`.
- Generate drafts only; never auto-send email, posts, comments, or direct messages.
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
network-chief maintain-values
network-chief prepare-linkedin-posts --topic "AI operator network" --count 3
```

Recommended cadence:

- Weekly: export/import LinkedIn connections.
- Weekly or after campaigns: import LinkedIn messages/interactions.
- Twice weekly: prepare LinkedIn post drafts.

## Gmail Module

Purpose:

- Track email interactions from Google Takeout MBOX or connector JSON.
- Keep relationship recency updated.
- Prepare Gmail drafts for high-value stale connections.

Commands:

```bash
network-chief import-gmail-mbox --file exports/gmail.mbox --mailbox-owner you@example.com
network-chief import-gmail-json --file exports/gmail_messages.json --mailbox-owner you@example.com
network-chief maintain-values
network-chief prepare-gmail-keepalive --limit 10
```

Recommended cadence:

- Daily on Mac Mini if using a connector export.
- Weekly if using Google Takeout files manually.
- Every morning: review Gmail drafts and approve only exact messages you want sent.

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
07:20 import Gmail connector export
07:25 import LinkedIn/X exports if present
07:30 network-chief maintain-values
07:35 network-chief brief --limit 12 --out data/today.md
07:40 network-chief prepare-gmail-keepalive --limit 10
08:00 OpenClaw sends local operator summary only
17:00 network-chief prepare-linkedin-posts --count 2
17:05 network-chief prepare-x-posts --count 2
17:10 network-chief prepare-x-comments --count 5
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
- Which contacts should be moved to a higher or lower priority band.
