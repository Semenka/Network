# Network Chief Heartbeat

OpenClaw should use explicit cron jobs for this agent, not broad channel routing.

Recommended schedule:

- 08:30 Europe/Paris daily: sync Gmail/LinkedIn/X, rebuild voice profile, prepare the rotating LinkedIn post, run the audience-growth morning brief, and announce it to Telegram.
- 17:30 Europe/Paris daily: list unreviewed drafts and ask for approve/reject/edit reason codes.
- 18:00 Europe/Paris Sunday: build the weekly scorecard and suggest scoring/template adjustments.

Morning command from the repo root:

```bash
NETWORK_CHIEF_DB=data/network.db python3.12 -m network_chief sync-gmail --since-months 24 --max-threads 2000 --out data/gmail-sync.md
NETWORK_CHIEF_DB=data/network.db python3.12 -m network_chief sync-sources --include-downloads --out data/source-sync.md
NETWORK_CHIEF_DB=data/network.db python3.12 -m network_chief voice-profile rebuild --source sent_mail,approved_edits --out data/voice-profile.md
NETWORK_CHIEF_DB=data/network.db python3.12 -m network_chief prepare-daily-linkedin-post --industry energy --out data/linkedin-daily-post.md
NETWORK_CHIEF_DB=data/network.db python3.12 -m network_chief audience-brief --limit 12 --out data/audience-today.md
NETWORK_CHIEF_DB=data/network.db python3.12 -m network_chief prepare-channel-drafts --channels gmail,linkedin,telegram --limit 8
```

Evening review command:

```bash
NETWORK_CHIEF_DB=data/network.db python3.12 -m network_chief drafts
```

Weekly scorecard command:

```bash
NETWORK_CHIEF_DB=data/network.db python3.12 -m network_chief scorecard --days 7 --out data/scorecard.md
```
