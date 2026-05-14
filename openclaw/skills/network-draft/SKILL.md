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
network-chief prepare-channel-drafts --channels gmail,linkedin,telegram --limit 10
network-chief prepare-linkedin-posts --count 3
network-chief prepare-x-posts --count 3
network-chief prepare-x-comments --count 5
```

Approve:

```bash
network-chief approve-draft --id <draft-id> --reason-code good_timing
```

Reject:

```bash
network-chief reject-draft --id <draft-id> --reason-code weak_context
```

Record outcomes:

```bash
network-chief record-draft-event --id <draft-id> --event sent --external-ref <message-ref>
network-chief record-draft-event --id <draft-id> --event published --external-ref <platform-ref>
network-chief record-draft-event --id <draft-id> --event response --note "<what happened>"
network-chief send-approved-gmail --draft-id <draft-id> --confirm-exact-text-file <file>
```

Outbound rule:

- Approved means the local draft text is acceptable.
- Gmail sending requires approved status plus a second exact-text confirmation.
- Telegram contact drafts require explicit stored handles/chat IDs.
- LinkedIn/X publishing and LinkedIn DMs remain manual unless an official connector/API path is configured.
