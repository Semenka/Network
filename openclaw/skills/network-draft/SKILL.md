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

Reject — always pass a reason so the feedback loop can learn:

```bash
network-chief reject-draft --id <draft-id> --reason wrong_timing
```

Reason codes (free text allowed): `wrong_timing | weak_context | wrong_channel | too_transactional | duplicate | not_relevant`. When one reason exceeds 30% of rejections in a window, `network-chief agent-review` raises a 🟧 finding so templates can be tuned.

Outcome tracking (closed loop):

- `network-chief push-drafts` records the Gmail thread on each pushed draft.
- The next `network-chief sync-google` detects replies on those threads and flips the draft's `outcome` to `responded` (tagging the incoming interaction `sentiment=reply`).
- People who reply get a ranking bonus (they surface higher in the next `brief`).
- For drafts you sent manually (no stored thread), `network-chief sync-google --heuristic` infers a reply when the recipient emails back after the draft was sent.

Outbound rule:

- Approved means "ready to send."
- Sending through Gmail, Telegram, WhatsApp, LinkedIn, X, or Instagram still requires explicit user confirmation for the exact message and recipient.
