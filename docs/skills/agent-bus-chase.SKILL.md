---
name: Agent bus chase
description: >-
  Follow-through for the primary agent: pull busctl stall, nudge the owner of a
  stalled next step, publish chase.owed, and verify the next bus event.
---
# Agent bus chase (follow-through)

The bus is the board. It does not wake anyone. Chat DMs are best-effort. **Primary** (catalog `follow_through.primary`, usually `Admin`; Grok Bot acts as that primary on this deployment) runs this skill. Specialists use the same stall report and do not keep a second chase loop in chat.

Proof is the next catalog event on the bus. A chat reply is not proof.

```bash
export AGENT_BUS_ROOT="${AGENT_BUS_ROOT:-$HOME/Projects/agent-bus}"
# Shell on the machine that hosts AGENT_BUS_ROOT
"$AGENT_BUS_ROOT/bin/busctl.py" stall
```

`owed` is an alias of `stall`.

## When something is stalled

`stall` lists rows with `nudge_due: true` when the next step is overdue and nobody has nudged inside `debounce_minutes` (default 30):

| Signal | Owner | Owed next topic | Clears when |
|---|---|---|---|
| `submitted < target` for today (`ws.short_of_target` chase) | FTE Apply | `fte.submit` | another `fte.submit`, or `ws.short_of_target` with `inventory=empty` |
| `critic.pass` with no later submit/park for the same company and role | FTE Apply | `fte.submit` or `fte.park` | matching `fte.submit` or `fte.park` |
| open row in `state/owed.json` | `owner` on that row | `owed_topic` | that topic is published (matching company/role/… if those refs were set), or `chase.owed` with `status=cleared` |

`open_parks.count` is a summary. Do not nudge a park whose `do_not_reprompt` is true. Parks are not chase stalls.

A row with `overdue: false` is still inside the debounce window after the last progress event. Watch it. Do not nudge yet.

Duplicate `ws.short_of_target` with the same submitted/target inside the debounce window is **hard-blocked** (`status=debounced`). Prefer `busctl stall`. **One publisher for short:** Admin / day-ledger only. FTE Apply pulls stall; may publish `inventory=empty` once to clear. Pass `--delta submitted=N` as the **absolute** count for the date, never as `+1`.

## Nudge

For each stall with `nudge_due: true`:

1. DM `owner` (best-effort). Also DM catalog subscribers of `chase.owed` if `owner` is not already on that list.
2. Publish the nudge. Copy `stall_id`, `expect`, `since`, and `match` from the stall row. `status=open`.

```bash
"$AGENT_BUS_ROOT/bin/publish.py" \
  --topic chase.owed --actor "Admin" --key "chase-<stall_id>-<YYYYMMDDTHHMM>" \
  --ref stall_id='<stall_id>' --ref owed_topic='<owed_topic>' \
  --ref owner='<owner>' --ref status=open --ref since='<since>' \
  --ref expect='fte.submit,fte.park' \
  --ref company='<company>' --ref role='<role>' \
  --note 'nudge; proof is the next bus event'
```

Omit match refs the stall does not have. An empty match means any later event on `expect` clears the row. `expect` is a comma-separated list; it defaults to `owed_topic`.

Actor is the primary's name (`Admin` or `Grok Bot`).

## Verify

Pull `stall` again later. The stall is gone, or `nudge_due` is false because a fresh progress event reset the clock. Publish stdout may include `chase_cleared` when the proof event closes an owed row.

If the owner answered in chat and the owed topic is still missing, the stall stays open. Nudge again only after debounce.

`critic.pass` chase only fires when `refs.lane=fte` (`chase.when_refs`) and the PASS is not a master CV (`company=master` or `source_id` containing `master`). Outbound uses `critic.pass.outbound`, not employment chase.

## Jim-held PASS → park same turn

If a Critic PASS cannot submit without Jim (Create Account, captcha Jim must solve, strategy hold), publish `fte.park` in the **same turn** as the PASS (`status=open`, `do_not_reprompt=true` when Jim should not be re-pinged). That clears the chase expect. Do not leave a Jim-held PASS as an open stall hoping Jim will notice.

