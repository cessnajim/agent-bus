# Agent graph schema (v1, 2026-11-01)

A Neo4j 5 Community graph derived **only** from the agent bus (`events/*.jsonl`, `catalog/topics.json`) plus a snapshot of the Jim asks queue. The bus is still the source of truth: the graph is read-only for agents and can be rebuilt from scratch at any time (`graph/load_bus.py --rebuild`).

## Running pieces (Fedora, user `jim`, rootless podman)
| Piece | What |
|---|---|
| `agent-graph.service` | Quadlet `~/.config/containers/systemd/agent-graph.container` (copy in `graph/systemd/`). Container `agent-graph`, image `neo4j:5-community`, bound to `127.0.0.1:7474` (browser) and `127.0.0.1:7687` (bolt) only. Heap 512m-1G, page cache 512m. Data in `~/.local/share/agent-graph/data`. Restarts always, starts at boot (linger is on). |
| `agent-graph-load.timer` | Runs `agent-graph-load.service` (`graph/load_bus.py --quiet`) 3 min after boot, then every 15 min. Last run summary: `~/.local/share/agent-graph/last_load.json`. |
| Credentials | `~/.config/agent-graph/credentials` (0600, `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`). `neo4j.env` (0600) feeds the container. Never print or copy them. |
| Python | venv `~/.local/share/agent-graph/venv` (neo4j driver). Scripts re-exec into it automatically. |
| Asks snapshot | `~/.local/share/agent-graph/inputs/jim-asks-queue.jsonl`, a copy of the box file `/workspace/jim-asks/queue.jsonl` (the queue lives on the Grok Bot box, not Fedora). Refresh by copying it over; the loader mirrors whatever snapshot is there. |

## Nodes
| Label | Key | From | Main properties |
|---|---|---|---|
| `Event` | `uid` = sha1(topic, idempotency_key, ts)[:20] | every bus line | topic, domain, key, ts, epoch, day (file date), occurred_at, actor (canonical), raw_actor, note, test, backfill, lane, company, role, status, refs_json, delta_json, file, line |
| `Agent` | `name` | actors, catalog publishers/subscribers/must_act, coverage block, asks | kind (`agent`/`system`/`flow`), agent_id, silent_days, quiet_ok, weekdays_only |
| `Topic` | `name` | catalog topics + any topic seen on the bus | in_catalog, domain, description, required_refs, state_file |
| `Day` | `date` | event file names | |
| `Company` | `name` | refs.company on FTE + critic.pass events | |
| `Job` | `uid` = company-slug + "\|" + posting id | fte.* and job critic.pass events | role, source_id, ats. Posting id = UUID if present, else longest 4+ digit run (so `gh5465434008` = `5465434008`), else role slug (or the id learned from another event with the same company+role) |
| `Application` | `uid` = Job uid | fte.submit / fte.reject / fte.park / fte.hold_cleared | status (`submitted`, `rejected`, `parked`, `not_submitted`), submitted_on, submitted_epoch, ats, cv_variant, reject_type, rejected_epoch, park_reason, park_status, critic_passes |
| `CVVersion` | `name` | fte.submit refs.cv_variant | |
| `CriticReview` | `uid` = Event uid | critic.pass, critic.pass.outbound | result (`PASS`; the bus has no fail topic), lane, packet_path, epoch |
| `RejectReason` | `name` | fte.reject refs.reject_type (default `unspecified`) | |
| `ParkReason` | `name` | fte.park refs.reason | |
| `Asset` | `uid` | critic.pass.outbound refs.asset; master-CV critic passes | name, kind (lane or `cv_master`) |
| `Post` | `uid` | social.post (critic_key or key minus date), youtube.published (video_id) | channel, name, status, url |
| `AssetBatch` | `uid` = batch_id | assets.ready, adobe.batch_submitted | theme, for |
| `AdobeReview` | `uid` = date | adobe.review_logged, adobe.reject_reasons_captured | |
| `Prospect` | `uid` = slug | shop.exclusive_draft_ready / shop.exclusive_sent refs.prospect | name (emails stay only inside Event.refs_json) |
| `Opportunity` | `uid` = network + id | catalant.pitch_submitted, marketplace.pitch_submitted | network, name, rate, band |
| `Expense` | `uid` = idempotency_key | expense.logged | amount, vendor, entity, status, occurred_at |
| `Vendor`, `Entity` | `name` | expense.logged | |
| `CertWeek` | `uid` = week | ui.cert_filed | claim |
| `Finding` | `uid` = key | seo.audit | site, finding, status, owner |
| `Trip` | `uid` | travel.* refs.trip | |
| `Ask` | `uid` = queue id or sha1(ts, agent, text) | Jim asks queue snapshot | agent, ask (first 500 chars), status, urgent, epoch, answered_at, owner_confirmed |

## Relationships
| Pattern | Meaning |
|---|---|
| `(Agent)-[:PUBLISHED]->(Event)` | who published it |
| `(Event)-[:IN_TOPIC]->(Topic)`, `(Event)-[:ON_DAY]->(Day)`, `(Day)-[:NEXT]->(Day)` | time and topic spine |
| `(Event)-[:ABOUT]->(Job / Application / Asset / Post / AssetBatch / Prospect / Opportunity / Expense / CertWeek / Finding / Trip / AdobeReview)` | the work item |
| `(Event)-[:NEXT {subject, subject_label}]->(Event)` | next event on the same work item (rebuilt each load, so backfills slot in) |
| `(Job)-[:AT]->(Company)`, `(Application)-[:FOR]->(Job)`, `(Application)-[:USED_CV]->(CVVersion)` | apply structure |
| `(Application)-[:REJECTED_BY {reject_type, ts}]->(Company)`, `(Application)-[:REJECTED_FOR]->(RejectReason)` | outcome |
| `(Application)-[:PARKED_FOR {status, ts}]->(ParkReason)` | park history |
| `(Event)-[:RECORDS]->(CriticReview)-[:PASSED]->(Job / Asset)`, `(Agent)-[:REVIEWED]->(CriticReview)` | Critic gate |
| `(Expense)-[:PAID_TO]->(Vendor)`, `(Expense)-[:BILLED_TO]->(Entity)` | money out |
| `(Finding)-[:OWNED_BY]->(Agent)` | SEO finding owner |
| `(Ask)-[:ASKED_BY]->(Agent)`, `(Ask)-[:WAITING_ON]->(Agent {name:'Jim'})` | asks queue; WAITING_ON only while status is open |
| `(Agent)-[:CAN_PUBLISH]->(Topic)`, `(Agent)-[:SUBSCRIBES]->(Topic)`, `(Topic)-[:MUST_ACT]->(Agent)` | catalog wiring (rebuilt each load) |

## Not in the bus yet (so not in the graph)
- No reply / recruiter screen / interview / offer topic: apply outcomes are only `rejected` or silence.
- No `critic.fail` topic: Critic fails show only as repeat passes on the same item (`critic_rework`).
- No revenue / payment-received topic (GLG, Dialectica, Catalant pay, shop orders: `shop.order_received` has 0 events).
- Expert-network calls, Idaho UI cert answers beyond `ui.cert_filed`, and Jim asks live outside the bus (asks only via the snapshot).
- `cv_variant` is logged on 31 of 110 submits and `ats` on 65.

## Use
- Agents: `$AGENT_BUS_ROOT/bin/graphq.py list`, then `graphq.py <name> [--param k=v] [--json]`. Read-only.
- Humans: Neo4j Browser at http://127.0.0.1:7474 on Fedora (user `neo4j`, password in the credentials file).
- Query catalog: `graph/queries.cypher`.
