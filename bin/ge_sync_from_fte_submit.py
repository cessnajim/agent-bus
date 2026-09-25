#!/usr/bin/env python3
"""Sync GE tracker+canvas from an fte.submit bus event.

Accepts CLI args or a JSON event (stdin / --event-json). Idempotent on
opportunity id ({company-slug}-{short-role-slug}, e.g. openloop-dir-ai-enablement).

On success: upserts opportunity + company to entered, appends events.jsonl,
renders canvas, publishes ge.synced (unless --dry-run / --skip-publish).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BUS_ROOT = Path(__file__).resolve().parents[1]
PUBLISH = BUS_ROOT / "bin" / "publish.py"
PIPELINE = Path("/home/jim/Projects/GeneralEmployment/pipeline")
TRACKER = PIPELINE / "tracker.json"
EVENTS = PIPELINE / "events.jsonl"
RENDER = PIPELINE / "render_canvas.py"

REQUIRED = ("company", "role", "source_id", "packet_path")
ENTERED_STATUSES = {"entered", "screen", "scope", "loop", "offer", "signed"}
STOP = {"of", "the", "and", "a", "an", "for", "in", "to", "at", "on", "with", "or"}


def company_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def role_slug(title: str) -> str:
    t = (title or "").lower()
    for ch in ("&", "/", "—", "–", ",", "(", ")"):
        t = t.replace(ch, " ")
    words = re.findall(r"[a-z0-9]+", t)
    out: list[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        if w in ("vice", "vp") and i + 1 < len(words) and words[i + 1] == "president":
            out.append("vp")
            i += 2
            continue
        if w == "director":
            out.append("dir")
            i += 1
            continue
        if w in STOP:
            i += 1
            continue
        out.append(w)
        i += 1
    return "-".join(out)


def derive_opp_id(company: str, role: str) -> str:
    return f"{company_slug(company)}-{role_slug(role)}"


def key_norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def today_iso() -> str:
    return datetime.now().astimezone().date().isoformat()


def event_date(refs: dict, event: dict | None) -> str:
    for candidate in (
        refs.get("submitted_on"),
        (event or {}).get("state_delta", {}).get("date") if event else None,
        ((event or {}).get("ts") or "")[:10] if event else None,
    ):
        if candidate and re.match(r"^\d{4}-\d{2}-\d{2}", str(candidate)):
            return str(candidate)[:10]
    return today_iso()


def submitted_count(tracker: dict) -> int:
    n = 0
    for o in tracker.get("opportunities") or []:
        if o.get("submitted_on") or o.get("status") in ENTERED_STATUSES:
            n += 1
    return n


def find_company(companies: list[dict], name: str) -> dict | None:
    want = key_norm(name)
    if not want:
        return None
    for row in companies:
        if key_norm(row.get("id", "")) == want or key_norm(row.get("company", "")) == want:
            return row
        for alias in row.get("harvest_names") or []:
            if key_norm(alias) == want:
                return row
    return None


def resolve_opp_id(
    tracker: dict,
    company: str,
    role: str,
    source_id: str,
    packet_path: str,
    bus_key: str,
) -> str:
    opps = tracker.get("opportunities") or []
    sid = str(source_id or "").strip()

    # 1) Match existing by job_id / source_id
    if sid:
        for o in opps:
            if str(o.get("job_id") or "") == sid or str(o.get("source_id") or "") == sid:
                if o.get("id"):
                    return o["id"]

    # 2) Match existing by company + title
    ck, rk = key_norm(company), key_norm(role)
    for o in opps:
        if key_norm(o.get("company", "")) == ck and key_norm(o.get("title", "")) == rk:
            if o.get("id"):
                return o["id"]

    # 3) packet_path basename (FTE Apply often uses opp id as folder name)
    base = Path(packet_path or "").name or Path(packet_path or "").parent.name
    base = (base or "").strip("/").strip()
    if base and re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)+$", base):
        for o in opps:
            if o.get("id") == base:
                return base
        # Prefer derived if it matches basename; else use basename for new
        derived = derive_opp_id(company, role)
        if base == derived or base.startswith(company_slug(company) + "-"):
            return base

    # 4) Strip date / -pass suffix from bus key
    if bus_key:
        m = re.match(r"^(.+?)-\d{4}-\d{2}-\d{2}(?:-.*)?$", bus_key)
        if m:
            cand = m.group(1)
            for o in opps:
                if o.get("id") == cand:
                    return cand
            if cand.startswith(company_slug(company) + "-"):
                return cand

    return derive_opp_id(company, role)


def find_opp(opps: list[dict], opp_id: str) -> dict | None:
    for o in opps:
        if o.get("id") == opp_id:
            return o
    return None


def build_refs_from_args(args: argparse.Namespace) -> dict:
    refs = {}
    for k in (
        "company",
        "role",
        "source_id",
        "packet_path",
        "url",
        "confirmation",
        "ats",
        "location",
        "submitted_on",
    ):
        v = getattr(args, k, None)
        if v is not None and v != "":
            refs[k] = v
    return refs


def load_event(args: argparse.Namespace) -> dict | None:
    """Load event from --event-json (path, inline JSON, or '-' for stdin).

    When CLI provides --company, skip auto-stdin so non-tty shells do not hang.
    """
    if args.event_json:
        if args.event_json == "-":
            raw = sys.stdin.read().strip()
            if not raw:
                return None
            return json.loads(raw)
        path = Path(args.event_json)
        if path.exists():
            return json.loads(path.read_text())
        return json.loads(args.event_json)
    # Auto-stdin only when caller did not pass primary CLI fields.
    if not args.company and not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            return json.loads(raw)
    return None

def soft_fail(msg: str) -> int:
    print(msg, file=sys.stderr)
    print(json.dumps({"status": "error", "error": msg}, indent=2))
    return 0  # soft: required-field / validation miss


def hard_fail(msg: str) -> int:
    print(msg, file=sys.stderr)
    print(json.dumps({"status": "error", "error": msg}, indent=2))
    return 1


def upsert_opportunity(tracker: dict, opp_id: str, refs: dict, day: str, bus_key: str, actor: str) -> str:
    opps = tracker.setdefault("opportunities", [])
    existing = find_opp(opps, opp_id)
    note_bits = [
        f"bus key {bus_key}" if bus_key else None,
        "FTE Apply" if (actor or "").lower() in ("fte apply", "fte-apply", "") else (actor or "FTE Apply"),
        "fte.submit → GE sync",
    ]
    note_line = "; ".join(b for b in note_bits if b)

    if existing is None:
        row = {
            "id": opp_id,
            "company": refs["company"],
            "title": refs["role"],
            "url": refs.get("url") or "",
            "location": refs.get("location") or "",
            "track": "fte",
            "status": "entered",
            "submitted_on": day,
            "entered_on": day,
            "follow_up_on": "",
            "confirmation": refs.get("confirmation") or "",
            "next_action": "Wait HR screen.",
            "note": note_line,
            "job_id": str(refs.get("source_id", "")),
        }
        if refs.get("ats"):
            row["ats"] = refs["ats"]
        opps.insert(0, row)
        return "created"

    # Idempotent update. Never resurrect lost; never demote screen/scope/loop/offer/signed.
    st = existing.get("status") or "watch"
    if st == "lost":
        pass
    elif st not in ENTERED_STATUSES:
        existing["status"] = "entered"

    if not existing.get("submitted_on"):
        existing["submitted_on"] = day
    if not existing.get("entered_on"):
        existing["entered_on"] = day
    existing["job_id"] = str(refs.get("source_id") or existing.get("job_id") or "")
    for k in ("url", "confirmation", "ats", "location"):
        if refs.get(k):
            existing[k] = refs[k]
    existing.setdefault("company", refs["company"])
    existing.setdefault("title", refs["role"])
    existing["track"] = existing.get("track") or "fte"
    prev_note = (existing.get("note") or "").rstrip()
    marker = "fte.submit → GE sync"
    if marker not in prev_note:
        existing["note"] = f"{prev_note}; {note_line}".lstrip("; ").strip() if prev_note else note_line
    return "updated"


def upsert_company(tracker: dict, refs: dict, day: str, opp_id: str, bus_key: str) -> None:
    companies = tracker.setdefault("companies", [])
    row = find_company(companies, refs["company"])
    cid = company_slug(refs["company"])
    link = {
        "label": f"Submitted {day}: {refs['role']}",
        "url": refs.get("url") or "",
        "checked": f"WS submit {day}",
    }
    if row is None:
        row = {
            "id": cid,
            "company": refs["company"],
            "lane": 1,
            "priority": 50,
            "track": "fte",
            "status": "entered",
            "entered_on": day,
            "last_touch": day,
            "next_action": "Wait HR screen.",
            "harvest_names": [refs["company"]],
            "next_links": [link] if link["url"] else [],
            "notes": f"fte.submit bus {bus_key}; FTE Apply; opp {opp_id}",
            "packet": [],
            "contact": "",
            "outreach_variant": "",
            "follow_up_on": "",
            "live_postings": [],
            "harvest_note": "",
        }
        companies.insert(0, row)
        return

    st = row.get("status") or "watch"
    if st != "lost":
        if st not in ENTERED_STATUSES:
            row["status"] = "entered"
        if not row.get("entered_on"):
            row["entered_on"] = day
    row["last_touch"] = day
    row["track"] = row.get("track") or "fte"
    if refs.get("url"):
        links = row.setdefault("next_links", [])
        if not any((l.get("url") == refs["url"]) for l in links if isinstance(l, dict)):
            links.insert(0, link)
    marker = f"opp {opp_id}"
    notes = (row.get("notes") or "").rstrip()
    if marker not in notes:
        add = f"fte.submit bus {bus_key}; FTE Apply; opp {opp_id}"
        row["notes"] = f"{notes}; {add}".lstrip("; ").strip() if notes else add


def append_event(refs: dict, opp_id: str, day: str, dry_run: bool) -> dict:
    ev = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "kind": "entered",
        "company": refs["company"],
        "id": opp_id,
        "role": refs["role"],
        "status": "entered",
        "submitted_on": day,
        "source": "fte_submit_bus_hook",
        "job_id": str(refs.get("source_id", "")),
    }
    for k in ("confirmation", "url", "ats", "location"):
        if refs.get(k):
            ev[k] = refs[k]
    ev["note"] = f"FTE Apply submit synced via agent-bus fte.submit hook; opp {opp_id}"
    if dry_run:
        return ev
    with EVENTS.open("a") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return ev


def render_canvas(dry_run: bool) -> dict:
    if dry_run:
        return {"ok": True, "dry_run": True}
    env = os.environ.copy()
    env["HOME"] = "/home/jim"
    try:
        r = subprocess.run(
            ["python3", str(RENDER)],
            cwd=str(PIPELINE),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return {
            "ok": r.returncode == 0,
            "returncode": r.returncode,
            "stdout": (r.stdout or "").strip()[:500],
            "stderr": (r.stderr or "").strip()[:500],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def publish_ge_synced(count: int, opp_id: str, day: str, dry_run: bool, skip: bool) -> dict:
    if dry_run or skip:
        return {"status": "skipped", "reason": "dry-run" if dry_run else "skip-publish"}
    key = f"ge-{opp_id}-{day}"
    try:
        r = subprocess.run(
            [
                str(PUBLISH),
                "--topic",
                "ge.synced",
                "--actor",
                "agent-bus-hook",
                "--key",
                key,
                "--ref",
                f"submitted_on_count={count}",
                "--note",
                f"auto GE sync after fte.submit for {opp_id}",
            ],
            cwd=str(BUS_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "AGENT_BUS_SKIP_HOOKS": "1"},
        )
        out = (r.stdout or "").strip()
        try:
            payload = json.loads(out) if out else {}
        except json.JSONDecodeError:
            payload = {"raw": out[:1000]}
        return {
            "status": "ok" if r.returncode == 0 else "error",
            "returncode": r.returncode,
            "key": key,
            "result": payload,
            "stderr": (r.stderr or "").strip()[:500],
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--company")
    ap.add_argument("--role")
    ap.add_argument("--source_id")
    ap.add_argument("--packet_path")
    ap.add_argument("--url")
    ap.add_argument("--confirmation")
    ap.add_argument("--ats")
    ap.add_argument("--location")
    ap.add_argument("--submitted_on")
    ap.add_argument("--key", default="", help="bus idempotency key")
    ap.add_argument("--actor", default="FTE Apply")
    ap.add_argument("--event-json", default="", help="path or inline JSON event")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-publish", action="store_true", help="skip ge.synced publish")
    ap.add_argument("--skip-canvas", action="store_true")
    args = ap.parse_args()

    event = None
    try:
        event = load_event(args)
    except Exception as e:
        return soft_fail(f"invalid event JSON: {e}")

    refs = dict((event or {}).get("refs") or {})
    # CLI overrides / fills
    cli_refs = build_refs_from_args(args)
    refs.update({k: v for k, v in cli_refs.items() if v is not None and v != ""})

    missing = [r for r in REQUIRED if not refs.get(r) and refs.get(r) != 0]
    # source_id may be int 0? treat missing only if absent/empty string
    missing = [r for r in REQUIRED if r not in refs or refs[r] is None or refs[r] == ""]
    if missing:
        return soft_fail(f"missing required fields: {', '.join(missing)}")

    bus_key = args.key or (event or {}).get("idempotency_key") or ""
    actor = args.actor or (event or {}).get("actor") or "FTE Apply"
    day = event_date(refs, event)

    if not TRACKER.exists():
        return hard_fail(f"tracker missing: {TRACKER}")

    try:
        tracker = json.loads(TRACKER.read_text())
    except Exception as e:
        return hard_fail(f"corrupt tracker: {e}")

    before = submitted_count(tracker)
    opp_id = resolve_opp_id(
        tracker,
        str(refs["company"]),
        str(refs["role"]),
        str(refs["source_id"]),
        str(refs["packet_path"]),
        bus_key,
    )

    planned = {
        "opp_id": opp_id,
        "day": day,
        "company": refs["company"],
        "role": refs["role"],
        "source_id": refs["source_id"],
        "bus_key": bus_key,
    }

    if args.dry_run:
        existing = find_opp(tracker.get("opportunities") or [], opp_id)
        company_row = find_company(tracker.get("companies") or [], refs["company"])
        summary = {
            "status": "dry_run",
            "action": "updated" if existing else "created",
            "planned": planned,
            "existing_opp": {
                "id": (existing or {}).get("id"),
                "status": (existing or {}).get("status"),
                "submitted_on": (existing or {}).get("submitted_on"),
            }
            if existing
            else None,
            "existing_company_status": (company_row or {}).get("status") if company_row else None,
            "submitted_count_before": before,
            "submitted_count_after": before,  # no write
            "canvas": {"ok": True, "dry_run": True},
            "ge_synced": {"status": "skipped", "reason": "dry-run"},
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    # Snapshot for idempotent event append: skip duplicate entered from this source today
    prior_hook = False
    if EVENTS.exists():
        for line in EVENTS.read_text().splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                e.get("kind") == "entered"
                and e.get("id") == opp_id
                and e.get("source") == "fte_submit_bus_hook"
                and str(e.get("submitted_on") or "")[:10] == day
            ):
                prior_hook = True
                break

    action = upsert_opportunity(tracker, opp_id, refs, day, bus_key, actor)
    # Guard: never flip ZoomInfo/OpenLoop (or any) away from advanced entered statuses incorrectly.
    # upsert_opportunity already preserves ENTERED_STATUSES and leaves lost alone.
    upsert_company(tracker, refs, day, opp_id, bus_key)

    tracker["updated"] = day
    try:
        tmp = TRACKER.with_suffix(f".json.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(tracker, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, TRACKER)
    except Exception as e:
        return hard_fail(f"failed writing tracker: {e}")

    if not prior_hook:
        try:
            append_event(refs, opp_id, day, dry_run=False)
        except Exception as e:
            print(f"events.jsonl append failed: {e}", file=sys.stderr)

    canvas = {"ok": True, "skipped": True} if args.skip_canvas else render_canvas(False)
    after_tracker = json.loads(TRACKER.read_text())
    after = submitted_count(after_tracker)

    ge = publish_ge_synced(after, opp_id, day, dry_run=False, skip=args.skip_publish)

    summary = {
        "status": "ok",
        "action": action,
        "opp_id": opp_id,
        "submitted_count_before": before,
        "submitted_count_after": after,
        "canvas": canvas,
        "ge_synced": ge,
        "event_appended": not prior_hook,
        "day": day,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    # Soft: canvas/publish failures reported but exit 0 (bus already progressive).
    # Hard only on tracker write / load failures (already returned).
    return 0


if __name__ == "__main__":
    sys.exit(main())
