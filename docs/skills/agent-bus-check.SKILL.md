---
name: Agent bus check
description: >-
  Pull agent-bus state/today for subscribed topics before treating chat memory as truth.
---
# Agent bus check (pull)

```bash
export AGENT_BUS_ROOT="${AGENT_BUS_ROOT:-$HOME/Projects/agent-bus}"
# Shell on the machine that hosts AGENT_BUS_ROOT
"$AGENT_BUS_ROOT/bin/busctl.py" topics
"$AGENT_BUS_ROOT/bin/busctl.py" state <name>
"$AGENT_BUS_ROOT/bin/busctl.py" today --topic <topic>
```

Publish alone is not enough. Chat DMs are best-effort; jsonl + state are source of truth.
