#!/usr/bin/env python3
"""Cron-less bus watchdog (2026-10-23). Silent when healthy.

  bin/bus_watchdog.py [--days 3] [--json] [--now ISO]

Exit 0 and prints nothing when every publisher is within its threshold and every
Critic PASS lane has a downstream event. Exit 2 and prints a short summary when
a publisher is SILENT/NEVER or a PASS has no downstream event past SLA.
Exit 1 on a tooling error. Thresholds: catalog/topics.json -> coverage
(silent_days, weekdays_only, snooze_until, expected_downstream).
Read-only: it never writes events or state. Wire it to a routine that reports
only when the exit code is non-zero.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chase  # noqa: E402
import buscov  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--lookback", type=int, default=7, help="days of Critic PASS history checked for missing downstream events")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--now", help="override clock (ISO, tests)")
    args = ap.parse_args(argv)
    try:
        now = chase.parse_ts(args.now) if args.now else None
        rep = buscov.build_report(now=now, days=args.days, lookback=args.lookback)
    except Exception as exc:  # noqa: BLE001
        print(f"bus_watchdog error: {exc}", file=sys.stderr)
        return 1
    if not rep["silent"] and not rep["missing_downstream"]:
        return 0
    if args.json:
        print(json.dumps({k: rep[k] for k in ("now", "silent", "missing_downstream")}, indent=2))
        return 2
    t = chase.parse_ts(rep["now"]).strftime("%a %m/%d %H:%M PT")
    print(f"BUS WATCHDOG {t}: {len(rep['silent'])} silent, {len(rep['missing_downstream'])} unpublished")
    for r in rep["silent"]:
        last = "never" if not r["last_event"] else buscov._fmt_last(r)
        why = f" (usually: {r['quiet_ok']})" if r.get("quiet_ok") else ""
        print(f"- {r['agent']}: {"never published" if r["age_days"] is None else "silent %sd" % r["age_days"]} (limit {r["threshold_days"]}d), last {last}{why}")
    for m in rep["missing_downstream"]:
        print(f"- {m['owner']}: {m['pass_count']} Critic PASS ({m['rule']}, oldest {m['oldest_pass_key']}) with no {'/'.join(m['expected_topics'])} after {m['sla_hours']}h")
    print("Fix: publish the real event (publish.py, note it as a backfill) or set coverage.agents.<name>.snooze_until / exempt in the catalog.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
