---
name: network-ingest
description: Refresh local network memory. Prefer API sync (Google + X.com) when authorized; fall back to LinkedIn/Gmail/X exports otherwise.
---

# Network Ingest

Use this when the user wants to update the network memory.

Pre-flight: confirm authorizations.

```bash
network-chief auth-status
```

If a provider is missing or expired, run the matching `auth-*` command first (see the `network-auth` skill).

API-first sync (preferred):

```bash
network-chief sync-google --limit 1000
network-chief sync-x --limit 500 --max-pages 5
network-chief sync-linkedin --guided-export   # opens LinkedIn data download, watches exports/
```

Manual import fallbacks (if API sync isn't available or you already have files):

```bash
network-chief import-linkedin --file exports/Connections.csv
network-chief import-linkedin-interactions --file exports/linkedin_messages.csv --owner-name "$LINKEDIN_OWNER_NAME"
network-chief import-gmail-mbox --file exports/gmail.mbox --mailbox-owner "$MAILBOX_OWNER"
network-chief import-gmail-json --file exports/gmail_messages.json --mailbox-owner "$MAILBOX_OWNER"
network-chief import-x --file exports/x_community.json --owner-handle "$X_OWNER_HANDLE"
```

Post-process:

```bash
network-chief maintain-values
network-chief connection-values --limit 30
network-chief mindmap --out data/network-map.json
```

Safety:

- Do not commit imported data.
- All sync commands are read-only. They never send mail, post, or follow.
- Track run counts and gaps after every import (see `source_runs` table).
- After import, report counts and notable gaps. If `sync-x` returns `status="rate_limited"`, surface the reset time to the user before retrying.
