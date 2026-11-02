#!/usr/bin/env python3
"""Roster v8 (Jim 2026-11-01): job policy + holds belong to Job Search, so Job Search
may publish fte.hold_cleared and jim.answer. Admin (and Grok Bot on jim.answer) stay."""
from __future__ import annotations
import json, re, sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import buscov  # noqa: E402
import chase  # noqa: E402

CAT = json.loads((ROOT / "catalog" / "topics.json").read_text())
T = CAT["topics"]


class Publishers(unittest.TestCase):
    def test_hold_cleared(self):
        pubs = T["fte.hold_cleared"]["publishers"]
        self.assertIn("Job Search", pubs)
        self.assertIn("Admin", pubs)
        self.assertEqual(len(pubs), len(set(pubs)))

    def test_jim_answer(self):
        pubs = T["jim.answer"]["publishers"]
        for p in ("Job Search", "Admin", "Grok Bot"):
            self.assertIn(p, pubs)
        self.assertEqual(len(pubs), len(set(pubs)))

    def test_old_fte_apply_names_resolve_to_publisher(self):
        for raw in ("FTE Apply", "fte-apply", "fte apply", "Job Search"):
            actor = buscov.canonical_actor(raw, CAT)
            self.assertEqual(actor, "Job Search", raw)
            self.assertIn(actor, T["fte.hold_cleared"]["publishers"])
            self.assertIn(actor, T["jim.answer"]["publishers"])

    def test_job_search_in_coverage(self):
        agents = set(CAT["coverage"]["agents"]) | set(CAT["coverage"]["system_actors"])
        self.assertIn("Job Search", agents)

    def test_hold_cleared_still_wakes_job_search_not_self(self):
        m = T["fte.hold_cleared"]
        self.assertEqual(chase.resolve_must_act(m, {}, "Admin"), ["Job Search"])
        # Job Search publishing its own hold clear does not DM itself
        self.assertEqual(chase.resolve_must_act(m, {}, "Job Search"), [])

    def test_todays_job_search_events_now_valid(self):
        day = ROOT / "events" / "2026-11-02.jsonl"
        if not day.exists():
            self.skipTest("no 2026-11-02 event file")
        pat = {"fte.hold_cleared": re.compile(r".*-hold-cleared-2026-11-02$"),
               "jim.answer": re.compile(r"^jim-answer-.*-2026-11-02$")}
        seen = 0
        for e in chase.read_jsonl(day):
            t, k = e.get("topic"), e.get("idempotency_key", "")
            if t in pat and pat[t].match(k):
                actor = buscov.canonical_actor(e.get("actor"), CAT)
                if actor != "Job Search":
                    continue
                seen += 1
                self.assertIn(actor, T[t]["publishers"], k)
                for r in T[t]["required_refs"]:
                    self.assertIn(r, e.get("refs") or {}, k)
        self.assertGreater(seen, 0)


if __name__ == "__main__":
    unittest.main()
