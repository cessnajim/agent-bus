#!/usr/bin/env python3
"""Inspect agent-bus: topics | state | today | tail | audit | rebuild."""
from __future__ import annotations

import argparse
import fcntl
import json
import sys
from datetime import datetime
from pathlib import Path

import publish

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog" / "topics.json"
EVENTS = ROOT / "events"
STATE = ROOT / "state"
LOCK = publish.LOCK


def is_blank(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def flat_diff(old, new, prefix: str = "") -> list[str]:
    if isinstance(old, dict) and isinstance(new, dict):
        changes = []
        for key in list(dict.fromkeys([*old.keys(), *new.keys()])):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in old:
                changes.append(f"+ {path} = {new[key]!r}")
            elif key not in new:
                changes.append(f"- {path} = {old[key]!r}")
            else:
                changes.extend(flat_diff(old[key], new[key], path))
        return changes
    if old != new:
        label = prefix or "value"
        return [f"{label}: {old!r} -> {new!r}"]
    return []


def event_problems(ev: dict, topics: dict) -> list[str]:
    problems = []
    topic = ev.get("topic")
    meta = topics.get(topic) if isinstance(topic, str) and topic else None
    if meta is None:
        problems.append(f"topic missing: {topic!r}")
    else:
        refs = ev.get("refs") if isinstance(ev.get("refs"), dict) else {}
        for req in meta.get("required_refs") or []:
            if req not in refs or is_blank(refs.get(req)):
                problems.append(f"required ref {req} missing or blank")
    note = ev.get("note")
    if isinstance(note, str) and len(note) > publish.NOTE_MAX:
        problems.append(f"note is {len(note)} chars; max {publish.NOTE_MAX}")
    return problems


def notify_covers(day: str, name: str, topic: str, key: str) -> bool:
    inbox = publish.notify_inbox(name, day)
    if not inbox.exists():
        return False
    for i, line in enumerate(inbox.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise SystemExit(f"corrupt jsonl {inbox}:{i}: {e}") from e
        if row.get("topic") == topic and row.get("idempotency_key") == key:
            return True
    return False


def events_by_state(catalog: dict, rows: list[tuple[Path, int, dict]]) -> dict[str, list[dict]]:
    topics = catalog["topics"]
    groups: dict[str, list[dict]] = {}
    for meta in topics.values():
        sf = meta.get("state_file")
        if sf:
            groups.setdefault(sf, [])
    for _path, _lineno, ev in rows:
        topic = ev.get("topic")
        meta = topics.get(topic) if isinstance(topic, str) else None
        if not meta:
            continue
        sf = meta.get("state_file")
        if sf:
            groups.setdefault(sf, []).append(ev)
    return groups


def folded_states(catalog: dict, rows: list[tuple[Path, int, dict]]):
    """name -> (path, existing or None, folded). Skips names with neither file nor events."""
    out = []
    for name, evs in events_by_state(catalog, rows).items():
        path = STATE / f"{name}.json"
        if not evs and not path.exists():
            continue
        existing = publish.load_state(path) if path.exists() else None
        folded = publish.fold_events(existing, evs)
        out.append((name, path, existing, folded))
    return out


def audit_view(doc: dict | None, promoted: set[str]) -> dict:
    doc = doc or {}
    view = {key: doc[key] if key in doc else None for key in ("topic", "last_key", "refs", "state")}
    for key in publish.PROMOTE_KEYS:
        if key in promoted:
            view[key] = doc[key] if key in doc else None
    return view


def audit() -> int:
    catalog = json.loads(CATALOG.read_text())
    topics = catalog["topics"]
    rows = publish.read_event_lines(EVENTS)
    findings = []

    seen: dict[tuple[str, str], list[dict]] = {}
    for path, lineno, ev in rows:
        topic = ev.get("topic")
        key = ev.get("idempotency_key")
        if isinstance(topic, str) and isinstance(key, str):
            seen.setdefault((topic, key), []).append({"day": path.stem, "path": str(path), "line": lineno})
    for (topic, key), hits in seen.items():
        days = sorted({hit["day"] for hit in hits})
        if len(days) > 1:
            findings.append({
                "kind": "key_reused",
                "topic": topic,
                "idempotency_key": key,
                "days": days,
            })

    for path, lineno, ev in rows:
        problems = event_problems(ev, topics)
        if problems:
            findings.append({
                "kind": "event",
                "path": str(path),
                "line": lineno,
                "topic": ev.get("topic"),
                "idempotency_key": ev.get("idempotency_key"),
                "problems": problems,
            })
        topic = ev.get("topic")
        meta = topics.get(topic) if isinstance(topic, str) else None
        key = ev.get("idempotency_key")
        if not meta or not isinstance(key, str):
            continue
        for sub in meta.get("subscribers") or []:
            name = publish.subscriber_name(sub)
            if notify_covers(path.stem, name, topic, key):
                continue
            findings.append({
                "kind": "notify",
                "path": str(path),
                "line": lineno,
                "topic": topic,
                "idempotency_key": key,
                "subscriber": name,
                "inbox": str(publish.notify_inbox(name, path.stem)),
            })

    for name, path, existing, folded in folded_states(catalog, rows):
        promoted = {k for k in publish.PROMOTE_KEYS if k in (existing or {}) or k in folded}
        old = audit_view(existing, promoted)
        new = audit_view(folded, promoted)
        if old != new:
            findings.append({
                "kind": "state",
                "state": name,
                "path": str(path),
                "changes": flat_diff(old, new),
            })

    ok = not findings
    print(json.dumps({"ok": ok, "findings": findings}, indent=2, ensure_ascii=False))
    return 0 if ok else 1


def rebuild(write: bool) -> int:
    def plan():
        catalog = json.loads(CATALOG.read_text())
        rows = publish.read_event_lines(EVENTS)
        planned = []
        for name, path, existing, folded in folded_states(catalog, rows):
            planned.append((name, path, existing, folded, existing != folded))
        return planned

    if write:
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        with LOCK.open("a") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            try:
                planned = plan()
                for _name, path, _existing, folded, changed in planned:
                    if changed:
                        publish.atomic_write_json(path, folded)
            finally:
                fcntl.flock(lockf, fcntl.LOCK_UN)
    else:
        planned = plan()

    results = []
    for name, path, existing, folded, changed in planned:
        if not changed:
            action = "unchanged"
            changes: list[str] = []
        else:
            action = "written" if write else "dry-run"
            changes = flat_diff(existing or {}, folded)
        results.append({
            "state": name,
            "path": str(path),
            "action": action,
            "changes": changes,
        })
    print(json.dumps({"write": write, "results": results}, indent=2, ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("topics")
    p_state = sp.add_parser("state"); p_state.add_argument("name")
    p_today = sp.add_parser("today"); p_today.add_argument("--topic")
    p_tail = sp.add_parser("tail"); p_tail.add_argument("--topic"); p_tail.add_argument("--n", type=int, default=20)
    sp.add_parser("audit")
    p_rebuild = sp.add_parser("rebuild")
    p_rebuild.add_argument("--write", action="store_true", help="replace state files from a fold of the log")
    args = ap.parse_args()
    catalog = json.loads(CATALOG.read_text())

    if args.cmd == "topics":
        print(json.dumps({"rules": catalog.get("rules", {}), "topics": {
            k: {"publishers": v.get("publishers"), "subscribers": v.get("subscribers"),
                "required_refs": v.get("required_refs"), "done_means": v.get("done_means"),
                "state_file": v.get("state_file")}
            for k, v in catalog["topics"].items()
        }}, indent=2))
        return 0

    if args.cmd == "state":
        path = STATE / f"{args.name}.json"
        if not path.exists():
            print(json.dumps({"status": "empty", "path": str(path)}))
            return 0
        print(path.read_text())
        return 0

    if args.cmd == "audit":
        return audit()
    if args.cmd == "rebuild":
        return rebuild(args.write)

    day = datetime.now().astimezone().date().isoformat()
    path = EVENTS / f"{day}.jsonl"
    rows = []
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            if getattr(args, "topic", None) and ev.get("topic") != args.topic:
                continue
            rows.append(ev)
    if args.cmd == "today":
        print(json.dumps(rows, indent=2, ensure_ascii=False)); return 0
    print(json.dumps(rows[-args.n:], indent=2, ensure_ascii=False)); return 0

if __name__ == "__main__":
    sys.exit(main())
