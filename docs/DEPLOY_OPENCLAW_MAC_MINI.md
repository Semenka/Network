# Deploy on Mac Mini with OpenClaw

This repo is designed so the Mac Mini can run the local memory and OpenClaw workspace without private data in git.

## 1. Clone

```bash
git clone https://github.com/Semenka/Network.git
cd Network
```

## 2. Python Environment

```bash
python3 -m venv .venv
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
network-chief brief --limit 10 --out data/today.md
network-chief drafts
```

Recommended OpenClaw cron behavior:

1. Run the brief every morning.
2. Summarize the suggested interactions.
3. Ask which drafts should be approved.
4. Send nothing until the user approves exact text and recipient.

## 8. Security Notes

- Keep `data/`, `exports/`, and `.env` off GitHub.
- Use official LinkedIn exports rather than scraping.
- Use Gmail Takeout or OAuth/API access.
- Keep channel integrations allowlisted.
- Keep outbound actions approval-gated.
