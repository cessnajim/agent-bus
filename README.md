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
3. Subscribers **pull** (`busctl` / `agent-bus state|today|tail|stall`) before treating chat as truth.
4. After publish, DM each subscriber listed on the topic (best-effort).
5. Primary runs **chase** when the next catalogued step does not show up. Proof is the next bus event.

## Follow-through (board + primary + chase)

The bus is the board (events + state). It does not wake agents. Chat DMs are best-effort. Follow-through is one mechanism for every primary (catalog owner `Admin`; on this deployment Grok Bot acts as that primary) and every specialist:

1. **Pull** `busctl stall` (alias `busctl owed`).
2. **Detect** a stall: Work Seeking still short of target, a `critic.pass` with no later `fte.submit` or `fte.park` for the same company and role, or an open `chase.owed` row. Open parks are counted, not nagged.
3. **Nudge** the stall `owner` (DM, best-effort) and publish `chase.owed` so the nudge is on the board.
4. **Verify** on the next pull. The stall clears when the expected topic is published. A chat ack is not proof.

Debounce defaults to 30 minutes (`follow_through.debounce_minutes`). Do not re-publish `ws.short_of_target` inside that window when submitted and target are unchanged. Copy `docs/skills/agent-bus-chase.SKILL.md` with the check and publish skills.

`fte.submit` `--delta submitted=N` is the **absolute** count for that date, not an increment. Two receipts both sent as `submitted=1` used to leave `state/ws.json` stuck at 1. The snapshot now stores `max(claim, today's fte.submit event count)`. Pass the real absolute total when some submits are off the bus; the floor only prevents an undercount against the ledger.

## Commands
```bash
export AGENT_BUS_ROOT="${AGENT_BUS_ROOT:-$PWD}"
"$AGENT_BUS_ROOT/bin/agent-bus" topics
"$AGENT_BUS_ROOT/bin/agent-bus" state ws          # also: parks, owed, catalant, ge, adobe, shop
"$AGENT_BUS_ROOT/bin/agent-bus" stall             # alias: owed
"$AGENT_BUS_ROOT/bin/agent-bus" today --topic fte.submit
# submitted=N is the absolute count for the date. The second submit of the day is submitted=2.
"$AGENT_BUS_ROOT/bin/publish.py" --topic fte.submit --actor "FTE Apply" --key '...' \
  --ref company=Acme --ref role='...' --ref source_id='...' --ref packet_path='...' \
  --delta submitted=2 --delta target=6 --delta date=YYYY-MM-DD --note 'confirm'
```

## Live topics (this deployment)
Employment / WS: `fte.submit`, `fte.reject`, `fte.hold_cleared`, `fte.park`, `critic.pass` (requires `lane`), `critic.pass.outbound`, `catalant.email_confirmed`, `catalant.pitch_submitted`, `ws.short_of_target`, `ge.synced`

Follow-through: `chase.owed` (state `owed`). `busctl stall` also reads chase blocks on `critic.pass` and `ws.short_of_target`.

Adobe Stock: `adobe.batch_submitted`, `adobe.review_logged`, `adobe.reject_reasons_captured`

Shop: `shop.order_received`, `shop.exclusive_draft_ready`, `shop.exclusive_sent`

Starter/generic topics live in `catalog/topics.example.json`.

## Layout
| Path | Role |
|---|---|
| `bin/publish.py` | Append event + update state snapshot |
| `bin/busctl.py` | Read topics / state / today / tail / stall |
| `bin/chase.py` | Stall detection and absolute `submitted` floor |
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

## Tests
```bash
python3 -m unittest tests.test_chase
```

## Changelog

### 0.3.0 — 2026-10-01
- Chase substrate: catalog `follow_through`, topic `chase.owed`, `busctl stall` / `owed`, skill `docs/skills/agent-bus-chase.SKILL.md`. Primary pulls, nudges the owner, and treats the next bus event as proof.
- `fte.submit` / ws `submitted` is an absolute day count. The snapshot is floored at today's `fte.submit` event count so a repeated `submitted=1` cannot stick.
- Park rows merge for any topic whose `state_file` is `parks` (id is `source_id` or `item_id`).

## License
MIT — see [LICENSE](LICENSE).

## Distributing
See [docs/DISTRIBUTING.md](docs/DISTRIBUTING.md).
