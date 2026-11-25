#!/usr/bin/env python3
"""Claude Code seat (Jim 2026-11-25: 'Own seat'). Claude Code sessions publish and pull the two
site topics under their own name. The seat is not a Cursor agent: it has no agent id to DM, so it
is pull-only and must never appear in must_act."""
from __future__ import annotations
import json, sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import buscov  # noqa: E402

CAT = json.loads((ROOT / "catalog" / "topics.json").read_text())
T = CAT["topics"]
NAME = "Claude Code"


class ClaudeCodeSeat(unittest.TestCase):
    def test_registered(self):
        self.assertIn(NAME, CAT["agents"])
        self.assertIn(NAME, CAT["coverage"]["agents"])

    def test_publishes_and_pulls_site_topics(self):
        for topic in ("site.deployed", "site.pr_opened"):
            self.assertIn(NAME, T[topic]["publishers"], topic)
            self.assertIn(NAME, T[topic]["subscribers"], topic)

    def test_publishes_nothing_else(self):
        allowed = {"site.deployed", "site.pr_opened"}
        for topic, meta in T.items():
            if topic not in allowed:
                self.assertNotIn(NAME, meta.get("publishers", []), topic)

    def test_never_must_act(self):
        for topic, meta in T.items():
            self.assertNotIn(NAME, meta.get("must_act", []), topic)
            for rule in meta.get("must_act_rules", []) or []:
                self.assertNotIn(NAME, json.dumps(rule), topic)

    def test_on_request_so_never_flagged_silent(self):
        self.assertTrue(CAT["coverage"]["agents"][NAME].get("on_request"))

    def test_aliases_resolve(self):
        for alias in ("claude", "Claude code", "claude-code"):
            self.assertEqual(buscov.canonical_actor(alias, CAT), NAME, alias)

    def test_north_idaho_labs_seat_unchanged(self):
        for topic in ("site.deployed", "site.pr_opened"):
            self.assertIn("North Idaho Labs", T[topic]["publishers"], topic)


if __name__ == "__main__":
    unittest.main()
