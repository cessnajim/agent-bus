# Shop Exclusives (Fedora note)

Local note for the Fedora agent-bus copy. Cursor skill on the box (`/home/box/agent-data/workflows/agent-bus-publish/SKILL.md`) is not edited from here.

## Ramp (locked 2026-09-27)
Sales draft → Writing Critic PASS → send From `jim@northidaholabs.com` via Proton or Bridge SMTP under standing auth. Report after the fact (Admin FYI). Jim is out of the per-send loop.

## Topic
`shop.exclusive_sent`
- publishers: Sales, Admin
- subscribers: Admin, Sales, Marketing
- required_refs: prospect, to, subject, asset (optional draft_path)
- done_means: Bridge/Proton send confirmed; dashboard ledger updated; Admin FYI
- state_file: shop

## Ledger
`state/shop_exclusives.json` is source of truth for queue + sent rows.
Queue statuses: `queued` | `drafting` | `critic` | `draft_ready` | `sent`.

## Dashboard
- URL: http://127.0.0.1:8788/shop.html
- API: GET `/api/shop` → ledger + today's `shop.*` events from `events/YYYY-MM-DD.jsonl`

## Publish after each send
```bash
~/Projects/agent-bus/bin/publish.py \
  --topic shop.exclusive_sent --actor Sales --key prospect-slug-YYYY-MM-DD \
  --ref prospect="Name" --ref to=buyer@example.com \
  --ref subject="Subject" --ref asset="Asset" \
  --note "Bridge SMTP confirmed"
```
Then update the exclusives ledger (move/complete the queue row into `sent`).
