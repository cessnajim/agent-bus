#!/usr/bin/env python3
"""Agent registry, topics and subscriptions. 2026-10-15 onboarding, re-scoped by roster v8 (2026-11-01,
Jim: 'i approve all 8 suggestions'): Travel -> Travel & Personal; Stills & Clips + SEO Auditor + Marketing -> Content Studio;
Subscription Cleaner + Expense Manager -> Books; NIL + NatureNet added."""
from __future__ import annotations
import json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import buscov  # noqa: E402
import chase  # noqa: E402
import publish  # noqa: E402

CAT = json.loads((ROOT / "catalog" / "topics.json").read_text())
T = CAT["topics"]
IDS = {
    "Admin": "fa50d9b1-127a-404a-81d2-7bf61b49e1fe",
    "Job Search": "a8926586-fbf3-4d6c-8f0a-790e65ead1bd",
    "Consulting": "dd0ab463-c048-4751-87c7-68b6f041d6d9",
    "Content Studio": "5d55b9e2-f9a6-45b0-8371-5254e17eb025",
    "OAWJ Shop": "9c5fcb12-9a8e-42f3-b619-c7ccb804e3fd",
    "Books": "c8c41a30-3468-484b-8e16-ed184224e00d",
    "Travel & Personal": "34ff498e-f62a-48ec-bda8-8b8ea4a62ae4",
    "Social Media": "0b44ceee-7329-4bd0-9c7f-2288abdd0782",
    "North Idaho Labs": "cd904b26-f530-418d-bffc-39c46a0451e5",
    "NatureNet": "cea6bba3-83ef-460d-98b4-b81f565c2026",
}
OLD = ["FTE Apply", "Investment", "Marketing", "Sales", "Expense Manager", "Subscription Cleaner",
       "Stills & Clips", "SEO Auditor", "Disk Saver", "Travel"]


class NewAgents(unittest.TestCase):
    def test_version(self):
        self.assertGreaterEqual(CAT["version"], 8)

    def test_registry(self):
        for name, aid in IDS.items():
            self.assertEqual(CAT["agents"].get(name), aid, name)
        for o in OLD:
            self.assertNotIn(o, CAT["agents"])

    def test_old_names_resolve(self):
        for o in OLD:
            self.assertNotEqual(buscov.canonical_actor(o, CAT), o, o)
        self.assertEqual(buscov.canonical_actor("SEO Auditor", CAT), "Content Studio")
        self.assertEqual(buscov.canonical_actor("subscription cleaner", CAT), "Books")
        self.assertEqual(buscov.canonical_actor("Travel", CAT), "Travel & Personal")

    def test_no_old_names_in_routing(self):
        for name, meta in T.items():
            for k in ("publishers", "subscribers", "must_act"):
                for o in OLD:
                    self.assertNotIn(o, meta.get(k, []), f"{name}.{k}")

    def test_topics_and_publishers(self):
        for topic, pub in [("travel.*", "Travel & Personal"), ("assets.ready", "Content Studio"),
                           ("social.post", "Social Media"), ("seo.audit", "Content Studio"),
                           ("subs.review", "Books"), ("expense.logged", "Books"),
                           ("income.earned", "Consulting"), ("grant.submitted", "NatureNet")]:
            self.assertIn(pub, T[topic]["publishers"], topic)

    def test_new_topics_present(self):
        for t in ("fte.response", "fte.harvest", "fte.filter_change", "critic.revise", "income.earned", "income.paid",
                  "jim.ask", "jim.answer", "learning.proposal", "learning.applied", "shop.reply",
                  "marketplace.outcome", "social.metrics", "grant.submitted", "site.deployed"):
            self.assertIn(t, T)

    def test_assets_ready_subscribers(self):
        for s in ("Content Studio", "Adobe Stock"):
            self.assertIn(s, T["assets.ready"]["subscribers"])
        # self-must_act is dropped: Content Studio publishing assets.ready DMs no one
        self.assertEqual(chase.resolve_must_act(T["assets.ready"], {}, "Content Studio"), [])

    def test_lane_subscriptions(self):
        for t in ("critic.pass.outbound", "adobe.batch_submitted"):
            self.assertIn("Social Media", T[t]["subscribers"])
            self.assertIn("Content Studio", T[t]["subscribers"])
        self.assertIn("Books", T["catalant.pitch_submitted"]["subscribers"])
        for a in ("North Idaho Labs", "NatureNet"):
            self.assertIn(a, T["critic.pass.outbound"]["subscribers"])

    def test_wildcard_resolve(self):
        self.assertIs(publish.resolve_topic(T, "travel.route"), T["travel.*"])
        self.assertIsNone(publish.resolve_topic(T, "travel.*"))
        self.assertIsNone(publish.resolve_topic(T, "nope.x"))
        self.assertIs(publish.resolve_topic(T, "fte.submit"), T["fte.submit"])

    def test_bus_test_never_dms(self):
        for a in IDS:
            self.assertEqual(chase.resolve_must_act(T["bus.test"], {}, a), [])


class Publish(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-bus-test-"))
        (self.tmp / "catalog").mkdir()
        shutil.copy(ROOT / "catalog" / "topics.json", self.tmp / "catalog" / "topics.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_travel_wildcard_publish_old_name(self):
        env = dict(os.environ, AGENT_BUS_ROOT=str(self.tmp), AGENT_BUS_SKIP_HOOKS="1")
        out = subprocess.run([sys.executable, str(ROOT / "bin" / "publish.py"), "--topic", "travel.route",
                              "--actor", "Travel", "--key", "t-1", "--ref", "trip=moscow-longmont"],
                             env=env, capture_output=True, text=True, check=True)
        r = json.loads(out.stdout)
        self.assertEqual(r["status"], "published")
        self.assertEqual(r["event"]["topic"], "travel.route")
        self.assertEqual(r["event"]["actor"], "Travel & Personal")


if __name__ == "__main__":
    unittest.main()
