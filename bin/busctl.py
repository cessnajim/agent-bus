#!/usr/bin/env python3
"""Inspect agent-bus: topics | state | today | tail | stall | owed | coverage"""
from __future__ import annotations
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chase  # noqa: E402

ROOT = chase.bus_root()
CATALOG = ROOT / "catalog" / "topics.json"
EVENTS = ROOT / "events"
STATE = ROOT / "state"

def main():
    ap = argparse_parser()
    args = ap.parse_args()
    catalog = json.loads(CATALOG.read_text())

    if args.cmd == "topics":
        print(json.dumps({"rules": catalog.get("rules", {}),
            "follow_through": catalog.get("follow_through", {}),
            "topics": {
            k: dict(
                publishers=v.get("publishers"),
                subscribers=v.get("subscribers"),
                must_act=v.get("must_act", v.get("subscribers")),
                **({"must_act_rules": v.get("must_act_rules")} if v.get("must_act_rules") else {}),
                required_refs=v.get("required_refs"),
                done_means=v.get("done_means"),
                state_file=v.get("state_file"),
                **({"chase": v.get("chase")} if v.get("chase") is not None else {}),
            )
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

    if args.cmd == "coverage":
        import buscov
        rep = buscov.build_report(ROOT, days=args.days, lookback=args.lookback)
        print(json.dumps(rep, indent=2, ensure_ascii=False) if args.json else buscov.render_table(rep))
        return 0

    if args.cmd in ("stall", "owed"):
        print(json.dumps(chase.build_stall_report(ROOT), indent=2, ensure_ascii=False))
        return 0

    # "today" = the America/Los_Angeles calendar day. Legacy events were filed
    # by Eastern date with -04:00 offsets; events_for_bus_day normalizes them.
    day = getattr(args, "day", None) or chase.bus_now().date().isoformat()
    rows = [
        ev for ev in chase.events_for_bus_day(EVENTS, day)
        if not (getattr(args, "topic", None) and ev.get("topic") != args.topic)
    ]
    if args.cmd == "today":
        print(json.dumps(rows, indent=2, ensure_ascii=False)); return 0
    print(json.dumps(rows[-args.n:], indent=2, ensure_ascii=False)); return 0


def argparse_parser():
    import argparse
    ap = argparse.ArgumentParser(description="Read the agent-bus board")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("topics")
    p_state = sp.add_parser("state"); p_state.add_argument("name")
    p_today = sp.add_parser("today"); p_today.add_argument("--topic")
    p_today.add_argument("--day", help="PT date YYYY-MM-DD (default: today in America/Los_Angeles)")
    p_tail = sp.add_parser("tail"); p_tail.add_argument("--topic"); p_tail.add_argument("--n", type=int, default=20)
    p_cov = sp.add_parser("coverage", help="Per-publisher liveness: last event, counts, silent flag; Critic PASS with no downstream event")
    p_cov.add_argument("--days", type=int, default=3); p_cov.add_argument("--lookback", type=int, default=7); p_cov.add_argument("--json", action="store_true")
    sp.add_parser("stall", help="Owed next steps: WS short, Critic PASS without submit/park, open parks")
    sp.add_parser("owed", help="Alias of stall")
    return ap

if __name__ == "__main__":
    sys.exit(main())
