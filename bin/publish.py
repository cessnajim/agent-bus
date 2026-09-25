#!/usr/bin/env python3
"""Publish one event to the agent topic bus (Fedora). Idempotent on (topic, key) per day."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog" / "topics.json"
EVENTS = ROOT / "events"
STATE = ROOT / "state"
LOCK = ROOT / ".publish.lock"
NOTE_MAX = 400


def parse_kv(items):
    out = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"expected key=value, got {item!r}")
        k, v = item.split("=", 1)
        if v.isdigit():
            out[k] = int(v)
        elif v.lower() in ("true", "false"):
            out[k] = v.lower() == "true"
        else:
            out[k] = v
    return out


def load_day_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for i, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise SystemExit(f"corrupt jsonl {path}:{i}: {e}") from e
    return rows


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"corrupt state file: {path}: {e}") from e


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--actor", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--ref", action="append", default=[])
    ap.add_argument("--delta", action="append", default=[])
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    if args.note and len(args.note) > NOTE_MAX:
        raise SystemExit(f"note is {len(args.note)} chars; max {NOTE_MAX}")

    catalog = json.loads(CATALOG.read_text())
    topics = catalog["topics"]
    if args.topic not in topics:
        raise SystemExit(f"unknown topic {args.topic!r}. known: {', '.join(sorted(topics))}")
    meta = topics[args.topic]
    refs = parse_kv(args.ref)
    missing = [r for r in meta.get("required_refs", []) if r not in refs]
    if missing:
        raise SystemExit(f"missing required refs: {', '.join(missing)}")

    event = {
        "topic": args.topic,
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "idempotency_key": args.key,
        "actor": args.actor,
        "refs": refs,
        "state_delta": parse_kv(args.delta),
    }
    if args.note:
        event["note"] = args.note

    EVENTS.mkdir(parents=True, exist_ok=True)
    day = datetime.now().astimezone().date().isoformat()
    path = EVENTS / f"{day}.jsonl"
    state_name = meta.get("state_file")
    state_path = (STATE / f"{state_name}.json") if state_name else None

    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            for prev in load_day_events(path):
                if prev.get("idempotency_key") == args.key and prev.get("topic") == args.topic:
                    print(json.dumps({"status": "duplicate", "path": str(path), "event": prev}, indent=2))
                    return 0

            # Fail closed on corrupt state BEFORE appending the event.
            cur = load_state(state_path) if state_path else None

            with path.open("a") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())

            if state_path is not None and cur is not None:
                # topic / last_key / updated_at / last_actor = last event on this file.
                # refs = last event only (jsonl is the audit; no bag merge across topics).
                # Hand fields outside this merge stay until updated in the same turn as publish.
                cur.update({
                    "topic": args.topic,
                    "updated_at": event["ts"],
                    "last_key": args.key,
                    "last_actor": args.actor,
                    "refs": refs,
                })
                cur.setdefault("state", {}).update(event["state_delta"])
                # Top-level mirrors: refs first, then state_delta so delta wins on conflict.
                for k in ("submitted", "target", "date", "holds", "submitted_on_count", "email", "band"):
                    if k in refs:
                        cur[k] = refs[k]
                    if k in event["state_delta"]:
                        cur[k] = event["state_delta"][k]
                atomic_write_json(state_path, cur)
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)

    print(json.dumps({
        "status": "published",
        "path": str(path),
        "subscribers": meta.get("subscribers", []),
        "done_means": meta.get("done_means"),
        "state_path": str(state_path) if state_path else None,
        "event": event,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
