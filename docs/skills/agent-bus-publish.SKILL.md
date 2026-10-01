---
name: Agent bus publish
description: >-
  Publish a catalog side effect to agent-bus before treating the work as done.
  fte.submit submitted=N is the absolute count for the day.
---
# Agent bus publish

```bash
export AGENT_BUS_ROOT="${AGENT_BUS_ROOT:-$HOME/Projects/agent-bus}"
"$AGENT_BUS_ROOT/bin/publish.py" \
  --topic <topic> --actor "<Agent>" --key '<idempotency-key>' \
  --ref k=v --note '...'
```

Only topics in `catalog/topics.json`. After publish, DM each subscriber from stdout. Subscribers still pull (`busctl stall` included).

## `submitted` is absolute

On `fte.submit` and `ws.short_of_target`, `--delta submitted=N` (and the `submitted` ref) is the **absolute** count for that date, not an increment. The second submit of the day is `submitted=2`. The ws snapshot stores `max(claim, number of fte.submit events today)`, so a repeated `submitted=1` cannot leave state stuck at 1. If publish prints `submitted_floor`, the claim was below the ledger and the snapshot was raised. Pass a higher absolute only when some receipts are not on the bus.

Re-publishing `ws.short_of_target` inside the debounce window with the same counts returns `chase_warn`. That warning is not follow-through. See `docs/skills/agent-bus-chase.SKILL.md`.
