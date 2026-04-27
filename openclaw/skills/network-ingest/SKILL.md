---
name: network-ingest
description: Import and update local network memory from LinkedIn exports, Gmail exports, and manual notes.
---

# Network Ingest

Use this when the user wants to update the network memory.

Commands:

```bash
network-chief import-linkedin --file exports/Connections.csv
network-chief import-gmail-mbox --file exports/gmail.mbox --mailbox-owner "$MAILBOX_OWNER"
network-chief import-gmail-json --file exports/gmail_messages.json --mailbox-owner "$MAILBOX_OWNER"
network-chief mindmap --out data/network-map.json
```

Safety:

- Do not commit imported data.
- Prefer official exports.
- After import, report counts and notable gaps.
