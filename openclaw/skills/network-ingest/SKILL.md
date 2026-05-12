---
name: network-ingest
description: Import and update local network memory from LinkedIn exports, Gmail exports, X.com exports, and manual notes.
---

# Network Ingest

Use this when the user wants to update the network memory.

Commands:

```bash
network-chief sync-gmail --since-months 24 --max-threads 2000 --out data/gmail-sync.md
network-chief sync-sources --scan-dir exports --include-downloads --out data/source-sync.md
network-chief import-linkedin --file exports/Connections.csv
network-chief import-linkedin-interactions --file exports/linkedin_messages.csv --owner-name "$LINKEDIN_OWNER_NAME"
network-chief import-gmail-mbox --file exports/gmail.mbox --mailbox-owner "$MAILBOX_OWNER"
network-chief import-gmail-json --file exports/gmail_messages.json --mailbox-owner "$MAILBOX_OWNER"
network-chief import-x --file exports/x_community.json --owner-handle "$X_OWNER_HANDLE"
network-chief maintain-values
network-chief voice-profile rebuild --source sent_mail,approved_edits --out data/voice-profile.md
network-chief channel-accounts --channel telegram
network-chief connection-values --limit 30
network-chief mindmap --out data/network-map.json
```

Safety:

- Do not commit imported data.
- Prefer official exports.
- Track run counts and gaps after every import.
- After import, report counts and notable gaps.
