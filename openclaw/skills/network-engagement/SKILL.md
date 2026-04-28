---
name: network-engagement
description: Prepare approval-gated Gmail, LinkedIn, and X.com engagement drafts from the local network database.
---

# Network Engagement

Use this when the user wants to keep the network active across Gmail, LinkedIn, and X.com.

Commands:

```bash
network-chief prepare-gmail-keepalive --limit 10
network-chief prepare-linkedin-posts --topic "$NETWORK_TOPIC" --count 3
network-chief prepare-x-posts --topic "$NETWORK_TOPIC" --count 3
network-chief prepare-x-comments --topic "$NETWORK_TOPIC" --count 5
network-chief drafts
```

Report:

- draft IDs,
- target channel,
- intended audience or person,
- rationale and active goal,
- risk if context is weak.

Rules:

- Never publish, comment, or send without explicit approval.
- Prefer specific posts and comments tied to current goals.
- Use X comments for lightweight community presence, not hard asks.
- Use LinkedIn posts for professional signal and introduction requests.
- Use Gmail drafts for high-context relationship maintenance.
