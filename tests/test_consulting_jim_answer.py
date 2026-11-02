#!/usr/bin/env python3
"""2026-11-02: Consulting (alias Investment) may publish jim.answer for the asks it owns
(e.g. catalant-support-preferred-email-2026-10-30-sent, 08:14 AM PT)."""
from __future__ import annotations
import json, sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import buscov  # noqa: E402
import chase  # noqa: E402

CAT = json.loads((ROOT / "catalog" / "topics.json").read_text())
M = CAT["topics"]["jim.answer"]


class ConsultingJimAnswer(unittest.TestCase):
    def test_publishers(self):
        for p in ("Consulting", "Job Search", "Admin", "Grok Bot"):
            self.assertIn(p, M["publishers"])
        self.assertEqual(len(M["publishers"]), len(set(M["publishers"])))

    def test_investment_alias(self):
        for raw in ("Investment", "investment", "Consulting"):
            self.assertIn(buscov.canonical_actor(raw, CAT), M["publishers"], raw)

    def test_owner_routing_lowercase_owner(self):
        # event used owner=consulting; Consulting publishing its own answer does not wake itself
        self.assertEqual(chase.resolve_must_act(M, {"owner": "Job Search"}, "Consulting"), ["Job Search"])

    def test_logged_event_valid(self):
        day = ROOT / "events" / "2026-11-02.jsonl"
        if not day.exists():
            self.skipTest("no 2026-11-02 event file")
        hits = [e for e in chase.read_jsonl(day)
                if e.get("topic") == "jim.answer"
                and e.get("idempotency_key") == "catalant-support-preferred-email-2026-10-30-sent"]
        self.assertEqual(len(hits), 1)
        e = hits[0]
        self.assertIn(buscov.canonical_actor(e["actor"], CAT), M["publishers"])
        for r in M["required_refs"]:
            self.assertIn(r, e["refs"])


if __name__ == "__main__":
    unittest.main()
