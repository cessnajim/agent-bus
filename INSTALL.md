# Install agent-bus

## Requirements
- Python 3.10+
- A writable directory for the git checkout (this becomes `AGENT_BUS_ROOT`)

## 1. Clone
```bash
git clone https://github.com/cessnajim/agent-bus.git
cd agent-bus
export AGENT_BUS_ROOT="$PWD"
```

## 2. Choose a catalog
```bash
# Fresh roster: start from the generic example
cp catalog/topics.example.json catalog/topics.json

# Or keep the included topics.json if you are continuing this repo's live catalog
```

## 3. Init runtime dirs (gitignored)
```bash
mkdir -p events state
test -f state/parks.json || printf '%s\n' '{"parks":{}}' > state/parks.json
```

## 4. Smoke test
```bash
"$AGENT_BUS_ROOT/bin/busctl.py" topics
"$AGENT_BUS_ROOT/bin/publish.py" \
  --topic task.done --actor Worker --key demo-1 \
  --ref item_id=demo-1 --ref lane=demo --note 'smoke'
"$AGENT_BUS_ROOT/bin/busctl.py" today
"$AGENT_BUS_ROOT/bin/busctl.py" stall
```

`stall` (alias `owed`) is the follow-through report: empty on a fresh board, then WS-short / unmatched Critic PASS / open parks once the live catalog is in use. If you kept Jim's employment catalog instead of the example, use a real topic from `busctl.py topics` for the smoke publish.

## 5. Point agents at the host
On every agent that publishes or pulls:
1. `ListMachines` / Shell `machineId` = the computer that has `AGENT_BUS_ROOT`
2. Always run `$AGENT_BUS_ROOT/bin/busctl.py` and `$AGENT_BUS_ROOT/bin/publish.py`
3. Copy the recipes in `docs/skills/` into your agent skill library: check, publish, and **chase**. Primary runs chase; specialists use the same stall report and do not invent a second loop.

## 6. Optional live viz
```bash
python3 "$AGENT_BUS_ROOT/viz/server.py"
# http://127.0.0.1:8788/
```

## Moving the board
Clone or `rsync` the whole repo (including `events/` and `state/` if you want history). Set `AGENT_BUS_ROOT` on the new host. Agents only need the new machineId.
