#!/usr/bin/env python3
"""household.trade_recorded (2026-11-02, Books asked; Jim via Larry): personal securities trades / tax events.
Household capital gains only -- never income.earned/income.paid, never Unemployment weekly gross."""
from __future__ import annotations
import json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import chase  # noqa: E402
import publish  # noqa: E402

CAT = json.loads((ROOT / "catalog" / "topics.json").read_text())
T = CAT["topics"]
TOPIC = "household.trade_recorded"
REQUIRED = ["symbol", "shares", "trade_date", "proceeds_gross", "gain", "term", "ledger_path"]
REFS = ["symbol=BE", "shares=75", "trade_date=2026-11-02", "proceeds_gross=15071.63", "gain=4118.88",
        "term=short", "ledger_path=/workspace/expenses/household_securities_2026.csv"]


class Catalog(unittest.TestCase):
    def test_present_and_resolves(self):
        self.assertIn(TOPIC, T)
        self.assertIs(publish.resolve_topic(T, TOPIC), T[TOPIC])

    def test_routing(self):
        m = T[TOPIC]
        self.assertEqual(m["publishers"], ["Books"])
        self.assertEqual(sorted(m["subscribers"]), ["Admin", "Grok Bot"])
        self.assertEqual(m["must_act"], [])
        self.assertEqual(m["required_refs"], REQUIRED)
        self.assertEqual(m["optional_refs"], ["lots", "fees", "account"])
        for a in CAT["agents"]:
            self.assertEqual(chase.resolve_must_act(m, {}, a), [], a)

    def test_markets_and_unemployment_off(self):
        m = T[TOPIC]
        for k in ("publishers", "subscribers", "must_act"):
            self.assertNotIn("Markets", m[k])
            self.assertNotIn("Unemployment", m[k])

    def test_not_income(self):
        d = T[TOPIC]["description"]
        self.assertIn("NOT self-employment", d)
        self.assertIn("income.earned", d)
        self.assertIn("Unemployment", d)
        # income topics stay separate: no trade refs bleed into them
        for t in ("income.earned", "income.paid"):
            self.assertNotIn("proceeds_gross", T[t]["required_refs"])


class Publish(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-bus-test-"))
        (self.tmp / "catalog").mkdir()
        shutil.copy(ROOT / "catalog" / "topics.json", self.tmp / "catalog" / "topics.json")
        self.env = dict(os.environ, AGENT_BUS_ROOT=str(self.tmp), AGENT_BUS_SKIP_HOOKS="1")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_pub(self, refs):
        args = [sys.executable, str(ROOT / "bin" / "publish.py"), "--topic", TOPIC, "--actor", "Books",
                "--key", "trade-BE-2026-11-02"]
        for r in refs:
            args += ["--ref", r]
        return subprocess.run(args, env=self.env, capture_output=True, text=True)

    def test_publish_ok_dms_no_one(self):
        out = self.run_pub(REFS + ["fees=0", "account=brokerage"])
        self.assertEqual(out.returncode, 0, out.stderr)
        r = json.loads(out.stdout)
        self.assertEqual(r["status"], "published")
        self.assertEqual(r["event"]["actor"], "Books")
        self.assertEqual(r["event"]["refs"]["term"], "short")
        self.assertEqual(r["must_act"], [])
        self.assertEqual(sorted(r["pull_only"]), ["Admin", "Grok Bot"])

    def test_missing_required_ref_fails(self):
        out = self.run_pub([x for x in REFS if not x.startswith("term=")])
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("term", out.stderr)


if __name__ == "__main__":
    unittest.main()
