#!/usr/bin/env python3
"""2026-10-23 bus robustness: coverage report, watchdog, youtube.published, lane routing."""
from __future__ import annotations
import json, os, shutil, subprocess, sys, tempfile, unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import chase  # noqa: E402
import buscov  # noqa: E402

CAT = json.loads((ROOT / "catalog" / "topics.json").read_text())
NOW = datetime(2026, 10, 23, 20, 0, tzinfo=chase.BUS_TZ)


def ev(topic, ts, actor, key="k", **refs):
    return {"topic": topic, "ts": ts, "idempotency_key": key, "actor": actor, "refs": refs, "state_delta": {}}


class Catalog(unittest.TestCase):
    def test_v7_youtube_topic(self):
        self.assertGreaterEqual(CAT["version"], 7)
        t = CAT["topics"]["youtube.published"]
        self.assertEqual(CAT["owner"], "Admin")
        self.assertIn("Content Studio", t["publishers"])
        self.assertGreaterEqual(len(t["subscribers"]), 2)
        self.assertTrue(t["done_means"])
        self.assertEqual(chase.resolve_must_act(t, {}, "Content Studio"), ["Social Media"])

    def test_critic_lanes_route_to_owner(self):
        meta = CAT["topics"]["critic.pass.outbound"]
        for lane, owner in [("oawj_youtube", "Content Studio"), ("adobe_stock", "Content Studio"),
                            ("oawj_licensing", "OAWJ Shop"), ("oawj_sales", "OAWJ Shop"),
                            ("investment", "Consulting"), ("marketplace", "Consulting")]:
            self.assertEqual(chase.resolve_must_act(meta, {"lane": lane}, "Writing Critic"), [owner], lane)
        self.assertEqual(chase.resolve_must_act(meta, {"lane": "personal_volunteer"}, "Writing Critic"), ["Admin"])

    def test_coverage_covers_every_catalog_publisher(self):
        agents = set(CAT["coverage"]["agents"]) | set(CAT["coverage"]["system_actors"])
        for topic, meta in CAT["topics"].items():
            for p in meta["publishers"]:
                self.assertIn(p, agents, f"{topic} publisher {p} missing from coverage block")


class Canonical(unittest.TestCase):
    def test_actor_normalized(self):
        for raw, want in [("fte-apply", "Job Search"), ("Job Search", "Job Search"), ("investment", "Consulting"),
                          ("stills & clips", "Content Studio"), ("agent-bus-hook", "agent-bus-hook")]:
            self.assertEqual(buscov.canonical_actor(raw, CAT), want)
        self.assertEqual(buscov.canonical_actor("Brand New", CAT), "Brand New")


class Report(unittest.TestCase):
    def rows(self, events, **kw):
        return {r["agent"]: r for r in buscov.agent_rows(CAT, events, NOW, 3)}

    def test_silent_and_ok(self):
        events = [ev("shop.order_received", "2026-10-15T10:00:00-07:00", "Sales"),
                  ev("assets.ready", "2026-10-23T10:00:00-07:00", "stills & clips")]
        r = self.rows(events)
        self.assertEqual(r["OAWJ Shop"]["status"], "SILENT")      # 8d > 4d, old name "Sales" aliased
        self.assertEqual(r["Content Studio"]["status"], "OK")     # v8 merge alias normalized
        self.assertEqual(r["Ops"]["status"], "EXEMPT")            # retiring after 2026-11-08
        for a in ("Markets", "moms health", "ex wife watch"):
            self.assertEqual(r[a]["status"], "EXEMPT")
        for a in ("Travel & Personal", "North Idaho Labs", "NatureNet"):
            self.assertEqual(r[a]["status"], "ON_REQUEST")       # never flagged silent

    def test_bus_test_ignored(self):
        events = [ev("bus.test", "2026-10-23T10:00:00-07:00", "Travel & Personal", test=True)]
        self.assertEqual(self.rows(events)["Travel & Personal"]["events_total"], 0)

    def test_weekdays_only_skips_weekend(self):
        # Fri 10/23 vs Mon 10/26 = 1 business day
        events = [ev("fte.submit", "2026-10-23T17:00:00-07:00", "fte-apply")]
        mon = datetime(2026, 10, 26, 9, 0, tzinfo=chase.BUS_TZ)
        r = {x["agent"]: x for x in buscov.agent_rows(CAT, events, mon, 3)}
        self.assertEqual(r["Job Search"]["age_days"], 1)
        self.assertEqual(r["Job Search"]["status"], "OK")

    def test_snooze(self):
        cat = json.loads(json.dumps(CAT))
        cat["coverage"]["agents"]["Content Studio"]["snooze_until"] = "2026-10-30"
        r = {x["agent"]: x for x in buscov.agent_rows(cat, [], NOW, 3)}
        self.assertEqual(r["Content Studio"]["status"], "SNOOZED")

    def test_missing_downstream_then_cleared(self):
        pas = ev("critic.pass.outbound", "2026-10-20T10:26:00-07:00", "Writing Critic", "oawj-short-x", lane="oawj_youtube")
        out = buscov.missing_downstream(CAT, [pas], NOW, 7)
        self.assertEqual([m["rule"] for m in out], ["youtube-publish"])
        pub = ev("youtube.published", "2026-10-23T16:25:00-07:00", "Content Studio", "yt1", video_id="x")
        self.assertEqual(buscov.missing_downstream(CAT, [pas, pub], NOW, 7), [])

    def test_within_sla_not_flagged(self):
        pas = ev("critic.pass.outbound", "2026-10-23T09:00:00-07:00", "Writing Critic", "k", lane="oawj_youtube")
        self.assertEqual(buscov.missing_downstream(CAT, [pas], NOW, 7), [])

    def test_untracked_lane_ignored(self):
        pas = ev("critic.pass.outbound", "2026-10-15T09:00:00-07:00", "Writing Critic", "k", lane="personal_volunteer")
        self.assertEqual(buscov.missing_downstream(CAT, [pas], NOW, 7), [])


class WatchdogCli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "catalog").mkdir(); (self.tmp / "events").mkdir(); (self.tmp / "bin").mkdir()
        shutil.copy(ROOT / "catalog" / "topics.json", self.tmp / "catalog")
        for f in ("chase.py", "buscov.py", "bus_watchdog.py"):
            shutil.copy(ROOT / "bin" / f, self.tmp / "bin")
        self.env = dict(os.environ, AGENT_BUS_ROOT=str(self.tmp))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_wd(self, now):
        return subprocess.run([sys.executable, str(self.tmp / "bin" / "bus_watchdog.py"), "--now", now],
                              capture_output=True, text=True, env=self.env)

    def write(self, rows):
        with (self.tmp / "events" / "2026-10-23.jsonl").open("w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def test_healthy_is_silent_exit0(self):
        names = [a for a, c in CAT["coverage"]["agents"].items() if not c.get("exempt")]
        self.write([ev("critic.pass", "2026-10-23T10:00:00-07:00", n, f"k{i}", test=False) for i, n in enumerate(names)])
        p = self.run_wd("2026-10-23T20:00:00-07:00")
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))

    def test_silent_exit2_with_summary(self):
        self.write([])
        p = self.run_wd("2026-10-23T20:00:00-07:00")
        self.assertEqual(p.returncode, 2)
        self.assertIn("BUS WATCHDOG", p.stdout)
        self.assertIn("Content Studio", p.stdout)
        self.assertLess(len(p.stdout.splitlines()), 40)


if __name__ == "__main__":
    unittest.main()
