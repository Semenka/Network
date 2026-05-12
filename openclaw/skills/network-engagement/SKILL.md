---
name: network-engagement
description: Prepare approval-gated Gmail, LinkedIn, Telegram, and X.com engagement drafts from the local network database.
---

# Network Engagement

Use this when the user wants to keep the network active across Gmail, LinkedIn, Telegram, and X.com.

Commands:

```bash
network-chief audience-brief --limit 10 --out data/audience-today.md
network-chief prepare-daily-linkedin-post --industry energy --out data/linkedin-daily-post.md
network-chief linkedin-rotation --days 7
network-chief prepare-gmail-keepalive --limit 10
network-chief prepare-channel-drafts --channels gmail,linkedin,telegram --limit 10
network-chief prepare-linkedin-posts --topic "$NETWORK_TOPIC" --count 3
network-chief prepare-x-posts --topic "$NETWORK_TOPIC" --count 3
network-chief prepare-x-comments --topic "$NETWORK_TOPIC" --count 5
network-chief drafts
network-chief scorecard --days 7 --out data/scorecard.md
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
- Use Telegram contact drafts only for explicitly stored Telegram accounts.
- Record published/sent/response outcomes with `record-draft-event`.
- Record public metrics with `record-audience-metric`.
