#!/usr/bin/env python3
"""2026-11-02: Writing Critic PASSes on Latah Dispatch (news.latah.northidaholabs.com) site copy
fell through to the Admin default (lanes seo / content_studio were unmapped). Under roster v8,
North Idaho Labs owns the NIL sites (draft-only, Jim merges); Content Studio proposes the copy
(weekly Latah check + SEO) and pulls as a subscriber."""
from __future__ import annotations
import json, sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import chase  # noqa: E402

CAT = json.loads((ROOT / "catalog" / "topics.json").read_text())
M = CAT["topics"]["critic.pass.outbound"]
WC = "Writing Critic"


class LatahRouting(unittest.TestCase):
    def test_todays_events_route_to_nil(self):
        # exact refs from latah-dispatch-copy-2026-11-02-pass and latah-archive-line-2026-11-02
        for refs in ({"lane": "seo", "asset": "latah-dispatch-fire-roads-home-copy",
                      "packet_path": "/workspace/seo-audits/latah-weekly/content-proposal-2026-11-02-critic-packet.md"},
                     {"lane": "content_studio", "asset": "latah-dispatch-archive-intro", "packet_path": "/archive/"}):
            self.assertEqual(chase.resolve_must_act(M, refs, WC), ["North Idaho Labs"], refs)

    def test_logged_events_route_to_nil(self):
        day = ROOT / "events" / "2026-11-02.jsonl"
        if not day.exists():
            self.skipTest("no 2026-11-02 event file")
        keys = {"latah-dispatch-copy-2026-11-02-pass", "latah-archive-line-2026-11-02"}
        found = {}
        for e in chase.read_jsonl(day):
            if e.get("topic") == "critic.pass.outbound" and e.get("idempotency_key") in keys:
                found[e["idempotency_key"]] = chase.resolve_must_act(M, e.get("refs"), e.get("actor"))
        self.assertEqual(set(found), keys)
        for k, ma in found.items():
            self.assertEqual(ma, ["North Idaho Labs"], k)
            self.assertNotIn("Admin", ma, k)

    def test_nil_lanes(self):
        for lane in ("nil", "latah_dispatch", "latah", "news_latah", "NIL"):
            self.assertEqual(chase.resolve_must_act(M, {"lane": lane, "asset": "x"}, WC), ["North Idaho Labs"], lane)

    def test_latah_youtube_short_still_content_studio(self):
        # OAWJ Shorts filmed in Latah are not site copy
        refs = {"lane": "youtube", "asset": "latah-drone-climb-aug9-1431"}
        self.assertEqual(chase.resolve_must_act(M, refs, WC), ["Content Studio"])

    def test_existing_lanes_unchanged(self):
        for lane, owner in [("youtube", "Content Studio"), ("oawj_shop", "Content Studio"), ("adobe_stock", "Content Studio"),
                            ("oawj_licensing", "OAWJ Shop"), ("outreach", "OAWJ Shop"),
                            ("marketplace", "Consulting"), ("catalant", "Consulting")]:
            self.assertEqual(chase.resolve_must_act(M, {"lane": lane, "asset": "a"}, WC), [owner], lane)
        self.assertEqual(chase.resolve_must_act(M, {"lane": "personal_volunteer", "asset": "a"}, WC), ["Admin"])
        # unknown lane, non-Latah asset still lands on Admin to route
        self.assertEqual(chase.resolve_must_act(M, {"lane": "seo", "asset": "oawj-shop-meta"}, WC), ["Admin"])

    def test_subscribers_cover_owner_and_proposer(self):
        for a in ("North Idaho Labs", "Content Studio"):
            self.assertIn(a, M["subscribers"])
        self.assertEqual(chase.pull_only(M, ["North Idaho Labs"]).count("Content Studio"), 1)

    def test_prefix_match(self):
        self.assertTrue(chase._ref_matches("Latah-Dispatch-X", "latah-dispatch*"))
        self.assertFalse(chase._ref_matches("latah-drone", "latah-dispatch*"))
        self.assertFalse(chase._ref_matches(None, "latah-dispatch*"))
        self.assertTrue(chase._ref_matches("seo", ["nil", "SEO"]))
        self.assertFalse(chase._ref_matches("seox", "seo"))


if __name__ == "__main__":
    unittest.main()
