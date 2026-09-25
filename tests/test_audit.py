#!/usr/bin/env python3
"""Cross-day keys, wake failure, and audit/rebuild. Temp copy only."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

LIVE = Path(__file__).resolve().parents[1]
PY = sys.executable


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


def run(root: Path, script: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PY, str(root / "bin" / script), *args],
        cwd=root,
        text=True,
        capture_output=True,
    )


def publish(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return run(root, "publish.py", args)


def busctl(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return run(root, "busctl.py", args)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def slug(name: str) -> str:
    return name.lower().replace(" ", "-")


def require_json(proc: subprocess.CompletedProcess[str], label: str) -> dict:
    if proc.returncode not in (0, 1):
        fail(f"{label} exit {proc.returncode}\nstderr:\n{proc.stderr}\nstdout:\n{proc.stdout}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        fail(f"{label} stdout is not JSON ({e}):\n{proc.stdout}\nstderr:\n{proc.stderr}")


def shift_day(root: Path, day: str, prior: str) -> None:
    """Move a temp-tree day file to an earlier name. Does not touch the live tree."""
    src = root / "events" / f"{day}.jsonl"
    src.rename(root / "events" / f"{prior}.jsonl")
    for path in (root / "notify").glob(f"*/{day}.jsonl"):
        path.rename(path.with_name(f"{prior}.jsonl"))


def check_readme() -> None:
    text = (LIVE / "README.md").read_text()
    needed = [
        "is the record",
        "notify/<slug>/",
        "unique per topic across days",
        "checks the log against the state snapshots and the catalog",
        "cannot see Ashby, Adobe Contributor, or the GE tracker",
        "The day ledger still compares the log to live reality",
    ]
    missing = [line for line in needed if line not in text]
    if missing:
        fail(f"README missing: {missing}")
    needle = '("submitted", "target", "date", "holds", "submitted_on_count", "email", "band")'
    count = sum(p.read_text().count(needle) for p in (LIVE / "bin").glob("*.py"))
    if count != 1:
        fail(f"promote list should live in one function, found {count} copies")


def check_cross_day_and_wake(root: Path) -> str:
    catalog_path = root / "catalog" / "topics.json"
    catalog = json.loads(catalog_path.read_text())
    subs = catalog["topics"]["fte.reject"]["subscribers"]
    marker = root / "wake-hits.txt"
    script = root / "wake_fail.py"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        f"p = Path({str(marker)!r})\n"
        "with p.open('a') as fh:\n"
        "    fh.write(sys.stdin.read())\n"
        "raise SystemExit(1)\n"
    )
    catalog["topics"]["fte.reject"]["wake"] = f"{PY} {script}"
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n")

    first = publish(root, [
        "--topic", "fte.reject",
        "--actor", "FTE Apply",
        "--key", "once-across-days",
        "--ref", "company=Acme",
        "--ref", "role=Engineer",
        "--ref", "source_id=abc",
        "--note", "cross-day",
    ])
    if first.returncode != 0:
        fail(f"publish exit {first.returncode}\n{first.stderr}\n{first.stdout}")
    if "wake failed:" not in first.stderr or "exit 1" not in first.stderr:
        fail(f"wake failure missing on stderr: {first.stderr!r}")
    body = json.loads(first.stdout)
    if body.get("status") != "published":
        fail(body)
    day = Path(body["path"]).name.removesuffix(".jsonl")
    events = read_jsonl(Path(body["path"]))
    if len(events) != 1:
        fail(f"expected one event line, got {len(events)}")
    for name in subs:
        rows = read_jsonl(root / "notify" / slug(name) / f"{day}.jsonl")
        if len(rows) != 1:
            fail(f"{name} expected one notify line, got {len(rows)}")
        if set(rows[0]) != {"topic", "idempotency_key", "ts", "actor", "note", "path"}:
            fail(rows[0])
        if "refs" in rows[0]:
            fail("notify line dumped refs")
    if marker.read_text().count("once-across-days") != len(subs):
        fail(f"wake hits {marker.read_text().count('once-across-days')} want {len(subs)}")
    if not (root / "notify" / "writing-critic" / f"{day}.jsonl").is_file():
        fail("Writing Critic did not land in writing-critic")

    prior = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    shift_day(root, day, prior)
    event_bytes = (root / "events" / f"{prior}.jsonl").read_bytes()
    notify_bytes = {
        name: (root / "notify" / slug(name) / f"{prior}.jsonl").read_bytes() for name in subs
    }

    again = publish(root, [
        "--topic", "fte.reject",
        "--actor", "FTE Apply",
        "--key", "once-across-days",
        "--ref", "company=Acme",
        "--ref", "role=Engineer",
        "--ref", "source_id=abc",
        "--note", "cross-day",
    ])
    if again.returncode != 0:
        fail(f"cross-day duplicate exit {again.returncode}\n{again.stderr}\n{again.stdout}")
    dup = json.loads(again.stdout)
    if dup.get("status") != "duplicate":
        fail(f"expected duplicate, got {dup}")
    if prior not in dup.get("path", ""):
        fail(f"duplicate path should be the earlier file: {dup.get('path')}")
    if (root / "events" / f"{day}.jsonl").exists():
        fail("later day gained an event file")
    if (root / "events" / f"{prior}.jsonl").read_bytes() != event_bytes:
        fail("duplicate appended to the earlier event file")
    for name in subs:
        today_inbox = root / "notify" / slug(name) / f"{day}.jsonl"
        if today_inbox.exists():
            fail(f"duplicate wrote {today_inbox}")
        if (root / "notify" / slug(name) / f"{prior}.jsonl").read_bytes() != notify_bytes[name]:
            fail(f"duplicate rewrote notify for {name}")
    if marker.read_text().count("once-across-days") != len(subs):
        fail("duplicate ran wake")

    fresh = publish(root, [
        "--topic", "fte.reject",
        "--actor", "FTE Apply",
        "--key", "once-across-days-next",
        "--ref", "company=Acme",
        "--ref", "role=Engineer",
        "--ref", "source_id=abc",
        "--note", "new key",
    ])
    if fresh.returncode != 0 or json.loads(fresh.stdout).get("status") != "published":
        fail(f"new key should publish: {fresh.returncode} {fresh.stdout} {fresh.stderr}")
    today_events = read_jsonl(root / "events" / f"{day}.jsonl")
    if [ev.get("idempotency_key") for ev in today_events] != ["once-across-days-next"]:
        fail(today_events)
    return day


def check_audit_rebuild(root: Path) -> None:
    clean = busctl(root, ["audit"])
    if clean.returncode != 0:
        fail(f"audit should be clean before the hand edit:\n{clean.stdout}\n{clean.stderr}")

    published = publish(root, [
        "--topic", "ge.synced",
        "--actor", "Admin",
        "--key", "ge-audit-1",
        "--ref", "submitted_on_count=4",
    ])
    if published.returncode != 0:
        fail(f"ge publish failed: {published.stderr}\n{published.stdout}")
    state_path = root / "state" / "ge.json"
    state = json.loads(state_path.read_text())
    state["scratch"] = "keep-me"
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    still_clean = busctl(root, ["audit"])
    if still_clean.returncode != 0:
        fail(f"hand field should not be a finding:\n{still_clean.stdout}")

    state = json.loads(state_path.read_text())
    state["refs"]["submitted_on_count"] = 99
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    edited = state_path.read_bytes()
    event_before = {
        p: p.read_bytes() for p in (root / "events").glob("*.jsonl")
    }

    bad = busctl(root, ["audit"])
    if bad.returncode != 1:
        fail(f"hand-edited refs should fail audit, exit {bad.returncode}\n{bad.stdout}")
    report = json.loads(bad.stdout)
    state_findings = [f for f in report["findings"] if f.get("kind") == "state" and f.get("state") == "ge"]
    if not state_findings:
        fail(f"no ge state finding: {report}")
    if not any("submitted_on_count" in change for change in state_findings[0].get("changes", [])):
        fail(state_findings)

    dry = busctl(root, ["rebuild"])
    if dry.returncode != 0:
        fail(f"dry-run exit {dry.returncode}\n{dry.stderr}\n{dry.stdout}")
    if state_path.read_bytes() != edited:
        fail("dry-run wrote the state file")
    dry_body = json.loads(dry.stdout)
    ge_dry = [row for row in dry_body["results"] if row["state"] == "ge"]
    if not ge_dry or ge_dry[0]["action"] != "dry-run":
        fail(dry_body)
    if not any("submitted_on_count" in change for change in ge_dry[0]["changes"]):
        fail(ge_dry)

    written = busctl(root, ["rebuild", "--write"])
    if written.returncode != 0:
        fail(f"rebuild --write exit {written.returncode}\n{written.stderr}\n{written.stdout}")
    restored = json.loads(state_path.read_text())
    if restored.get("refs", {}).get("submitted_on_count") != 4:
        fail(f"rebuild did not restore refs: {restored}")
    if restored.get("scratch") != "keep-me":
        fail(f"rebuild dropped a hand field: {restored}")
    if restored.get("submitted_on_count") != 4:
        fail(f"promoted key not restored: {restored}")
    after = busctl(root, ["audit"])
    if after.returncode != 0:
        fail(f"audit after rebuild should pass:\n{after.stdout}\n{after.stderr}")
    event_after = {p: p.read_bytes() for p in (root / "events").glob("*.jsonl")}
    if event_before != event_after:
        fail("rebuild --write changed an event file")


def check_other_findings(root: Path) -> None:
    events = root / "events"
    events.mkdir(parents=True)
    ok = {
        "topic": "ge.synced",
        "ts": "1999-01-01T00:00:00+00:00",
        "idempotency_key": "reused-key",
        "actor": "Admin",
        "refs": {"submitted_on_count": 1},
        "state_delta": {},
    }
    (events / "1999-01-01.jsonl").write_text(json.dumps(ok) + "\n")
    (events / "1999-01-02.jsonl").write_text(json.dumps(ok) + "\n")
    bad = {
        "topic": "no.such.topic",
        "ts": "1999-01-03T00:00:00+00:00",
        "idempotency_key": "bad-topic",
        "actor": "Admin",
        "refs": {},
        "state_delta": {},
        "note": "n" * 401,
    }
    blank = {
        "topic": "ge.synced",
        "ts": "1999-01-03T00:00:01+00:00",
        "idempotency_key": "blank-ref",
        "actor": "Admin",
        "refs": {"submitted_on_count": "  "},
        "state_delta": {},
    }
    (events / "1999-01-03.jsonl").write_text(json.dumps(bad) + "\n" + json.dumps(blank) + "\n")
    proc = busctl(root, ["audit"])
    if proc.returncode != 1:
        fail(f"bad log should fail audit: {proc.returncode} {proc.stdout}")
    kinds = {f["kind"] for f in json.loads(proc.stdout)["findings"]}
    if not {"key_reused", "event", "notify"} <= kinds:
        fail(f"missing finding kinds: {kinds}")
    # Dry-run only. Do not rebuild --write this fixture into a snapshot.
    dry = busctl(root, ["rebuild"])
    if dry.returncode != 0:
        fail(dry.stderr)
    if (root / "state").exists() and any((root / "state").glob("*.json")):
        fail("dry-run created a state file")


def main() -> int:
    live_before = snapshot(LIVE)
    check_readme()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "bus"
        copy_tree(root)
        empty = busctl(root, ["audit"])
        if empty.returncode != 0:
            fail(f"empty tree audit: {empty.returncode} {empty.stdout} {empty.stderr}")
        check_cross_day_and_wake(root)
        check_audit_rebuild(root)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "bus"
        copy_tree(root)
        check_other_findings(root)
    if snapshot(LIVE) != live_before:
        fail("live events/state/notify changed")
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
