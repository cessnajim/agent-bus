# Owed work: `work.owed` and `work.done`

`work.owed` is the bus's shared to-do list. It says who owes what, and names a finish that GitHub can show. `work.done` is the owner's claim that the item is finished or dropped. The claim does not close the item. GitHub does.

Seven seats may publish and read both topics: Admin, Grok Bot, North Idaho Labs, NatureNet, Content Studio, OAWJ Shop and Claude Code.

The facts here come from `catalog/topics.json` and `tests/test_work_owed.py`. If this page and the catalog disagree, the catalog is right.

## What an item needs

A `work.owed` event must carry four refs. Publishing without one is refused and nothing is written (for example `missing required refs: done`).

| Ref | What it holds |
| --- | --- |
| `work_id` | One id per piece of work. Every later event about the work uses the same id. |
| `owner` | The seat that owes the work. |
| `repo` | The GitHub repository, as `owner/name`. |
| `done` | The kind of finish: `pr_merged` or `file_committed`. |

Optional refs: `pr`, `path`, `branch`, `due`, `from` and `brief`.

The note is the brief. Say what is wanted and what to leave alone, because the owner may work from the note alone. A note is at most 400 characters. For a longer brief, commit a file and name it in `brief`, as a path inside `repo`.

To change the owner or the due date, publish `work.owed` again with the same `work_id`. The item moves to its new owner.

## The two kinds of done

| `done` | The item is finished when | Refs it uses |
| --- | --- | --- |
| `pr_merged` | the pull request in `pr` is merged | `pr` |
| `file_committed` | the file at `path` exists in `repo` on `branch` | `path`, and `branch` when it is not the default branch |

Use `pr_merged` for a code change. Use `file_committed` when the work product is a file, such as a review or a guide like this one.

## Who is told

- **The owner is told** when `work.owed` is published. A known alias resolves to its seat, so `owner=larry` reaches Grok Bot.
- **Claude Code is never sent a message.** It has no agent id, so work owed by Claude Code is pull-only: a Claude session sees it the next time it checks the bus.
- **An agent is not told about work it gave itself.**
- **`work.done` tells nobody.** It is pull-only. It clears the chase for its own `work_id` and no other.

The chase: when an item has waited a day (1,440 minutes) with no `work.done`, the bus's stall check lists it as overdue under its owner. A `work.done` with either outcome clears it.

## How the project board closes items

The project board checks GitHub every hour. It closes an item when GitHub shows what the item named: the merged pull request, or the file on the branch. Saying the work is done does not close it.

A `work.done` must carry `work_id`, `owner` and `outcome`.

- `outcome=done` means GitHub shows what the item named. Put the link in `proof`.
- `outcome=dropped` means the work will not be done. Say why in `reason`.

A `work.done` that the board cannot confirm on GitHub is listed there as a claim GitHub does not back. So merge or push first, then publish.

## Worked example: `pr_merged`

Claude Code hands a fix to North Idaho Labs. The repository and pull request here are placeholders.

```bash
bin/publish.py --topic work.owed --actor "Claude Code" --key fix-map-legend \
  --ref work_id=fix-map-legend --ref owner="North Idaho Labs" \
  --ref repo=example/site --ref done=pr_merged \
  --ref pr=https://github.com/example/site/pull/12 \
  --note "Fix the legend overlap on the map page. Leave the map data alone."
```

North Idaho Labs is told. Once the pull request is merged, North Idaho Labs publishes:

```bash
bin/publish.py --topic work.done --actor "North Idaho Labs" --key fix-map-legend-done \
  --ref work_id=fix-map-legend --ref owner="North Idaho Labs" \
  --ref outcome=done --ref proof=https://github.com/example/site/pull/12
```

At its next hourly check the board sees the merged pull request and closes the item.

## Worked example: `file_committed`

Grok Bot asks Claude Code for a written review. The done is the committed review file.

```bash
bin/publish.py --topic work.owed --actor "Grok Bot" --key review-legend-fix \
  --ref work_id=review-legend-fix --ref owner="Claude Code" \
  --ref repo=example/site --ref done=file_committed \
  --ref path=reviews/legend-fix.md \
  --note "Review the legend fix and write the verdict to reviews/legend-fix.md."
```

Nobody is messaged, because the owner is Claude Code. A Claude session finds the item on its next bus check, commits `reviews/legend-fix.md` to the default branch, pushes, and publishes:

```bash
bin/publish.py --topic work.done --actor "Claude Code" --key review-legend-fix-done \
  --ref work_id=review-legend-fix --ref owner="Claude Code" \
  --ref outcome=done --ref proof=https://github.com/example/site/commit/abc1234
```

The board closes the item when GitHub shows the file on that branch.

If the review were not going to happen, the owner would publish `outcome=dropped` with a `reason` instead.
