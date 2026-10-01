#!/usr/bin/env python3
"""Publish one event to the agent topic bus (Fedora). Idempotent on (topic, key) per day."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# bin/ is not a package; chase.py sits beside this script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import chase  # noqa: E402

ROOT = chase.bus_root()
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

    now = datetime.now().astimezone()
    EVENTS.mkdir(parents=True, exist_ok=True)
    day = now.date().isoformat()
    path = EVENTS / f"{day}.jsonl"
    state_name = meta.get("state_file")
    state_path = (STATE / f"{state_name}.json") if state_name else None
    submitted_floor = None
    submitted_stored = None
    debounce_warn = None
    chase_cleared: list[str] = []
    result_chase_error = None

    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            prior = load_day_events(path)
            for prev in prior:
                if prev.get("idempotency_key") == args.key and prev.get("topic") == args.topic:
                    print(json.dumps({"status": "duplicate", "path": str(path), "event": prev}, indent=2))
                    return 0

            # Fail closed on corrupt state BEFORE appending the event.
            cur = load_state(state_path) if state_path else None

            # --delta/--ref submitted is the absolute count for the day, not +1.
            # Floor the snapshot at today's fte.submit ledger so a second
            # submitted=1 cannot leave state stuck at 1. Jsonl keeps the claim.
            ledger = sum(1 for prev in prior if prev.get("topic") == "fte.submit")
            if args.topic == "fte.submit":
                ledger += 1
            writes_submitted = "submitted" in refs or "submitted" in event["state_delta"]
            if state_name == "ws" and (args.topic == "fte.submit" or writes_submitted):
                claimed = event["state_delta"].get("submitted", refs.get("submitted"))
                stored, adjusted = chase.submitted_floor(claimed, ledger)
                submitted_stored = stored
                if adjusted:
                    submitted_floor = {"claimed": claimed, "ledger": ledger, "stored": stored}

            if args.topic == "ws.short_of_target":
                debounce = chase.debounce_minutes(
                    meta.get("chase") or {},
                    catalog.get("follow_through") or {},
                )
                debounce_warn = chase.short_republish_warning(
                    prior, refs, event["state_delta"], debounce, now
                )

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
                if submitted_stored is not None:
                    cur["submitted"] = submitted_stored
                    cur.setdefault("state", {})["submitted"] = submitted_stored
                # Parks: open/cleared rows keyed by source_id or item_id.
                if state_name == "parks":
                    chase.apply_parks_row(cur, refs, event, args.key, args.actor)
                if args.topic == "chase.owed" and refs.get("stall_id") is not None:
                    chase.apply_owed_row(cur, refs, event, args.key, args.actor)
                atomic_write_json(state_path, cur)

            if args.topic != "chase.owed":
                owed_path = STATE / "owed.json"
                try:
                    owed = load_state(owed_path) if owed_path.exists() else {}
                except SystemExit as exc:
                    result_chase_error = str(exc)
                    owed = None
                else:
                    result_chase_error = None
                if isinstance(owed, dict):
                    chase_cleared = chase.owed_ids_cleared_by(owed.get("owed") or {}, event)
                    if chase_cleared:
                        owed_map = owed.setdefault("owed", {})
                        for stall_id in chase_cleared:
                            owed_map.pop(stall_id, None)
                        owed["open_count"] = sum(
                            1
                            for row in owed_map.values()
                            if isinstance(row, dict) and row.get("status") == "open"
                        )
                        owed["updated_at"] = event["ts"]
                        owed["last_proof_topic"] = args.topic
                        atomic_write_json(owed_path, owed)
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)

    result = {
        "status": "published",
        "path": str(path),
        "subscribers": meta.get("subscribers", []),
        "done_means": meta.get("done_means"),
        "state_path": str(state_path) if state_path else None,
        "event": event,
    }
    if submitted_floor:
        result["submitted_floor"] = submitted_floor
    if debounce_warn:
        result["chase_warn"] = debounce_warn
    if chase_cleared:
        result["chase_cleared"] = chase_cleared
    if result_chase_error:
        result["chase_clear_error"] = result_chase_error

    # Post-publish hooks (non-duplicate only). Failures are reported but do not
    # roll back the bus event — publish still exits 0.
    if args.topic == "fte.submit" and os.environ.get("AGENT_BUS_SKIP_HOOKS") != "1":
        result["hooks"] = {"ge_sync": run_ge_sync_hook(event)}

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def run_ge_sync_hook(event: dict) -> dict:
    """Invoke GE tracker+canvas sync for a fresh fte.submit. Never raises."""
    hook = ROOT / "bin" / "ge_sync_from_fte_submit.py"
    if not hook.exists():
        return {"status": "error", "error": f"hook missing: {hook}"}
    try:
        proc = subprocess.run(
            [sys.executable, str(hook), "--event-json", "-"],
            input=json.dumps(event),
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(ROOT),
        )
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        parsed = None
        if stdout:
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                # Hook may print noise then JSON — take last JSON object
                for line in reversed(stdout.splitlines()):
                    line = line.strip()
                    if line.startswith("{"):
                        try:
                            parsed = json.loads(line)
                            break
                        except json.JSONDecodeError:
                            continue
        status = "ok"
        if proc.returncode != 0:
            status = "error"
        elif isinstance(parsed, dict) and parsed.get("status") == "error":
            status = "error"
        return {
            "status": status,
            "returncode": proc.returncode,
            "stdout": parsed if parsed is not None else stdout[:2000],
            "stderr": stderr[:2000],
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


if __name__ == "__main__":
    sys.exit(main())
