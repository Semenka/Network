# Network Head Playbook (OpenClaw + Network Chief)

This playbook is for running your "head of network" agent on your Mac Mini with OpenClaw as the orchestration layer and `network-chief` as local memory/decision support.

## 1) What to review daily (operator cockpit)

Run each morning:

```bash
network-chief brief --limit 12 --out data/today.md
network-chief drafts
network-chief goals
network-chief mindmap --out data/network-map.json
```

Then classify the top 12 into four buckets:

- **Maintain**: warm contacts that should not go stale.
- **Activate**: people who can help active weekly goals now.
- **Expand**: second-degree/introduction opportunities.
- **Compound**: people where helping them increases your long-term reputation.

## 2) Best interaction surfaces by intent

Use channels by intent, not convenience:

- **Gmail (high-context, strategic asks):** investor updates, partnership asks, formal introductions, long-form follow-ups.
- **Telegram (fast coordination):** quick check-ins, scheduling, lightweight status nudges.
- **LinkedIn DM (professional signal):** career transitions, business collaborations, reconnecting dormant professional ties.
- **LinkedIn posts (broadcast):** quarterly themes, asks to the wider network, social proof loops.
- **WhatsApp (high-trust personal ties):** close operators, trusted advisors, warm intro nudges.

Rule of thumb:

- If message needs context/history, pick Gmail.
- If it needs speed, pick Telegram.
- If it needs public professional positioning, pick LinkedIn.

## 3) Draft templates your agent should produce

The agent should produce all drafts as **approval-gated** artifacts.

### A. Gmail draft (strategic)

Subject:

- `Quick catch-up on <goal>`

Body pattern:

1. Personal context sentence.
2. What changed since last touch.
3. Specific ask or offer.
4. Concrete next step (date range).

### B. Telegram draft (tactical)

Pattern:

- 2-4 lines max.
- One objective.
- One CTA.

Example style:

- "Hey <name>, quick one: I’m mapping <theme> this week. Could we do a 15-min catch-up Thu/Fri?"

### C. LinkedIn DM draft (reconnect)

Pattern:

- Mention a real update from their profile/post.
- One sentence on why it matters to your current priority.
- Lightweight request.

### D. LinkedIn post draft (network expansion)

Pattern:

- Hook: one insight from your current work.
- 3 bullets: what you learned / what you’re building / who you want to meet.
- CTA: "If you know X, message me".

## 4) Feedback loop that makes the agent better

Create a closed loop with measurable outcomes:

1. **Suggestion generated** (agent logs why this person/channel now).
2. **Human action** (approve/edit/reject with reason code).
3. **Delivery outcome** (sent/not sent).
4. **Response outcome** (replied, meeting booked, intro made, no response).
5. **Goal attribution** (which weekly/monthly/quarterly goal moved).
6. **Model update** (increase/decrease channel + persona score weights).

Minimum reason codes for reject/edit:

- wrong_timing
- weak_context
- wrong_channel
- too_transactional
- duplicate_recent_touch

Use these to retrain ranking rules weekly.

## 5) Weekly optimization routine (Sunday)

1. Export KPIs for previous 7 days.
2. Review top/bottom 10 suggestions.
3. Adjust scoring weights (staleness vs goal-match vs trust).
4. Update templates that underperform by channel.
5. Add 1 new network segment experiment for next week.

Target KPIs:

- Draft approval rate.
- Reply rate by channel.
- Meeting conversion rate.
- Intro conversion rate.
- Time-to-response median.
- % interactions tied to an active goal.

## 6) Deployment pattern for reliability

Use two environments:

- **Prod (Mac Mini):** always-on daemon + scheduled jobs + encrypted local DB.
- **Dev (this machine):** template/scoring changes, tests, dry-runs on sample exports.

Recommended cadence on Mac Mini:

- 07:30 daily: ingest + brief generation.
- 08:00 daily: OpenClaw sends operator summary only.
- 17:30 daily: follow-up reminder on unapproved drafts.
- Sunday: weekly scorecard + parameter tuning report.

## 7) Practical upgrade backlog (highest ROI first)

1. Add channel-specific draft rendering in CLI output.
2. Add reject/edit reason capture to draft status updates.
3. Add per-channel performance table (reply and meeting conversion).
4. Add relationship health bands (green/yellow/red) in brief.
5. Add "intro path" detection (A -> B -> target) from interaction graph.
6. Add LLM tone memory from your accepted edits (private local profile).

## 8) Governance and safety

- Never auto-send externally without explicit approval.
- Keep personal data local (`data/`, `exports/`, `.env`).
- Tag inferred facts and confidence separately from verified facts.
- Keep monthly privacy reviews for channel connectors and OAuth scopes.

