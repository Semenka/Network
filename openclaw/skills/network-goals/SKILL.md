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

Goal quality checklist:

- clear target segment,
- explicit capital type,
- measurable success metric,
- bounded cadence,
- approval from the user.
