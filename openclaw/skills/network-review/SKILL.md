---
name: network-review
description: Weekly retrospective on what the agent did and where it was inefficient. Read-only; emits ranked, command-shaped recommendations.
---

# Network Review

Use this when the user wants to understand what Network Chief actually did over the past week and where the leverage is hiding.

Recommended cadence (set in cron / OpenClaw scheduler):

- Weekly Mon 08:05: `network-chief agent-review --window 7 --out dashboards/agent-review-7d.md --json dashboards/agent-review-7d.json`

Each run persists a `review_snapshots` row keyed by `window_days`. The next run with the same window adds a "compared to previous review" header so trends are visible week over week.

What the report contains:

1. **Summary** — top-3 highest-leverage findings, each with the exact command to run.
2. **Activity log** — all `source_runs` in the window grouped by source, plus a "Missing" list of sources we expect on a weekly cadence (e.g., `google_people`, `value_maintenance`, `telegram_discovery`) but didn't see.
3. **Pipeline throughput** — drafts created/approved/rejected, channel mix, **subject diversity**, mean & max **idle time** for still-`draft` rows.
4. **KPI deltas** — paired oldest/newest `kpi_snapshots` within the window: `total_people`, `pct_active_30d`, `stale_high_value`, `approval_rate_pct`.
5. **Findings & recommendations** — color-tagged (🟥 critical / 🟧 attention / 🟨 info / 🟩 ok), each with the prescribed command.

How to act on findings:

- 🟥 indicates a leverage gap that breaks a downstream KPI loop (e.g., starving the approval-rate signal). Run the prescribed command immediately.
- 🟧 indicates a workflow/setup gap (e.g., no goals, no Telegram coverage, low subject diversity). Address before the next review.
- 🟨 is informational — usually about cron / cadence wiring.
- 🟩 means no waste detected; keep going.

Safety:

- `agent-review` is read-only. It never creates drafts, sends messages, or mutates contacts. The only DB write is the `review_snapshots` row (skip with `--no-snapshot`).
- Recommendations are always printed as commands — never auto-executed. The user remains the decider.
- `dashboards/agent-review-*.md` is gitignored under the existing `dashboards/dashboard-*.md` pattern when you want to keep retrospectives off the public repo.
