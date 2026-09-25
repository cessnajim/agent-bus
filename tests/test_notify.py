#!/usr/bin/env python3
"""Notify-inbox checks against a temp copy of the tree.

Does not write the live events/, state/, or notify/ directories.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LIVE = Path(__file__).resolve().parents[1]
NOTIFY_KEYS = {"topic", "idempotency_key", "ts", "actor", "note", "path"}


def fail(msg: str) -> None:
    raise SystemExit(msg)


def snapshot(root: Path) -> dict:
    out = {}
    for name in ("events", "state", "notify"):
        base = root / name
        if not base.exists():
            out[name] = None
            continue
        out[name] = {
            str(p.relative_to(root)): p.read_bytes()
            for p in sorted(base.rglob("*"))
            if p.is_file()
        }
    return out


def copy_tree(dst: Path) -> None:
    def ignore(_dir, names):
        skip = {".git", "__pycache__", "events", "state", "notify", ".publish.lock"}
        return {n for n in names if n in skip or n.endswith((".pyc", ".pyo"))}

    shutil.copytree(LIVE, dst, ignore=ignore)


def publish(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(root / "bin" / "publish.py"), *args],
        cwd=root,
        text=True,
        capture_output=True,
    )


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def slug(name: str) -> str:
    return name.lower().replace(" ", "-")


def catalog_path(root: Path) -> Path:
    return root / "catalog" / "topics.json"


def load_catalog(root: Path) -> dict:
    return json.loads(catalog_path(root).read_text())


def save_catalog(root: Path, catalog: dict) -> None:
    catalog_path(root).write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")


def assert_notify(row: dict, event: dict, event_path: str) -> None:
    if set(row) != NOTIFY_KEYS:
        fail(f"notify keys {sorted(row)} != {sorted(NOTIFY_KEYS)}")
    if row["topic"] != event["topic"] or row["idempotency_key"] != event["idempotency_key"]:
        fail(f"notify identity mismatch: {row}")
    if row["ts"] != event["ts"] or row["actor"] != event["actor"]:
        fail(f"notify ts/actor mismatch: {row}")
    if row["note"] != (event.get("note") or ""):
        fail(f"note {row['note']!r} != {event.get('note') or ''!r}")
    if row["path"] != event_path:
        fail(f"path {row['path']!r} != {event_path!r}")


def wake_cmd(path: Path, marker: Path, *, code: int = 1, sleep_s: float = 0, write_pid: bool = False) -> str:
    body = (
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "data = sys.stdin.read()\n"
        f"marker = Path({str(marker)!r})\n"
        "marker.parent.mkdir(parents=True, exist_ok=True)\n"
    )
    if write_pid:
        body += "marker.write_text(str(os.getpid()))\n"
    else:
        body += "with marker.open('a') as fh:\n    fh.write(data)\n"
    body += f"time.sleep({sleep_s!r})\nraise SystemExit({code})\n"
    path.write_text(body)
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(path))}"


def require_published(proc: subprocess.CompletedProcess[str], label: str) -> dict:
    if proc.returncode != 0:
        fail(f"{label} exit {proc.returncode}\nstderr:\n{proc.stderr}\nstdout:\n{proc.stdout}")
    try:
        body = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        fail(f"{label} stdout is not JSON: {e}\n{proc.stdout}")
    if body.get("status") != "published":
        fail(f"{label} status: {body}")
    return body


def main() -> int:
    live_before = snapshot(LIVE)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "bus"
        copy_tree(root)
        catalog = load_catalog(root)
        subs = catalog["topics"]["fte.reject"]["subscribers"]
        if subs != ["Admin", "Unemployment", "Ops", "Writing Critic"]:
            fail(f"unexpected fte.reject subscribers: {subs}")

        first = publish(root, [
            "--topic", "fte.reject",
            "--actor", "FTE Apply",
            "--key", "notify-test-reject",
            "--ref", "company=Acme",
            "--ref", "role=Engineer",
            "--ref", "source_id=abc",
            "--note", "reject note",
        ])
        body = require_published(first, "fte.reject")
        if first.stderr.strip():
            fail(f"file-only publish wrote stderr: {first.stderr}")
        event_path = body["path"]
        day = Path(event_path).name.removesuffix(".jsonl")
        events = read_jsonl(Path(event_path))
        if len(events) != 1 or "refs" not in events[0]:
            fail(f"event record missing: {events}")
        for name in subs:
            inbox = root / "notify" / slug(name) / f"{day}.jsonl"
            rows = read_jsonl(inbox)
            if len(rows) != 1:
                fail(f"{name} notify count {len(rows)} ({inbox})")
            assert_notify(rows[0], body["event"], event_path)
        if not (root / "notify" / "writing-critic" / f"{day}.jsonl").is_file():
            fail("Writing Critic did not slug to writing-critic")

        again = publish(root, [
            "--topic", "fte.reject",
            "--actor", "FTE Apply",
            "--key", "notify-test-reject",
            "--ref", "company=Acme",
            "--ref", "role=Engineer",
            "--ref", "source_id=abc",
            "--note", "reject note",
        ])
        if again.returncode != 0:
            fail(f"duplicate exit {again.returncode}: {again.stderr}")
        dup = json.loads(again.stdout)
        if dup.get("status") != "duplicate":
            fail(f"expected duplicate, got {dup}")
        if len(read_jsonl(Path(event_path))) != 1:
            fail("duplicate appended an event")
        for name in subs:
            if len(read_jsonl(root / "notify" / slug(name) / f"{day}.jsonl")) != 1:
                fail(f"duplicate appended notify for {name}")

        marker = root / "wake-hits.txt"
        catalog["topics"]["ge.synced"]["wake"] = wake_cmd(root / "wake_fail.py", marker, code=1)
        save_catalog(root, catalog)
        admin_inbox = root / "notify" / "admin" / f"{day}.jsonl"
        admin_before = len(read_jsonl(admin_inbox))
        woken = publish(root, [
            "--topic", "ge.synced",
            "--actor", "Admin",
            "--key", "notify-test-ge",
            "--ref", "submitted_on_count=4",
        ])
        ge = require_published(woken, "ge.synced wake exit 1")
        if "wake failed: Admin: exit 1" not in woken.stderr:
            fail(f"missing wake failure on stderr: {woken.stderr!r}")
        ge_events = [ev for ev in read_jsonl(Path(ge["path"])) if ev.get("idempotency_key") == "notify-test-ge"]
        if len(ge_events) != 1:
            fail(f"wake exit 1 dropped the event: {ge_events}")
        admin_rows = read_jsonl(admin_inbox)
        ge_notes = [row for row in admin_rows if row.get("idempotency_key") == "notify-test-ge"]
        if len(admin_rows) != admin_before + 1 or len(ge_notes) != 1:
            fail(f"wake exit 1 notify lines: {admin_rows}")
        assert_notify(ge_notes[0], ge["event"], ge["path"])
        if ge_notes[0]["note"] != "":
            fail("omitted note should be an empty string on the notify line")
        state = json.loads((root / "state" / "ge.json").read_text())
        if state.get("topic") != "ge.synced" or state.get("last_key") != "notify-test-ge":
            fail(f"state snapshot changed shape: {state}")
        if state.get("refs", {}).get("submitted_on_count") != 4:
            fail(f"state refs missing submitted_on_count: {state}")
        if marker.read_text().count("notify-test-ge") != 1:
            fail(f"wake stdin was not the notify line: {marker.read_text()!r}")
        state_bytes = (root / "state" / "ge.json").read_bytes()
        admin_bytes = admin_inbox.read_bytes()
        event_bytes = Path(ge["path"]).read_bytes()
        dup_ge = publish(root, [
            "--topic", "ge.synced",
            "--actor", "Admin",
            "--key", "notify-test-ge",
            "--ref", "submitted_on_count=4",
        ])
        if dup_ge.returncode != 0 or json.loads(dup_ge.stdout).get("status") != "duplicate":
            fail(f"ge duplicate failed: {dup_ge.returncode} {dup_ge.stdout} {dup_ge.stderr}")
        if marker.read_text().count("notify-test-ge") != 1:
            fail("duplicate ran wake")
        if admin_inbox.read_bytes() != admin_bytes or Path(ge["path"]).read_bytes() != event_bytes:
            fail("duplicate wrote event or notify bytes")
        if (root / "state" / "ge.json").read_bytes() != state_bytes:
            fail("duplicate rewrote state")

        fte_marker = root / "wake-fte.txt"
        day_marker = root / "wake-day.txt"
        catalog = load_catalog(root)
        catalog["topics"]["critic.pass"]["subscribers"] = [
            {"name": "FTE Apply", "wake": wake_cmd(root / "wake_fte.py", fte_marker, code=1)},
            "day-ledger",
        ]
        catalog["topics"]["critic.pass"]["wake"] = {
            "day-ledger": wake_cmd(root / "wake_day.py", day_marker, code=1),
        }
        save_catalog(root, catalog)
        crit = publish(root, [
            "--topic", "critic.pass",
            "--actor", "Writing Critic",
            "--key", "notify-test-critic",
            "--ref", "company=Acme",
            "--ref", "role=Engineer",
            "--ref", "packet_path=/tmp/packet",
            "--note", "pass",
        ])
        require_published(crit, "critic.pass")
        if "wake failed: FTE Apply: exit 1" not in crit.stderr:
            fail(f"object wake stderr missing: {crit.stderr}")
        if "wake failed: day-ledger: exit 1" not in crit.stderr:
            fail(f"mapped wake stderr missing: {crit.stderr}")
        for folder in ("fte-apply", "day-ledger"):
            rows = read_jsonl(root / "notify" / folder / f"{day}.jsonl")
            if len(rows) != 1 or rows[0]["topic"] != "critic.pass":
                fail(f"{folder} inbox: {rows}")
            if set(rows[0]) != NOTIFY_KEYS:
                fail(rows[0])
        fte_payload = json.loads(fte_marker.read_text())
        if set(fte_payload) != NOTIFY_KEYS or "refs" in fte_payload:
            fail(f"wake stdin dumped extra fields: {fte_payload}")
        if day_marker.read_text().count("notify-test-critic") != 1:
            fail("day-ledger wake did not run once")

        # A topic-level wake string applies to string subscribers only.
        inherit_marker = root / "wake-inherit.txt"
        catalog = load_catalog(root)
        catalog["topics"]["fte.hold_cleared"]["subscribers"] = [
            {"name": "Writing Critic"},
            "Ops",
        ]
        catalog["topics"]["fte.hold_cleared"]["wake"] = wake_cmd(
            root / "wake_inherit.py", inherit_marker, code=1
        )
        save_catalog(root, catalog)
        held = publish(root, [
            "--topic", "fte.hold_cleared",
            "--actor", "Admin",
            "--key", "notify-test-hold",
            "--ref", "company=Acme",
            "--ref", "role=Engineer",
            "--ref", "hold_reason=packet",
        ])
        require_published(held, "fte.hold_cleared")
        if inherit_marker.read_text().count("notify-test-hold") != 1:
            fail(f"topic wake string should run once for the string subscriber: {inherit_marker.read_text()!r}")
        if "wake failed: Ops: exit 1" not in held.stderr:
            fail(held.stderr)
        if "wake failed: Writing Critic" in held.stderr:
            fail("subscriber object without wake inherited the topic command")

        pid_marker = root / "wake-pid.txt"
        catalog = load_catalog(root)
        catalog["topics"]["ge.synced"]["wake"] = wake_cmd(
            root / "wake_sleep.py", pid_marker, code=0, sleep_s=30, write_pid=True
        )
        save_catalog(root, catalog)
        admin_n = len(read_jsonl(admin_inbox))
        timed = publish(root, [
            "--topic", "ge.synced",
            "--actor", "Admin",
            "--key", "notify-test-ge-timeout",
            "--ref", "submitted_on_count=5",
        ])
        require_published(timed, "wake timeout")
        if "wake failed: Admin: timeout after 5s" not in timed.stderr:
            fail(f"timeout stderr: {timed.stderr!r}")
        if len(read_jsonl(admin_inbox)) != admin_n + 1:
            fail("timeout dropped the notify line")
        if "notify-test-ge-timeout" not in Path(ge["path"]).read_text():
            fail("timeout dropped the event line")
        if not pid_marker.exists():
            fail("timed-out wake never started")
        pid = int(pid_marker.read_text().strip())
        alive = True
        for _ in range(20):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                alive = False
                break
            time.sleep(0.05)
        if alive:
            fail(f"wake process {pid} still alive after timeout")

        ev_before = Path(event_path).read_bytes()
        notify_before = snapshot(root)["notify"]
        bad = publish(root, [
            "--topic", "fte.submit",
            "--actor", "Admin",
            "--key", "notify-test-missing",
            "--ref", "company=Acme",
        ])
        if bad.returncode == 0:
            fail("missing required refs exited 0")
        if Path(event_path).read_bytes() != ev_before:
            fail("missing refs wrote an event")
        if snapshot(root)["notify"] != notify_before:
            fail("missing refs wrote a notify line")

    if snapshot(LIVE) != live_before:
        fail("live events/state/notify changed")
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
