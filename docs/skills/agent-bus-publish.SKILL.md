---
name: Agent bus publish
description: >-
  Publish a catalog side effect to agent-bus before treating the work as done.
---
# Agent bus publish

```bash
export AGENT_BUS_ROOT="${AGENT_BUS_ROOT:-$HOME/Projects/agent-bus}"
"$AGENT_BUS_ROOT/bin/publish.py" \
  --topic <topic> --actor "<Agent>" --key '<idempotency-key>' \
  --ref k=v --note '...'
```

Only topics in `catalog/topics.json`. After publish, DM each subscriber from stdout. Subscribers still pull.
