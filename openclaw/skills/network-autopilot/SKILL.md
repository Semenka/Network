---
name: network-autopilot
description: Run Network Chief autonomously — one command senses all sources, scores, self-directs from agent-review findings, prepares outreach, sends within policy, and reports. Outbound is policy-gated.
---

# Network Autopilot

Use this to operate Network Chief unattended. One cycle = SENSE → THINK → ACT → REPORT.

```bash
network-chief autopilot --once
```

Recommended cadence (OpenClaw cron / crontab):

```cron
# every 6 hours
0 */6 * * * cd /path/to/Network && set -a && . ./.env && set +a && network-chief autopilot --once >> data/autopilot.log 2>&1
```

What each phase does (all call existing, tested functions):

- **SENSE**: sync-google (+ Gmail reply detection), sync-x. Missing/expired tokens are skipped, never fatal.
- **THINK**: maintain-values, rank people, compute agent-review findings.
- **SELF-DIRECT**: high-leverage findings trigger cheap, reversible actions automatically — e.g. a stale-but-valuable backlog runs `brief` + `prepare-gmail-keepalive`; a stale Google sync re-syncs. Judgement calls (idle drafts, low approval rate, rejection patterns, no goals) are surfaced for you, never auto-decided.
- **ACT**: prepare drafts/posts for every channel; stage them into Gmail Drafts; send only what the policy permits. ACT now includes four auto-engagement paths, each gated by its own policy switch (all default off):
  - **auto-reply Gmail** — drafts a short acknowledgement to fresh incoming gmail from real-human contacts (a robust bot/no-reply filter excludes `noreply@`, `donotreply@`, `notifications@`, marketing aliases, sub-mailers like `mail.x.com` / `notif.x.fr`, sendgrid-style hash relays, and the operator's own addresses).
  - **auto-publish X posts** — posts approved `x_post` drafts via `POST /2/tweets`.
  - **auto-reply X mentions** — replies to recent incoming mentions with `in_reply_to_tweet_id`.
  - **LinkedIn publish-assist** — `network-chief publish-linkedin` opens LinkedIn's share dialog and copies the approved post body to the clipboard (LinkedIn has no public Posts API for personal apps; this is the closest "automation" the platform allows).
- **REPORT**: refresh `dashboards/dashboard-30d.md`, `dashboards/agent-review-7d.md`, and `dashboards/autopilot-digest.md` (the digest surfaces per-channel auto-action counts).

## Autonomy policy (the send gate)

Shipped **disarmed**: `level=0` (prepare-only) and `dry_run=true`. Installing or scheduling the autopilot can never send a message until you explicitly arm it.

```bash
network-chief policy show
network-chief policy set --level 1 --dry-run false        # bounded auto-send (Gmail)
network-chief policy set --level 2 --dry-run false --daily-send-cap 10   # full auto-send
network-chief policy set --level 0                        # disarm again
```

Levels:

- **L0 prepare-only** (default): everything is staged into Gmail Drafts / Telegram links; you approve and send.
- **L1 bounded**: auto-sends only to existing contacts who have already replied to you, never first-contact.
- **L2 full**: auto-sends to anyone eligible.

Per-channel auto-engagement switches (all default **false**, all independent of `level`):
```bash
network-chief policy set --gmail-auto-reply true   # auto-acknowledge fresh inbound from humans
network-chief policy set --x-post true             # auto-publish approved x_post drafts
network-chief policy set --x-reply true            # auto-reply to incoming X mentions
```
Set `NETWORK_CHIEF_SELF_EMAILS=alt1@…,alt2@…` to add your other addresses to the self-skip list. The Gmail auto-reply path requires the `gmail.send` scope (re-run `auth-google --manual` if your token is older).

Guardrails enforced at **every** level ≥ 1 (never relaxed): `consent_status='active'` only, verified recipient address only, `daily_send_cap`, `quiet_hours`, `min_days_between_touches`. Use `network-chief set-consent --status opted_out` to permanently exclude someone.

## Platform reality

| Channel | Sense | Prepare | Auto-send |
|---|---|---|---|
| Gmail | yes | yes (Drafts) | yes (gmail.send; the one auto-send channel) |
| X | yes | yes | possible but free-tier rate-limited; opt-in via `--channels` |
| LinkedIn | identity/CSV | post drafts | no API — assisted (you post) |
| Telegram | discovery | deep-links | bot can't initiate — assisted (you tap) |
| Instagram / WhatsApp | no | no | no API |

So the autopilot autonomously **senses + thinks + prepares across everything**, and autonomously **sends on Gmail** (and X if you enable it) within the policy.

Safety:

- `autopilot` writes an audit row per phase to `source_runs` — `network-chief agent-review` shows the autopilot's own activity.
- `--dry-run` forces no-send for a single run regardless of policy.
- Arming real sends prints a loud `⚠ ARMED` confirmation.
