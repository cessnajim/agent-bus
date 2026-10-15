#!/usr/bin/env python3
"""2026-10-15 onboarding: six new agents, their topics and subscriptions."""
from __future__ import annotations
import json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import chase  # noqa: E402
import publish  # noqa: E402

CAT = json.loads((ROOT / "catalog" / "topics.json").read_text())
T = CAT["topics"]
NEW = {
    "Travel": "34ff498e-f62a-48ec-bda8-8b8ea4a62ae4",
    "Stills & Clips": "ca62e4bf-ae28-4748-a4b6-5c016f0a83cd",
    "Social Media": "0b44ceee-7329-4bd0-9c7f-2288abdd0782",
    "SEO Auditor": "624476b9-0878-48cd-ae81-00a0d6e08e00",
    "Subscription Cleaner": "b5b207f9-7281-423f-a743-3aba78cb1176",
    "Expense Manager": "c8c41a30-3468-484b-8e16-ed184224e00d",
}


class NewAgents(unittest.TestCase):
    def test_version(self):
        self.assertGreaterEqual(CAT["version"], 5)

    def test_registry(self):
        self.assertEqual(CAT["agents"], NEW)

    def test_topics_and_publishers(self):
        for topic, pub in [("travel.*", "Travel"), ("assets.ready", "Stills & Clips"),
                           ("social.post", "Social Media"), ("seo.audit", "SEO Auditor"),
                           ("subs.review", "Subscription Cleaner"), ("expense.logged", "Expense Manager")]:
            self.assertIn(pub, T[topic]["publishers"], topic)

    def test_assets_ready_subscribers(self):
        for s in ("Marketing", "Adobe Stock"):
            self.assertIn(s, T["assets.ready"]["subscribers"])
        self.assertEqual(chase.resolve_must_act(T["assets.ready"], {}, "Stills & Clips"), ["Marketing"])

    def test_lane_subscriptions(self):
        for t in ("critic.pass.outbound", "adobe.batch_submitted"):
            self.assertIn("Social Media", T[t]["subscribers"])
            self.assertIn("SEO Auditor", T[t]["subscribers"])
        for a in ("Expense Manager", "Subscription Cleaner"):
            self.assertIn(a, T["catalant.pitch_submitted"]["subscribers"])

    def test_wildcard_resolve(self):
        self.assertIs(publish.resolve_topic(T, "travel.route"), T["travel.*"])
        self.assertIsNone(publish.resolve_topic(T, "travel.*"))
        self.assertIsNone(publish.resolve_topic(T, "nope.x"))
        self.assertIs(publish.resolve_topic(T, "fte.submit"), T["fte.submit"])

    def test_bus_test_never_dms(self):
        for a in NEW:
            self.assertEqual(chase.resolve_must_act(T["bus.test"], {}, a), [])


class Publish(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-bus-test-"))
        (self.tmp / "catalog").mkdir()
        shutil.copy(ROOT / "catalog" / "topics.json", self.tmp / "catalog" / "topics.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_travel_wildcard_publish(self):
        env = dict(os.environ, AGENT_BUS_ROOT=str(self.tmp), AGENT_BUS_SKIP_HOOKS="1")
        out = subprocess.run([sys.executable, str(ROOT / "bin" / "publish.py"), "--topic", "travel.route",
                              "--actor", "Travel", "--key", "t-1", "--ref", "trip=moscow-longmont"],
                             env=env, capture_output=True, text=True, check=True)
        r = json.loads(out.stdout)
        self.assertEqual(r["status"], "published")
        self.assertEqual(r["event"]["topic"], "travel.route")


if __name__ == "__main__":
    unittest.main()
