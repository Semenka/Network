---
name: network-brief
description: Produce a daily ranked brief of relationship actions and draft outreach candidates.
---

# Network Brief

Use this for the daily network routine.

Run:

```bash
network-chief sync-gmail --since-months 24 --max-threads 2000 --out data/gmail-sync.md
network-chief maintain-values
network-chief voice-profile rebuild --source sent_mail,approved_edits --out data/voice-profile.md
network-chief brief --limit 10 --out data/today.md
network-chief brief --mode audience --limit 10 --out data/audience-people.md
network-chief prepare-channel-drafts --channels gmail,linkedin,telegram --limit 8
```

Then summarize:

- top suggested interactions,
- why each person matters now,
- connection value signal: financial capital, time saving, competence, or specific knowledge,
- public audience-growth signal when in audience mode,
- which goal it supports,
- draft IDs created,
- any missing context.

Do not send anything. Ask the user which drafts to approve.
