import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import chase

CAT = {"agents": {"Grok Bot": "x", "Admin": "y"},
       "coverage": {"actor_aliases": {"larry": "Grok Bot"}}}
M = {"must_act": ["refs.requesting_agent"]}

class T(unittest.TestCase):
    def test_case_insensitive_alias(self):
        for v in ("Larry", "larry", "LARRY"):
            self.assertEqual(chase.resolve_must_act(M, {"requesting_agent": v}, "Outcome Critic", CAT), ["Grok Bot"])
    def test_actor_dropped_via_alias(self):
        self.assertEqual(chase.resolve_must_act(M, {"requesting_agent": "Larry"}, "Grok Bot", CAT), [])
    def test_unknown_passthrough(self):
        self.assertEqual(chase.resolve_must_act(M, {"requesting_agent": "Someone"}, None, CAT), ["Someone"])
    def test_live_catalog_has_grant_outcome(self):
        cat = chase._load_catalog_for_aliases()
        self.assertIn("grant.outcome", cat["topics"])
        self.assertEqual(chase.canonical_name("Larry", cat), "Grok Bot")

if __name__ == "__main__":
    unittest.main()
