# Making agent-bus distributable

## What ships in git
- `bin/` — publish + busctl (+ optional hooks)
- `catalog/topics.example.json` — starter catalog
- `catalog/topics.json` — this deployment's live catalog (may be roster-specific)
- `schema/` — event JSON Schema
- `viz/` — optional SSE dashboard
- `docs/` — how-to, skills templates
- `LICENSE`, `README.md`, `INSTALL.md`, `.env.example`

## What stays local (gitignored)
- `events/*.jsonl` — daily audit log (may contain packet paths, emails)
- `state/*.json` — hot snapshots
- `.publish.lock`, `.env`

## Framework vs deployment
| Layer | Portable? | Notes |
|---|---|---|
| publish/busctl/schema/viz | yes | no host paths inside |
| skills templates | yes | use `$AGENT_BUS_ROOT` |
| topics.json | deployment | rename actors/topics per roster |
| events/state | deployment data | sync only if moving the board |

## Versioning
Bump `catalog.version` when topics change. Tag releases on GitHub (`v0.x`). Consumers pin a tag; operators keep a long-lived checkout for the live board.
