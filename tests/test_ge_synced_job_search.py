#!/usr/bin/env python3
"""2026-11-02 (roster v8): Job Search owns the weekday GE drift check, so it may publish
ge.synced. Admin and agent-bus-hook stay; subscribers/must_act/routing unchanged."""
from __future__ import annotations
import json, sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import buscov  # noqa: E402
import chase  # noqa: E402

CAT = json.loads((ROOT / "catalog" / "topics.json").read_text())
M = CAT["topics"]["ge.synced"]


class GeSyncedJobSearch(unittest.TestCase):
    def test_publishers(self):
        self.assertEqual(M["publishers"], ["Admin", "agent-bus-hook", "Job Search"])

    def test_routing_unchanged(self):
        self.assertEqual(M["subscribers"], ["Admin"])
        self.assertEqual(M["must_act"], [])
        self.assertNotIn("must_act_rules", M)
        self.assertEqual(M["required_refs"], ["submitted_on_count"])
        self.assertEqual(M["state_file"], "ge")
        for actor in ("Job Search", "Admin", "agent-bus-hook"):
            self.assertEqual(chase.resolve_must_act(M, {"submitted_on_count": 1}, actor), [], actor)
            self.assertEqual(chase.pull_only(M, []), ["Admin"])

    def test_old_names_resolve(self):
        for raw in ("FTE Apply", "fte-apply", "Job Search"):
            self.assertIn(buscov.canonical_actor(raw, CAT), M["publishers"], raw)

    def test_drift_check_event_valid(self):
        day = ROOT / "events" / "2026-11-02.jsonl"
        if not day.exists():
            self.skipTest("no 2026-11-02 event file")
        hits = [e for e in chase.read_jsonl(day)
                if e.get("topic") == "ge.synced" and e.get("idempotency_key") == "ge-drift-check-2026-11-02"]
        self.assertEqual(len(hits), 1)
        self.assertIn(buscov.canonical_actor(hits[0]["actor"], CAT), M["publishers"])
        self.assertIn("submitted_on_count", hits[0]["refs"])


if __name__ == "__main__":
    unittest.main()
