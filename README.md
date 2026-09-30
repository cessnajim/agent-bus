# agent-bus

Tiny durable **topic bus** for a multi-agent roster. Not Kafka — append-only jsonl, a versioned catalog, hot state snapshots, and an optional SSE dashboard.

**Public repo:** https://github.com/cessnajim/agent-bus

## Why
Agents do not share one chat brain. After a real side effect (submit, reject, Critic PASS, order received), the owning agent **publishes** a catalog topic. Everyone else **pulls** before acting. Chat DMs are best-effort; the bus is source of truth.

## Quick start
See [INSTALL.md](INSTALL.md).

```bash
git clone https://github.com/cessnajim/agent-bus.git
cd agent-bus && export AGENT_BUS_ROOT="$PWD"
cp catalog/topics.example.json catalog/topics.json   # or keep the live catalog
mkdir -p events state
./bin/agent-bus topics
```

## Portability
Set `AGENT_BUS_ROOT` on the machine that hosts the board. Agent skills must call `$AGENT_BUS_ROOT/bin/...` via Shell on that host’s `machineId` — never hard-code a username or hostname. Clone or rsync the repo (plus `events/` / `state/` if you need history) to move the board.

## Mandate
1. Only topics in `catalog/topics.json` (owner adds topics; no mid-turn invention).
2. Publish is part of **done** for catalog side effects.
3. Subscribers **pull** (`busctl` / `agent-bus state|today|tail`) before treating chat as truth.
4. After publish, DM each subscriber listed on the topic (best-effort).

## Commands
```bash
export AGENT_BUS_ROOT="${AGENT_BUS_ROOT:-$PWD}"
"$AGENT_BUS_ROOT/bin/agent-bus" topics
"$AGENT_BUS_ROOT/bin/agent-bus" state ws          # also: parks, catalant, ge, adobe, shop
"$AGENT_BUS_ROOT/bin/agent-bus" today --topic fte.submit
"$AGENT_BUS_ROOT/bin/publish.py" --topic fte.submit --actor "FTE Apply" --key '...' \
  --ref company=Acme --ref role='...' --ref source_id='...' --ref packet_path='...' \
  --delta submitted=1 --delta target=6 --delta date=YYYY-MM-DD --note 'confirm'
```

## Live topics (this deployment)
Employment / WS: `fte.submit`, `fte.reject`, `fte.hold_cleared`, `fte.park`, `critic.pass` (requires `lane`), `critic.pass.outbound`, `catalant.email_confirmed`, `catalant.pitch_submitted`, `ws.short_of_target`, `ge.synced`

Adobe Stock: `adobe.batch_submitted`, `adobe.review_logged`, `adobe.reject_reasons_captured`

Shop: `shop.order_received`, `shop.exclusive_draft_ready`, `shop.exclusive_sent`

Starter/generic topics live in `catalog/topics.example.json`.

## Layout
| Path | Role |
|---|---|
| `bin/publish.py` | Append event + update state snapshot |
| `bin/busctl.py` | Read topics / state / today / tail |
| `bin/agent-bus` | Thin portable CLI wrapper |
| `catalog/topics.json` | Live catalog (deployment) |
| `catalog/topics.example.json` | Generic starter |
| `events/` | Daily jsonl (gitignored runtime) |
| `state/` | Hot snapshots (gitignored runtime) |
| `schema/` | Event JSON Schema |
| `viz/` | Optional live dashboard |
| `docs/skills/` | Copy-paste skill templates |

## When to add a topic
Owner only. All three must be true: ≥2 subscribers, recurring, and `done_means` is nameable. One-off chat stays a DM.

## License
MIT — see [LICENSE](LICENSE).

## Distributing
See [docs/DISTRIBUTING.md](docs/DISTRIBUTING.md).
