# Network Chief Tools

Repository:

- `/Users/andrey/Documents/network`

Runtime:

- Use `python3.12` or an activated Python 3.12+ virtual environment.
- Default database: `data/network.db`.
- Private inputs stay in `exports/`; generated private outputs stay in `data/`.

Preflight:

```bash
make openclaw-preflight
```

Daily cockpit:

```bash
NETWORK_CHIEF_DB=data/network.db python3.12 -m network_chief sync-gmail --since-months 24 --max-threads 2000 --out data/gmail-sync.md
NETWORK_CHIEF_DB=data/network.db python3.12 -m network_chief sync-sources --include-downloads --out data/source-sync.md
NETWORK_CHIEF_DB=data/network.db python3.12 -m network_chief voice-profile rebuild --source sent_mail,approved_edits --out data/voice-profile.md
NETWORK_CHIEF_DB=data/network.db python3.12 -m network_chief prepare-daily-linkedin-post --industry energy --topic "AI applications in the energy industry" --out data/linkedin-daily-post.md
NETWORK_CHIEF_DB=data/network.db python3.12 -m network_chief audience-brief --limit 12 --out data/audience-today.md
NETWORK_CHIEF_DB=data/network.db python3.12 -m network_chief prepare-channel-drafts --channels gmail,linkedin,telegram --limit 8
NETWORK_CHIEF_DB=data/network.db python3.12 -m network_chief drafts
```

Approval and outcomes:

```bash
NETWORK_CHIEF_DB=data/network.db python3.12 -m network_chief approve-draft --id <id> --reason-code good_timing
NETWORK_CHIEF_DB=data/network.db python3.12 -m network_chief reject-draft --id <id> --reason-code weak_context
NETWORK_CHIEF_DB=data/network.db python3.12 -m network_chief send-approved-gmail --draft-id <id> --confirm-exact-text-file <file>
NETWORK_CHIEF_DB=data/network.db python3.12 -m network_chief record-draft-event --id <id> --event published --external-ref <platform-ref>
NETWORK_CHIEF_DB=data/network.db python3.12 -m network_chief record-draft-event --id <id> --event response --note "<outcome>"
NETWORK_CHIEF_DB=data/network.db python3.12 -m network_chief scorecard --days 7 --out data/scorecard.md
```

Outbound rule:

- Telegram is for operating the agent.
- Gmail sending is allowed only after approved draft status plus a second exact recipient and exact text confirmation.
- Telegram contact drafts are allowed only for contacts with explicit stored handles/chat IDs.
- LinkedIn/X publishing remains manual unless an official connector/API path is explicitly configured.
