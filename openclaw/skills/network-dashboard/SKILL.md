---
name: network-dashboard
description: Render a regular performance dashboard summarising Network Chief actions and their effect, with deltas vs the previous snapshot and KPIs grounded in established community-builder practice.
---

# Network Dashboard

Use this when the user wants a periodic summary of network health and Network Chief's effect on it.

Recommended cadence (set in cron / OpenClaw scheduler):

- Daily 7:50: `network-chief dashboard --window 7 --out dashboards/dashboard-7d.md`
- Weekly Mon 8:05: `network-chief dashboard --window 30 --out dashboards/dashboard-30d.md --json dashboards/dashboard-30d.json`
- Monthly 1st 8:10: `network-chief dashboard --window 90 --out dashboards/dashboard-90d.md`

Each run persists a `kpi_snapshots` row for the chosen window. The next run of the same window automatically renders deltas vs that prior snapshot.

**Auto-refresh.** Every state-changing `network-chief` command (imports, syncs, `maintain-values`, `brief`, `prepare-*`, `approve-draft`, `reject-draft`, `add-goal`) now refreshes `dashboards/dashboard-30d.md` automatically as a final step. Pass `--no-dashboard` (or set `NETWORK_CHIEF_NO_DASHBOARD=1`) to suppress. Read-only and OAuth commands skip the hook.

**Network graph.** The dashboard markdown embeds a Mermaid graph of the top 40 contacts by value-score, grouped into subgraphs by primary value pillar and color-coded by recency. GitHub renders it inline. To regenerate just the graph standalone: `network-chief graph --limit 40 --out dashboards/graph.md`. The `dashboards/` directory is committed to git on purpose — see `dashboards/README.md` for the privacy trade-off if your repo is public.

What to surface to the operator after each run:

1. **Stale-but-valuable** count — the single most actionable number. If non-zero, recommend `network-chief prepare-gmail-keepalive` followed by review.
2. **Approval rate** trend — if dropping below 50%, surface as a calibration concern; ask whether goals or value heuristics need updating.
3. **Reciprocity ratio** — if 90-day inbound÷outbound drops well below 1, the operator is broadcasting more than receiving; suggest re-engaging dormant contacts.
4. **New contacts** vs identity coverage — if breadth grows but `email %` drops, intake hygiene is slipping.

KPI provenance (so you can defend the choices to the operator):

- **Breadth + I+1/I+2 reach surface** — Reid Hoffman, *The Start-Up of You*.
- **3-touch cadence + 50 lunches/year** — Keith Ferrazzi, *Never Eat Alone*.
- **Reciprocity (givers vs takers)** — Adam Grant, *Give and Take*.
- **Active vs lurking + SPACES contribution** — David Spinks / CMX Hub.
- **Approval rate as calibration metric** — standard product/funnel analytics applied to draft → human-decision.
- **Stale-but-valuable as the single weekly target** — Network Chief's own value model; close to Chris Brogan's *Trust Agents* "open conversations" framing.

Safety:

- Dashboard is read-only. No drafts created, no messages sent.
- Delta arrows only mean "vs the previous snapshot of the same window" — first run shows no deltas.
- Do not commit `data/dashboard-*.md` or `data/dashboard-*.json` (covered by the `data/` gitignore).
