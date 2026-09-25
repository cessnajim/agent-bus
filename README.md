# agent-bus (Fedora)

Tiny durable topic bus for the agent roster. Not Kafka — jsonl + catalog + state snapshots.

## When to add a topic
Admin only. All three must be true:
1. Two or more subscribers
2. It will recur
3. `done_means` is nameable without a meeting

One-off chat stays a DM. Specialists may not invent topics mid-turn.

## Mandate
Publish is part of **done** for catalog topics. After a successful side effect (Ashby confirm, reject mail, Critic PASS, Catalant confirm), the owning agent:
1. `publish.py` with required refs
2. DMs each subscriber on the topic
3. Yields only after that

Day ledger audits live reality vs `events/YYYY-MM-DD.jsonl` and backfills misses.

`events/YYYY-MM-DD.jsonl` is the record. A key is unique per topic across days: the same `(topic, idempotency_key)` on a later day returns `status=duplicate` and writes nothing. Daily work is a new event with a new key.

On a real publish, each subscriber gets one line in `notify/<slug>/YYYY-MM-DD.jsonl`. That inbox is what the next session reads at start. The slug is the catalog name lowercased, with spaces turned into hyphens (`FTE Apply` → `fte-apply`). Wake is optional and best-effort: a subscriber `wake` string (or a topic `wake` field for a string subscriber) receives the notify line on stdin, with a 5s timeout. A non-zero exit or timeout leaves the event and the notify line in place. With no `wake` field, the file is the whole delivery.

`busctl audit` checks the log against the state snapshots and the catalog. It cannot see Ashby, Adobe Contributor, or the GE tracker. The day ledger still compares the log to live reality. `busctl rebuild` prints the snapshot diff; `--write` replaces state files from a fold of the log.

## Live topics (catalog)
Employment / WS: `fte.submit`, `fte.reject`, `fte.hold_cleared`, `critic.pass`, `catalant.email_confirmed`, `catalant.pitch_submitted`, `ws.short_of_target`, `ge.synced`

Out and About with Jim — Adobe Stock RF long-tail: `adobe.batch_submitted`, `adobe.review_logged`, `adobe.reject_reasons_captured` (state: `adobe`)

Out and About with Jim — shop: `shop.order_received`, `shop.exclusive_draft_ready` (state: `shop`)

Admin owns catalog adds. Publish is part of done.

## Commands
```bash
~/Projects/agent-bus/bin/publish.py \
  --topic fte.submit --actor "FTE Apply" --key oyster-a9b4-2026-09-24 \
  --ref company=Oyster --ref role="Senior Director, Data Platform and AI" \
  --ref source_id=a9b4d7d3 --ref packet_path=/path/to/packet \
  --delta submitted=2 --delta target=6 --note "Ashby confirm"

~/Projects/agent-bus/bin/busctl.py topics
~/Projects/agent-bus/bin/busctl.py state ws
~/Projects/agent-bus/bin/busctl.py today --topic fte.submit
~/Projects/agent-bus/bin/busctl.py audit
~/Projects/agent-bus/bin/busctl.py rebuild
~/Projects/agent-bus/bin/busctl.py rebuild --write
```

MCP stays for I/O. This bus is coordination after I/O succeeds.

## Live switchboard
```bash
python3 ~/Projects/agent-bus/viz/server.py
# open http://127.0.0.1:8788/  (LAN: http://192.168.88.13:8788/)
```
SSE pushes a fresh snapshot whenever `events/*.jsonl`, `state/*.json`, or the catalog changes. Fully dynamic — topic set, actors, and KPIs come from the bus, not hard-coded tiles.
