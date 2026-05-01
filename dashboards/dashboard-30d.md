# Network Chief Dashboard (30-day window)
_Captured at 2026-05-01T20:57:38Z • first snapshot_

## 1. Network breadth
- **Total contacts**: 985
- **New in last 30d**: 985
- **Identity coverage**: email 18.8% • linkedin 0.0% • twitter 0.0% • phone 85.0%
- **Source contribution** (people with at least one fact from each source):
  - `google_people` → 866
  - `gmail_api` → 120

## 2. Engagement cadence
- **Touches**: 7d 41 • 30d 388 • 90d 424
- **Active people (≥1 touch)**: 30d 115 (11.7%) • 90d 120 (12.2%)
- **Reciprocity (90d)**: incoming 365 • outgoing 59 → ratio 6.19 (incoming÷outgoing)
- **Stale-but-valuable** (value ≥60 AND no touch in 90d): **10**

## 3. Action pipeline (last 30d)
- **Drafts**: created 0 • approved 0 • rejected 0
- **Approval rate**: 0.0%
- **Sync runs**:
  - `gmail_api` (ok) ×1 — last 2026-04-28T23:01:21Z
  - `google_people` (ok) ×1 — last 2026-04-28T22:59:51Z

## 4. Value coverage
- **financial_capital** (score ≥60): 14
- **competence** (score ≥60): 10
- **specific_knowledge** (score ≥60): 17
- **time_saving** (score ≥60): 3
- **Median value score**: 78

## 5. Goal coverage
- No active goals. Add one with `network-chief add-goal`.

## 6. How to read this
- **Network breadth** answers Reid Hoffman's *I+1/I+2* question: are you adding new nodes, and do you have enough reach surface (channels) to reach them?
- **Cadence** is Keith Ferrazzi's *3-touch rule* and CMX's *active vs lurking* split — if `pct_active_30d` is below ~10%, the network is going cold.
- **Reciprocity** is Adam Grant's *givers vs takers* signal. Healthy operators run incoming÷outgoing around 0.7–1.3; a ratio <<1 over 90d means you're broadcasting more than you're receiving — investigate why.
- **Stale-but-valuable** is the single most actionable number on this dashboard. Each one is a high-value contact going cold. The weekly job is to drive this number down — use `prepare-gmail-keepalive`, then approve drafts.
- **Approval rate** measures whether the agent's drafts match your judgment. If it drifts below 50%, the keyword/value heuristics need tuning (or your goals need refreshing).
