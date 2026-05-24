---
name: network-goals
description: Create, review, and refine weekly, monthly, and quarterly network goals.
---

# Network Goals

Use this when the user wants to set or review network goals.

Add a goal:

```bash
network-chief add-goal \
  --title "Reactivate AI investor network" \
  --cadence weekly \
  --capital-type financial \
  --target-segment "AI founders, angels, funds" \
  --success-metric "5 warm investor conversations"
```

List goals:

```bash
network-chief goals
```

Make the success metric measurable with milestones (rendered as progress bars in the dashboard, e.g. `warm investor conversations: 2/5 (40%)`):

```bash
network-chief add-milestone --goal-id <goal-id> --metric "warm investor conversations" --target 5 --current 0
network-chief update-milestone --id <milestone-id> --current 2
```

Goal quality checklist:

- clear target segment,
- explicit capital type,
- measurable success metric,
- bounded cadence,
- approval from the user.
