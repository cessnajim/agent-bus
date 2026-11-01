#!/usr/bin/env python3
"""Bus liveness / coverage logic (2026-10-23). Shared by busctl.py and bus_watchdog.py.

Answers two questions from the event log alone:
  1. Which catalog publisher has gone silent longer than its threshold?
  2. Which Writing Critic PASS (outbound lane) has no downstream publish/send
     event after it, past its SLA? (a side effect that probably happened but
     was never put on the bus)

Thresholds live in catalog/topics.json -> "coverage". Actor spellings are
normalized (fte-apply == FTE Apply, investment == Investment).
bus.test events and refs.test=true are ignored.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import chase

DEFAULT_SILENT_DAYS = 3


def _key(name) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name).casefold())


def actor_index(catalog: dict) -> dict:
    """normalized-key -> canonical actor name."""
    cov = catalog.get("coverage", {})
    idx: dict[str, str] = {}
    names = set(cov.get("agents", {})) | set(cov.get("system_actors", [])) | set(catalog.get("agents", {}))
    for meta in catalog.get("topics", {}).values():
        names.update(meta.get("publishers", []))
    for n in names:
        idx[_key(n)] = n
    for alias, canon in cov.get("actor_aliases", {}).items():
        idx[_key(alias)] = canon
    return idx


def canonical_actor(name, catalog: dict) -> str:
    if name is None:
        return ""
    s = str(name).strip()
    return actor_index(catalog).get(_key(s), s)


def load_all_events(events_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for f in sorted(events_dir.glob("*.jsonl")):
        rows.extend(chase.read_jsonl(f))
    rows = [e for e in rows if chase.parse_ts(e.get("ts"))]
    rows.sort(key=lambda e: chase.ts_sort_key(e.get("ts")))
    return rows


def real_events(events: list[dict]) -> list[dict]:
    out = []
    for e in events:
        if e.get("topic") == "bus.test":
            continue
        if isinstance(e.get("refs"), dict) and e["refs"].get("test") is True:
            continue
        out.append(e)
    return out


def _age_days(last: datetime, now: datetime, weekdays_only: bool) -> int:
    d0, d1 = last.date(), now.date()
    if d1 <= d0:
        return 0
    if not weekdays_only:
        return (d1 - d0).days
    n, d = 0, d0
    while d < d1:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def agent_rows(catalog: dict, events: list[dict], now: datetime, days: int) -> list[dict]:
    cov = catalog.get("coverage", {})
    agents_cfg = cov.get("agents", {})
    system = set(cov.get("system_actors", []))
    evs = real_events(events)
    by_actor: dict[str, list[dict]] = {}
    for e in evs:
        by_actor.setdefault(canonical_actor(e.get("actor"), catalog), []).append(e)
    published_topics: dict[str, set] = {}
    for topic, meta in catalog.get("topics", {}).items():
        if topic == "bus.test":
            continue
        for p in meta.get("publishers", []):
            published_topics.setdefault(p, set()).add(topic)
    names = list(agents_cfg)
    for a in by_actor:
        if a not in names and a not in system:
            names.append(a)
    since = now - timedelta(days=days)
    today = now.date().isoformat()
    rows = []
    for name in names:
        cfg = agents_cfg.get(name, {})
        mine = by_actor.get(name, [])
        last = chase.parse_ts(mine[-1]["ts"]) if mine else None
        in_window = [e for e in mine if chase.ts_sort_key(e["ts"]) >= since.timestamp()]
        thr = cfg.get("silent_days", DEFAULT_SILENT_DAYS)
        wd = bool(cfg.get("weekdays_only"))
        if last is not None:
            age = _age_days(last, now, wd)
        elif cfg.get("since"):
            age = _age_days(datetime.fromisoformat(cfg["since"]).replace(tzinfo=chase.BUS_TZ), now, wd)
        else:
            age = None
        snooze = cfg.get("snooze_until")
        if cfg.get("exempt"):
            status = "EXEMPT"
        elif cfg.get("on_request"):
            status = "ON_REQUEST"  # v8: works only when asked; never flagged silent
        elif snooze and today <= snooze:
            status = "SNOOZED"
        elif age is None:
            status = "NEVER"
        elif age > thr:
            status = "SILENT"
        else:
            status = "OK"
        rows.append({
            "agent": name,
            "topics": sorted(published_topics.get(name, [])),
            "last_event": last.isoformat(timespec="seconds") if last else None,
            "last_topic": mine[-1].get("topic") if mine else None,
            "age_days": age,
            "threshold_days": thr,
            "weekdays_only": wd,
            "events_in_window": len(in_window),
            "events_total": len(mine),
            "status": status,
            "quiet_ok": cfg.get("quiet_ok"),
        })
    return rows


def missing_downstream(catalog: dict, events: list[dict], now: datetime, days: int) -> list[dict]:
    """Critic PASSes with no later downstream event past SLA (version-collapsing:
    one downstream event satisfies every earlier PASS in that rule)."""
    out = []
    evs = real_events(events)
    since = now - timedelta(days=days)
    for rule in catalog.get("coverage", {}).get("expected_downstream", []):
        lanes = {_key(l) for l in rule.get("lanes", [])}
        to_topics = set(rule.get("to_topics", []))
        last_down = max((chase.ts_sort_key(e["ts"]) for e in evs if e.get("topic") in to_topics), default=float("-inf"))
        sla = timedelta(hours=rule.get("sla_hours", 72))
        pending = []
        for e in evs:
            if e.get("topic") != rule.get("from_topic", "critic.pass.outbound"):
                continue
            if _key((e.get("refs") or {}).get("lane", "")) not in lanes:
                continue
            ts = chase.parse_ts(e["ts"])
            k = chase.ts_sort_key(e["ts"])
            if ts < since or k <= last_down or now - ts <= sla:
                continue
            pending.append(e)
        if pending:
            first = pending[0]
            out.append({
                "rule": rule["name"],
                "owner": rule.get("owner"),
                "expected_topics": sorted(to_topics),
                "sla_hours": rule.get("sla_hours", 72),
                "pass_count": len(pending),
                "oldest_pass_ts": first["ts"],
                "oldest_pass_key": first.get("idempotency_key"),
                "latest_pass_key": pending[-1].get("idempotency_key"),
            })
    return out


def hygiene(catalog: dict, events: list[dict]) -> list[dict]:
    """Actor spellings that differ from the canonical name (publish.py now fixes new ones)."""
    seen: dict[str, int] = {}
    for e in real_events(events):
        raw = str(e.get("actor", "")).strip()
        if raw and canonical_actor(raw, catalog) != raw:
            seen[raw] = seen.get(raw, 0) + 1
    return [{"raw": r, "canonical": canonical_actor(r, catalog), "events": n} for r, n in sorted(seen.items())]


def build_report(root: Path | None = None, now: datetime | None = None, days: int = 3, lookback: int = 7) -> dict:
    root = root or chase.bus_root()
    now = now or chase.bus_now()
    catalog = json.loads((root / "catalog" / "topics.json").read_text())
    events = load_all_events(root / "events")
    rows = agent_rows(catalog, events, now, days)
    missing = missing_downstream(catalog, events, now, max(lookback, days))
    flags = [r for r in rows if r["status"] in ("SILENT", "NEVER")]
    return {
        "now": now.isoformat(timespec="seconds"),
        "days": days,
        "lookback_days": max(lookback, days),
        "catalog_version": catalog.get("version"),
        "agents": rows,
        "silent": flags,
        "missing_downstream": missing,
        "hygiene": hygiene(catalog, events),
    }


def _fmt_last(r):
    if not r["last_event"]:
        return "never"
    return chase.parse_ts(r["last_event"]).strftime("%a %m/%d %H:%M PT")


def render_table(rep: dict) -> str:
    lines = [f"agent-bus coverage @ {rep['now']} (catalog v{rep['catalog_version']}, window {rep['days']}d, PASS lookback {rep['lookback_days']}d)", ""]
    hdr = f"{'agent':<21} {'last real event':<19} {'age':>4} {'thr':>4} {'win':>4} {'all':>5}  status"
    lines += [hdr, "-" * len(hdr)]
    for r in rep["agents"]:
        age = "-" if r["age_days"] is None else str(r["age_days"])
        flag = {"SILENT": f"SILENT {r['age_days']}d", "NEVER": "NEVER published"}.get(r["status"], r["status"])
        lines.append(f"{r['agent']:<21} {_fmt_last(r):<19} {age:>4} {r['threshold_days']:>4} {r['events_in_window']:>4} {r['events_total']:>5}  {flag}")
    lines += ["", "age = days since last real event (business days for FTE Apply); thr = silent_days; "
              "bus.test and test=true events are ignored."]
    if rep["missing_downstream"]:
        lines += ["", "Critic PASS with no downstream event past SLA:"]
        for m in rep["missing_downstream"]:
            lines.append(f"  - {m['rule']}: {m['pass_count']} PASS since last {'/'.join(m['expected_topics'])}; oldest {m['oldest_pass_key']} ({m['oldest_pass_ts']}); owner {m['owner']}")
    else:
        lines += ["", "Critic PASS -> downstream: none outstanding."]
    if rep["hygiene"]:
        lines += ["", "Actor spellings (normalized above):"]
        for h in rep["hygiene"]:
            lines.append(f"  - {h['raw']!r} -> {h['canonical']!r} ({h['events']} events)")
    return "\n".join(lines)
