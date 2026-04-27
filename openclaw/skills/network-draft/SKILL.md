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
