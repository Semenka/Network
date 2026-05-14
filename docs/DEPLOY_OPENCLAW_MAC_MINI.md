# Deploy on Mac Mini with OpenClaw

This repo is designed so the Mac Mini can run the local memory and OpenClaw workspace without private data in git.

## 1. Clone

```bash
git clone https://github.com/Semenka/Network.git
cd Network
```

## 2. Python Environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
network-chief init
```

## 3. Private Data Folders

```bash
mkdir -p data exports
cp .env.example .env
```

Set:

```text
NETWORK_CHIEF_DB=data/network.db
MAILBOX_OWNER=your-email@example.com
```

## 4. Import Sources

LinkedIn:

```bash
network-chief import-linkedin --file exports/Connections.csv
```

Gmail Takeout:

```bash
network-chief import-gmail-mbox --file exports/gmail.mbox --mailbox-owner "$MAILBOX_OWNER"
```

Daily bounded Gmail sync from connector JSON or local Gmail API/OAuth export:

```bash
network-chief sync-gmail --since-months 24 --max-threads 2000 --mailbox-owner "$MAILBOX_OWNER" --out data/gmail-sync.md
```

For unattended runs, write connector-style Gmail JSON to `data/gmail-connector-sync.json`.

## 5. Install OpenClaw

```bash
npm install -g openclaw@latest
openclaw onboard --install-daemon
openclaw doctor
```

## 6. Install Workspace Assets

```bash
bash scripts/install_openclaw_workspace.sh
```

## 7. Daily Routine

```bash
network-chief sync-gmail --since-months 24 --max-threads 2000 --out data/gmail-sync.md
network-chief sync-sources --scan-dir exports --include-downloads --out data/source-sync.md
network-chief voice-profile rebuild --source sent_mail,approved_edits --out data/voice-profile.md
network-chief prepare-daily-linkedin-post --industry energy --out data/linkedin-daily-post.md
network-chief audience-brief --limit 10 --out data/audience-today.md
network-chief prepare-channel-drafts --channels gmail,linkedin,telegram --limit 8
network-chief drafts
network-chief scorecard --days 7 --out data/scorecard.md
```

Recommended OpenClaw cron behavior:

1. Sync Gmail, LinkedIn, X, and value signals every morning.
2. Rebuild the local voice profile from private approved/sent examples.
3. Prepare one rotating daily LinkedIn post with a changed highlight/theme/visual.
4. Summarize suggested public-network interactions and draft IDs in Telegram.
5. Ask which drafts should be approved/rejected/edited.
6. Send nothing until the user approves exact text and recipient; Gmail requires a second `send-approved-gmail` confirmation.

Install the explicit Telegram cron jobs:

```bash
bash scripts/setup_openclaw_cron.sh
```

## 8. Security Notes

- Keep `data/`, `exports/`, and `.env` off GitHub.
- Use official LinkedIn exports rather than scraping.
- Use Gmail Takeout or OAuth/API access.
- Keep channel integrations allowlisted.
- Keep outbound actions approval-gated and exact-text confirmed.
