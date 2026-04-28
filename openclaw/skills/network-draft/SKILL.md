---
name: network-draft
description: Review, approve, reject, and prepare generated network outreach drafts.
---

# Network Drafts

Use this when the user wants to inspect proposed outreach.

List drafts:

```bash
network-chief drafts
```

Create channel-specific drafts:

```bash
network-chief prepare-gmail-keepalive --limit 10
network-chief prepare-linkedin-posts --count 3
network-chief prepare-x-posts --count 3
network-chief prepare-x-comments --count 5
```

Approve:

```bash
network-chief approve-draft --id <draft-id>
```

Reject:

```bash
network-chief reject-draft --id <draft-id>
```

Outbound rule:

- Approved means "ready to send."
- Sending through Gmail, Telegram, WhatsApp, LinkedIn, X, or Instagram still requires explicit user confirmation for the exact message and recipient.
