#!/usr/bin/env python3
"""Follow-through helpers: stall detection and absolute WS submitted counts.

Imported by publish.py and busctl.py. The bus stores the board; this module
does not nudge anyone. Primary runs docs/skills/agent-bus-chase.SKILL.md.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

MATCH_REF_KEYS = (
    "company",
    "role",
    "packet_path",
    "source_id",
    "asset",
    "item_id",
    "lane",
)


def bus_root() -> Path:
    env = os.environ.get("AGENT_BUS_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def parse_ts(ts) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.astimezone()
    return dt


def int_or_none(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        try:
            return int(value)
        except ValueError:
            return None
    return None


def norm(value) -> str:
    return str(value).strip().casefold()


def values_match(left, right) -> bool:
    if left is None or right is None:
        return False
    return norm(left) == norm(right)


def refs_satisfy(refs: dict, expected: dict) -> bool:
    if not expected:
        return False
    for key, value in expected.items():
        if not values_match(refs.get(key), value):
            return False
    return True


def refs_match_keys(left: dict, right: dict, keys: list) -> bool:
    if not keys:
        return True
    for key in keys:
        if key not in left or key not in right:
            return False
        if not values_match(left.get(key), right.get(key)):
            return False
    return True


def norm_path(value) -> str:
    return norm(str(value).rstrip("/"))


def chase_refs_match(left: dict, right: dict, keys: list) -> bool:
    """Match a chase trigger to a later proof event.

    Prefer durable ids over role strings. Critic may PASS
    "Director/Sr Director, AI Engineering" while submit records
    "Director of AI Engineering" for the same source_id / packet.
    Skill: agent-bus-chase — clear on matching source_id (or packet_path).
    """
    left = left or {}
    right = right or {}
    if left.get("company") not in (None, "") and right.get("company") not in (None, ""):
        if not values_match(left.get("company"), right.get("company")):
            return False

    ls, rs = left.get("source_id"), right.get("source_id")
    if ls not in (None, "") and rs not in (None, "") and values_match(ls, rs):
        return True

    lp, rp = left.get("packet_path"), right.get("packet_path")
    if lp not in (None, "") and rp not in (None, "") and norm_path(lp) == norm_path(rp):
        return True

    return refs_match_keys(left, right, keys)


def event_is_after(event: dict, trigger: dict) -> bool:
    """True when event is later than trigger.

    Timestamps are second-resolution, so a submit published in the same second
    as a Critic PASS still counts when it appears later in the log.
    """
    event_ts = parse_ts(event.get("ts"))
    trigger_ts = parse_ts(trigger.get("ts"))
    if event_ts and trigger_ts and event_ts != trigger_ts:
        return event_ts > trigger_ts
    if (not event_ts or not trigger_ts) and str(event.get("ts") or "") != str(trigger.get("ts") or ""):
        return str(event.get("ts") or "") > str(trigger.get("ts") or "")
    if "_seq" in event and "_seq" in trigger:
        return event["_seq"] > trigger["_seq"]
    return False


def order_events(events: list[dict]) -> list[dict]:
    decorated = sorted(enumerate(events), key=lambda pair: (pair[1].get("ts") or "", pair[0]))
    ordered = []
    for seq, (_, ev) in enumerate(decorated):
        copy = dict(ev)
        copy["_seq"] = seq
        ordered.append(copy)
    return ordered


def submitted_floor(claimed, ledger_count: int) -> tuple[int, bool]:
    """Absolute day count. Never store less than today's fte.submit events.

    Publishers pass the absolute total (including off-bus submits). A repeated
    `--delta submitted=1` must not stick the snapshot at 1 after the second
    receipt. Returns (stored, adjusted).
    """
    claimed_n = int_or_none(claimed)
    filled = claimed_n is None
    if claimed_n is None:
        claimed_n = 0
    stored = max(claimed_n, int(ledger_count))
    return stored, filled or stored != claimed_n


def split_expect(expect, owed_topic: str | None) -> list[str]:
    if isinstance(expect, list):
        parts = [str(p).strip() for p in expect if str(p).strip()]
    elif isinstance(expect, str):
        parts = [p.strip() for p in expect.split(",") if p.strip()]
    else:
        parts = []
    if not parts and owed_topic:
        parts = [str(owed_topic)]
    return parts


def chase_match_from_refs(refs: dict) -> dict:
    match = {}
    for key in MATCH_REF_KEYS:
        if key in refs and refs[key] not in (None, ""):
            match[key] = refs[key]
    return match


def proof_clears_owed(row: dict, event: dict) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("status") not in (None, "open"):
        return False
    expect = split_expect(row.get("expect"), row.get("owed_topic"))
    if event.get("topic") not in expect:
        return False
    since = parse_ts(row.get("since"))
    event_ts = parse_ts(event.get("ts"))
    if since and event_ts and event_ts < since:
        return False
    if since and not event_ts and str(event.get("ts") or "") < str(row.get("since") or ""):
        return False
    match = row.get("match") or {}
    refs = event.get("refs") or {}
    return chase_refs_match(match, refs, list(match.keys()))


def owed_ids_cleared_by(owed_map: dict, event: dict) -> list[str]:
    cleared = []
    for stall_id, row in (owed_map or {}).items():
        if proof_clears_owed(row, event):
            cleared.append(str(stall_id))
    return cleared


def apply_owed_row(cur: dict, refs: dict, event: dict, key: str, actor: str) -> None:
    stall_id = str(refs["stall_id"])
    owed_map = cur.setdefault("owed", {})
    if refs.get("status") == "cleared":
        owed_map.pop(stall_id, None)
    else:
        owed_map[stall_id] = {
            "stall_id": stall_id,
            "owed_topic": refs.get("owed_topic"),
            "expect": split_expect(refs.get("expect"), refs.get("owed_topic")),
            "owner": refs.get("owner"),
            "status": refs.get("status") or "open",
            "since": refs.get("since") or event.get("ts"),
            "match": chase_match_from_refs(refs),
            "updated_at": event.get("ts"),
            "key": key,
            "actor": actor,
        }
    cur["open_count"] = sum(1 for row in owed_map.values() if row.get("status") == "open")


def apply_parks_row(cur: dict, refs: dict, event: dict, key: str, actor: str) -> None:
    """Merge an open/cleared park. Id is source_id (live catalog) or item_id (example)."""
    raw_id = refs.get("source_id")
    if raw_id in (None, ""):
        raw_id = refs.get("item_id")
    if raw_id is None or refs.get("status") in (None, ""):
        return
    sid = str(raw_id)
    parks_map = cur.setdefault("parks", {})
    if refs.get("status") == "cleared":
        parks_map.pop(sid, None)
    else:
        parks_map[sid] = {
            "company": refs.get("company"),
            "role": refs.get("role"),
            "source_id": refs.get("source_id"),
            "item_id": refs.get("item_id"),
            "reason": refs.get("reason"),
            "status": refs.get("status"),
            "do_not_reprompt": refs.get("do_not_reprompt"),
            "until": refs.get("until"),
            "packet_path": refs.get("packet_path"),
            "updated_at": event.get("ts"),
            "key": key,
            "actor": actor,
        }
    cur["open_count"] = sum(1 for row in parks_map.values() if row.get("status") == "open")


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def load_recent_events(events_dir: Path, now: datetime, lookback_days: int) -> list[dict]:
    rows: list[dict] = []
    days = max(int(lookback_days), 1)
    for offset in range(days):
        day = (now.date() - timedelta(days=offset)).isoformat()
        path = events_dir / f"{day}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict):
                rows.append(ev)
    rows.sort(key=lambda ev: ev.get("ts") or "")
    return rows


def _event_day(ev: dict) -> str:
    ts = ev.get("ts") or ""
    return ts[:10] if len(ts) >= 10 else ""


def _age_minutes(now: datetime, ts) -> int | None:
    parsed = parse_ts(ts)
    if not parsed:
        return None
    return int((now - parsed).total_seconds() // 60)


def debounce_minutes(chase: dict, follow: dict) -> int:
    raw = chase.get("debounce_minutes", follow.get("debounce_minutes", 30))
    parsed = int_or_none(raw)
    return parsed if parsed is not None and parsed >= 0 else 30


def _state_is_today(state: dict, today: str) -> bool:
    if not state:
        return False
    nested = state.get("state") if isinstance(state.get("state"), dict) else {}
    date = state.get("date")
    if date in (None, ""):
        date = nested.get("date")
    if date not in (None, ""):
        return str(date)[:10] == today
    return str(state.get("updated_at") or "")[:10] == today


def _collect_numbers(events: list[dict], today: str, field: str) -> list[int]:
    found = []
    for ev in events:
        if _event_day(ev) != today:
            continue
        if ev.get("topic") not in ("fte.submit", "ws.short_of_target"):
            continue
        for bag in (ev.get("refs") or {}, ev.get("state_delta") or {}):
            number = int_or_none(bag.get(field))
            if number is not None:
                found.append(number)
    return found


def _latest_target(events: list[dict], today: str, ws: dict) -> int | None:
    target = None
    if _state_is_today(ws, today):
        nested = ws.get("state") if isinstance(ws.get("state"), dict) else {}
        target = int_or_none(ws.get("target"))
        if target is None:
            target = int_or_none(nested.get("target"))
    for ev in events:
        if _event_day(ev) != today:
            continue
        if ev.get("topic") not in ("fte.submit", "ws.short_of_target"):
            continue
        for bag in (ev.get("refs") or {}, ev.get("state_delta") or {}):
            number = int_or_none(bag.get("target"))
            if number is not None:
                target = number
    return target


def _effective_submitted(events: list[dict], today: str, ws: dict) -> int:
    numbers = []
    if _state_is_today(ws, today):
        nested = ws.get("state") if isinstance(ws.get("state"), dict) else {}
        for bag in (ws, nested):
            number = int_or_none(bag.get("submitted"))
            if number is not None:
                numbers.append(number)
    numbers.extend(_collect_numbers(events, today, "submitted"))
    ledger = sum(1 for ev in events if ev.get("topic") == "fte.submit" and _event_day(ev) == today)
    numbers.append(ledger)
    return max(numbers) if numbers else 0


def _last_nudge_ts(stall_id: str, owed: dict, events: list[dict]):
    ts = None
    row = (owed.get("owed") or {}).get(stall_id)
    if isinstance(row, dict) and row.get("status") == "open":
        ts = row.get("updated_at")
    for ev in events:
        if ev.get("topic") != "chase.owed":
            continue
        refs = ev.get("refs") or {}
        if str(refs.get("stall_id")) != stall_id:
            continue
        if refs.get("status") == "cleared":
            continue
        ev_ts = ev.get("ts")
        if ev_ts and (not ts or str(ev_ts) > str(ts)):
            ts = ev_ts
    return ts


def _stall_row(
    *,
    stall_id: str,
    kind: str,
    source_topic: str,
    owner: str,
    expect: list[str],
    since,
    chase: dict,
    follow: dict,
    now: datetime,
    owed: dict,
    events: list[dict],
    match: dict | None = None,
    detail: dict | None = None,
) -> dict:
    debounce = debounce_minutes(chase, follow)
    age = _age_minutes(now, since)
    overdue = True if age is None else age >= debounce
    last_nudge = _last_nudge_ts(stall_id, owed, events)
    nudge_age = _age_minutes(now, last_nudge) if last_nudge else None
    nudged_recently = nudge_age is not None and nudge_age < debounce
    return {
        "stall_id": stall_id,
        "kind": kind,
        "source_topic": source_topic,
        "owner": owner or follow.get("primary") or "Admin",
        "owed_topic": expect[0] if expect else None,
        "expect": expect,
        "match": match or {},
        "since": since,
        "age_minutes": age,
        "debounce_minutes": debounce,
        "overdue": overdue,
        "nudge_due": bool(overdue and not nudged_recently),
        "last_nudge": last_nudge,
        "detail": detail or {},
    }


def _short_stall(topic: str, meta: dict, chase: dict, events: list[dict], states: dict, follow: dict, now: datetime, owed: dict) -> dict | None:
    today = now.date().isoformat()
    state_name = meta.get("state_file") or "ws"
    ws = states.get(state_name) or {}
    target = _latest_target(events, today, ws if state_name == "ws" else {})
    if state_name != "ws":
        blob = states.get(state_name) or {}
        if _state_is_today(blob, today):
            nested = blob.get("state") if isinstance(blob.get("state"), dict) else {}
            if target is None:
                target = int_or_none(blob.get("target"))
            if target is None:
                target = int_or_none(nested.get("target"))
    if target is None:
        return None
    submitted = _effective_submitted(events, today, ws if state_name == "ws" else {})
    if submitted >= target:
        return None
    clear_refs = chase.get("clear_refs") or {}
    progress_topics = {topic, "fte.submit"}
    progress = [ev for ev in events if ev.get("topic") in progress_topics and _event_day(ev) == today]
    if progress:
        latest = progress[-1]
        if latest.get("topic") == topic and refs_satisfy(latest.get("refs") or {}, clear_refs):
            return None
        since = latest.get("ts")
    else:
        since = ws.get("updated_at") if _state_is_today(ws, today) else None
    if not since:
        return None
    expect = split_expect(chase.get("expect"), "fte.submit")
    return _stall_row(
        stall_id=f"{topic}:{today}",
        kind="submitted_below_target",
        source_topic=topic,
        owner=chase.get("owner") or "",
        expect=expect,
        since=since,
        chase=chase,
        follow=follow,
        now=now,
        owed=owed,
        events=events,
        detail={"submitted": submitted, "target": target, "gap": target - submitted, "date": today},
    )


def _trigger_cleared(trigger: dict, events: list[dict], chase: dict) -> bool:
    expect = set(split_expect(chase.get("expect"), None))
    match_keys = list(chase.get("match") or [])
    clear_refs = chase.get("clear_refs") or {}
    trigger_refs = trigger.get("refs") or {}
    for ev in events:
        if not event_is_after(ev, trigger):
            continue
        if ev.get("topic") in expect and chase_refs_match(trigger_refs, ev.get("refs") or {}, match_keys):
            return True
        if (
            clear_refs
            and ev.get("topic") == trigger.get("topic")
            and chase_refs_match(trigger_refs, ev.get("refs") or {}, match_keys)
            and refs_satisfy(ev.get("refs") or {}, clear_refs)
        ):
            return True
    return False


def _expect_stalls(topic: str, chase: dict, events: list[dict], follow: dict, now: datetime, owed: dict) -> list[dict]:
    match_keys = list(chase.get("match") or [])
    expect = split_expect(chase.get("expect"), None)
    if not expect:
        return []
    open_by_id: dict[str, dict] = {}
    for ev in events:
        if ev.get("topic") != topic:
            continue
        refs = ev.get("refs") or {}
        when_refs = chase.get("when_refs") or {}
        if when_refs and not refs_satisfy(refs, when_refs):
            continue
        if _trigger_cleared(ev, events, chase):
            continue
        ident = ":".join([topic] + [norm(refs.get(key)) for key in match_keys]) if match_keys else f"{topic}:{ev.get('idempotency_key') or ev.get('ts')}"
        prev = open_by_id.get(ident)
        if prev is None or str(ev.get("ts") or "") >= str(prev.get("ts") or ""):
            open_by_id[ident] = ev
    rows = []
    for ident, ev in open_by_id.items():
        refs = ev.get("refs") or {}
        match = {key: refs.get(key) for key in match_keys if key in refs}
        detail = {key: refs.get(key) for key in ("lane", "packet_path", "company", "role", "asset", "item_id") if refs.get(key) not in (None, "")}
        rows.append(
            _stall_row(
                stall_id=ident,
                kind="expect",
                source_topic=topic,
                owner=chase.get("owner") or "",
                expect=expect,
                since=ev.get("ts"),
                chase=chase,
                follow=follow,
                now=now,
                owed=owed,
                events=events,
                match=match,
                detail=detail,
            )
        )
    return rows


def _manual_owed_stalls(owed: dict, events: list[dict], stalls: list[dict], follow: dict, now: datetime) -> list[dict]:
    seen = {row["stall_id"] for row in stalls}
    extra = []
    for stall_id, row in (owed.get("owed") or {}).items():
        if not isinstance(row, dict) or row.get("status") != "open":
            continue
        if stall_id in seen:
            continue
        expect = split_expect(row.get("expect"), row.get("owed_topic"))
        extra.append(
            _stall_row(
                stall_id=str(stall_id),
                kind="owed",
                source_topic="chase.owed",
                owner=row.get("owner") or "",
                expect=expect,
                since=row.get("since") or row.get("updated_at"),
                chase={"debounce_minutes": follow.get("debounce_minutes", 30)},
                follow=follow,
                now=now,
                owed=owed,
                events=events,
                match=row.get("match") or {},
                detail={"owed_topic": row.get("owed_topic")},
            )
        )
    return extra


def _open_parks(parks: dict) -> dict:
    parks_map = parks.get("parks") if isinstance(parks.get("parks"), dict) else {}
    items = []
    for row in parks_map.values():
        if not isinstance(row, dict) or row.get("status") != "open":
            continue
        items.append(
            {
                "company": row.get("company"),
                "role": row.get("role"),
                "source_id": row.get("source_id", row.get("item_id")),
                "reason": row.get("reason"),
                "do_not_reprompt": row.get("do_not_reprompt"),
                "since": row.get("updated_at"),
            }
        )
    items.sort(key=lambda row: str(row.get("since") or ""))
    if parks_map:
        count = len(items)
    else:
        count = int_or_none(parks.get("open_count")) or 0
    return {"count": count, "items": items[:50]}


def detect_stalls(catalog: dict, events: list[dict], states: dict, now: datetime) -> dict:
    follow = catalog.get("follow_through") or {}
    owed = states.get("owed") or {}
    ordered = order_events(events)
    stalls: list[dict] = []
    for topic, meta in (catalog.get("topics") or {}).items():
        chase = meta.get("chase") if isinstance(meta, dict) else None
        if not isinstance(chase, dict):
            continue
        if chase.get("while") == "submitted_below_target":
            row = _short_stall(topic, meta, chase, ordered, states, follow, now, owed)
            if row:
                stalls.append(row)
            continue
        stalls.extend(_expect_stalls(topic, chase, ordered, follow, now, owed))
    stalls.extend(_manual_owed_stalls(owed, ordered, stalls, follow, now))
    stalls.sort(key=lambda row: (not row["nudge_due"], -(row["age_minutes"] or 0), row["stall_id"]))
    debounce = int_or_none(follow.get("debounce_minutes"))
    return {
        "primary": follow.get("primary") or catalog.get("owner") or "Admin",
        "proof": follow.get("proof") or "next bus event",
        "date": now.date().isoformat(),
        "debounce_minutes": debounce if debounce is not None else 30,
        "lookback_days": int_or_none(follow.get("lookback_days")) or 2,
        "nudge_due_count": sum(1 for row in stalls if row["nudge_due"]),
        "stalls": stalls,
        "open_parks": _open_parks(states.get("parks") or {}),
    }


def build_stall_report(root: Path | None = None, now: datetime | None = None) -> dict:
    root = root or bus_root()
    now = now or datetime.now().astimezone()
    catalog_path = root / "catalog" / "topics.json"
    catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else {"topics": {}}
    follow = catalog.get("follow_through") or {}
    lookback = int_or_none(follow.get("lookback_days")) or 2
    events = load_recent_events(root / "events", now, lookback)
    state_dir = root / "state"
    states = {
        "ws": _read_json(state_dir / "ws.json"),
        "parks": _read_json(state_dir / "parks.json"),
        "owed": _read_json(state_dir / "owed.json"),
    }
    return detect_stalls(catalog, events, states, now)


def short_republish_warning(prior_events: list[dict], refs: dict, delta: dict, debounce_minutes: int, now: datetime) -> dict | None:
    """Warn when ws.short_of_target is repeated inside the debounce window with the same counts."""
    last = None
    for ev in prior_events:
        if ev.get("topic") == "ws.short_of_target":
            last = ev
    if not last:
        return None
    age = _age_minutes(now, last.get("ts"))
    if age is None or age >= debounce_minutes:
        return None
    last_refs = last.get("refs") or {}
    last_delta = last.get("state_delta") or {}

    def _pick(bag_a, bag_b, field):
        if field in bag_a:
            return bag_a.get(field)
        return bag_b.get(field)

    same_submitted = values_match(
        _pick(delta, refs, "submitted"),
        _pick(last_delta, last_refs, "submitted"),
    )
    same_target = values_match(
        _pick(delta, refs, "target"),
        _pick(last_delta, last_refs, "target"),
    )
    if not (same_submitted and same_target):
        return None
    return {
        "debounce": True,
        "debounce_minutes": debounce_minutes,
        "age_minutes": age,
        "message": "ws.short_of_target already published inside the debounce window with the same submitted/target. Prefer busctl stall; do not treat another short event as follow-through.",
    }
