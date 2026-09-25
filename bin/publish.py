#!/usr/bin/env python3
"""Publish one event to the agent topic bus (Fedora).

Idempotent on (topic, idempotency_key) across every events/*.jsonl.
A real append also writes notify/<slug>/YYYY-MM-DD.jsonl per subscriber.
Optional wake commands are best-effort and do not roll back the publish.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog" / "topics.json"
EVENTS = ROOT / "events"
STATE = ROOT / "state"
NOTIFY = ROOT / "notify"
LOCK = ROOT / ".publish.lock"
NOTE_MAX = 400
WAKE_TIMEOUT_S = 5
# Top-level mirrors. refs win first, then state_delta on the same key.
PROMOTE_KEYS = ("submitted", "target", "date", "holds", "submitted_on_count", "email", "band")
MANAGED_KEYS = frozenset({"topic", "updated_at", "last_key", "last_actor", "refs", "state", *PROMOTE_KEYS})


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


def apply_event(cur: dict, event: dict) -> dict:
    """Fold one event into a snapshot.

    refs are replaced by this event. state merges this delta.
    Promoted keys mirror refs, then the delta. Every other key is left alone.
    """
    refs = event.get("refs") if isinstance(event.get("refs"), dict) else {}
    delta = event.get("state_delta") if isinstance(event.get("state_delta"), dict) else {}
    refs = dict(refs)
    delta = dict(delta)
    cur.update({
        "topic": event.get("topic"),
        "updated_at": event.get("ts"),
        "last_key": event.get("idempotency_key"),
        "last_actor": event.get("actor"),
        "refs": refs,
    })
    state = cur.get("state")
    if not isinstance(state, dict):
        state = {}
        cur["state"] = state
    state.update(delta)
    for k in PROMOTE_KEYS:
        if k in refs:
            cur[k] = refs[k]
        if k in delta:
            cur[k] = delta[k]
    return cur


def fold_events(existing: dict | None, events: list[dict]) -> dict:
    """Replay events onto preserved hand fields. Publish and rebuild both use apply_event."""
    cur = {k: v for k, v in (existing or {}).items() if k not in MANAGED_KEYS}
    for ev in events:
        apply_event(cur, ev)
    return cur


def read_event_lines(events_dir: Path) -> list[tuple[Path, int, dict]]:
    """Events in date order, then filename, then line order."""
    rows: list[tuple[Path, int, dict]] = []
    if not events_dir.exists():
        return rows
    paths = sorted((p for p in events_dir.glob("*.jsonl") if p.is_file()), key=lambda p: (p.stem, p.name))
    for path in paths:
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append((path, i, json.loads(line)))
            except json.JSONDecodeError as e:
                raise SystemExit(f"corrupt jsonl {path}:{i}: {e}") from e
    return rows


def find_duplicate(topic: str, key: str) -> tuple[Path, dict] | None:
    for path, _lineno, prev in read_event_lines(EVENTS):
        if prev.get("topic") == topic and prev.get("idempotency_key") == key:
            return path, prev
    return None


def subscriber_slug(name: str) -> str:
    return name.lower().replace(" ", "-")


def subscriber_name(sub) -> str:
    if isinstance(sub, str):
        if not sub.strip():
            raise SystemExit("subscriber name is empty")
        return sub
    if isinstance(sub, dict):
        name = sub.get("name")
        if not isinstance(name, str) or not name.strip():
            raise SystemExit(f"subscriber object missing name: {sub!r}")
        return name
    raise SystemExit(f"bad subscriber: {sub!r}")


def notify_inbox(name: str, day: str) -> Path:
    inbox = (NOTIFY / subscriber_slug(name) / f"{day}.jsonl").resolve()
    if not inbox.is_relative_to(NOTIFY.resolve()):
        raise SystemExit(f"subscriber slug escapes notify/: {name!r}")
    return inbox


def wake_command_for(sub, meta: dict) -> tuple[str, str | None]:
    """Catalog name plus optional wake command.

    A subscriber object may carry `wake`. A string subscriber uses the optional
    topic field `wake` (a command string, or a map of name → command).
    """
    name = subscriber_name(sub)
    if isinstance(sub, dict):
        wake = sub.get("wake")
    else:
        wake = meta.get("wake")
        if isinstance(wake, dict):
            wake = wake.get(name)

    if wake is None:
        return name, None
    if not isinstance(wake, str):
        raise SystemExit(f"wake for {name!r} must be a command string")
    cmd = wake.strip()
    return name, cmd or None


def notify_line(event: dict, event_path: Path) -> str:
    payload = {
        "topic": event["topic"],
        "idempotency_key": event["idempotency_key"],
        "ts": event["ts"],
        "actor": event["actor"],
        "note": event.get("note") or "",
        "path": str(event_path),
    }
    return json.dumps(payload, ensure_ascii=False)


def append_notify(inbox: Path, line: str) -> None:
    inbox.parent.mkdir(parents=True, exist_ok=True)
    with inbox.open("a") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def run_wake(name: str, cmd: str, line: str) -> None:
    """Poke one subscriber. Non-zero exit or timeout leaves the publish in place."""
    payload = line if line.endswith("\n") else line + "\n"
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            shell=True,
            start_new_session=True,
        )
    except OSError as e:
        print(f"wake failed: {name}: {e}", file=sys.stderr)
        return
    try:
        _, err = proc.communicate(payload, timeout=WAKE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _kill_wake(proc)
        print(f"wake failed: {name}: timeout after {WAKE_TIMEOUT_S}s", file=sys.stderr)
        return
    if proc.returncode != 0:
        detail = f"exit {proc.returncode}"
        err_line = (err or "").strip().splitlines()
        if err_line:
            detail += f": {err_line[0][:300]}"
        print(f"wake failed: {name}: {detail}", file=sys.stderr)


def _kill_wake(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.communicate(timeout=1)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


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

    wake_jobs: list[tuple[str, str, str]] = []
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            # One flock covers the cross-day key scan and the writes that follow.
            hit = find_duplicate(args.topic, args.key)
            if hit:
                found, prev = hit
                print(json.dumps({"status": "duplicate", "path": str(found), "event": prev}, indent=2))
                return 0

            # Fail closed on corrupt state BEFORE appending the event.
            cur = load_state(state_path) if state_path else None
            subs = meta.get("subscribers") or []
            if not isinstance(subs, list):
                raise SystemExit("subscribers must be a list")
            targets = []
            for sub in subs:
                name, cmd = wake_command_for(sub, meta)
                targets.append((name, cmd, notify_inbox(name, day)))

            with path.open("a") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())

            if state_path is not None and cur is not None:
                # topic / last_key / updated_at / last_actor = last event on this file.
                # refs = last event only (jsonl is the audit; no bag merge across topics).
                # Hand fields outside this merge stay until updated in the same turn as publish.
                apply_event(cur, event)
                atomic_write_json(state_path, cur)

            # Inbox poke only. The day jsonl stays the record.
            line = notify_line(event, path)
            for name, cmd, inbox in targets:
                append_notify(inbox, line)
                if cmd:
                    wake_jobs.append((name, cmd, line))
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)

    for name, cmd, line in wake_jobs:
        run_wake(name, cmd, line)

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
