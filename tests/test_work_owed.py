#!/usr/bin/env python3
"""work.owed / work.done (Jim 2026-11-25: 'Now add the owed topic to the bus').

Cross-platform owed work: any coding seat publishes what it took on or handed off, with a done
GitHub can show. The owner comes from the event, so the stall check names whoever owes it.
Claude Code has no agent id, so work owed by Claude Code is never a DM."""
from __future__ import annotations
import json, os, shutil, subprocess, sys, tempfile, unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import chase  # noqa: E402

CAT = json.loads((ROOT / "catalog" / "topics.json").read_text())
T = CAT["topics"]
SEATS = ["Admin", "Grok Bot", "North Idaho Labs", "NatureNet", "Content Studio", "OAWJ Shop", "Claude Code"]
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=chase.BUS_TZ)


def ev(topic, hours_ago, **refs):
    ts = (NOW - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    return {"topic": topic, "ts": ts, "idempotency_key": f"{topic}-{refs.get('work_id')}-{hours_ago}",
            "actor": "Grok Bot", "refs": refs, "state_delta": {}}


def owed(hours_ago, work_id="w1", owner="North Idaho Labs", **more):
    return ev("work.owed", hours_ago, work_id=work_id, owner=owner, repo="o/r", done="pr_merged", **more)


def stalls(events):
    report = chase.detect_stalls(CAT, events, {"ws": {}, "parks": {}, "owed": {}}, NOW)
    return [s for s in report["stalls"] if s["source_topic"] == "work.owed"]


class Catalog(unittest.TestCase):
    def test_both_topics_open_to_every_coding_seat(self):
        for topic in ("work.owed", "work.done"):
            self.assertEqual(T[topic]["publishers"], SEATS, topic)
            self.assertEqual(T[topic]["subscribers"], SEATS, topic)
            for seat in SEATS:
                self.assertIn(seat, CAT["agents"], seat)

    def test_required_refs(self):
        self.assertEqual(T["work.owed"]["required_refs"], ["work_id", "owner", "repo", "done"])
        self.assertEqual(T["work.done"]["required_refs"], ["work_id", "owner", "outcome"])

    def test_an_item_can_carry_a_brief(self):
        self.assertIn("brief", T["work.owed"]["optional_refs"])
        self.assertIn("The note is the brief", T["work.owed"]["done_means"])

    def test_done_is_something_github_can_show(self):
        text = T["work.owed"]["done_means"]
        for kind in ("pr_merged", "file_committed"):
            self.assertIn(kind, text)
        self.assertIn("saying so does not close it", text)


class WhoIsTold(unittest.TestCase):
    def told(self, owner, actor="Grok Bot"):
        return chase.resolve_must_act(T["work.owed"], {"owner": owner}, actor, CAT)

    def test_the_owner_is_told(self):
        self.assertEqual(self.told("North Idaho Labs"), ["North Idaho Labs"])
        self.assertEqual(self.told("larry", actor="Claude Code"), ["Grok Bot"])

    def test_claude_code_is_never_a_dm(self):
        for name in ("Claude Code", "claude", "claude-code", "Claude code"):
            self.assertEqual(self.told(name), [], name)

    def test_an_agent_is_not_told_about_work_it_gave_itself(self):
        self.assertEqual(self.told("Grok Bot", actor="Grok Bot"), [])

    def test_work_done_tells_nobody(self):
        self.assertEqual(chase.resolve_must_act(T["work.done"], {"owner": "NatureNet"}, "NatureNet", CAT), [])


class Chase(unittest.TestCase):
    def test_owed_past_a_day_stalls_under_its_owner(self):
        got = stalls([owed(30)])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["owner"], "North Idaho Labs")
        self.assertEqual(got[0]["owed_topic"], "work.done")
        self.assertEqual(got[0]["match"], {"work_id": "w1"})
        self.assertTrue(got[0]["overdue"])

    def test_owner_alias_resolves(self):
        self.assertEqual(stalls([owed(30, owner="claude")])[0]["owner"], "Claude Code")

    def test_fresh_work_is_not_overdue(self):
        got = stalls([owed(3)])
        self.assertEqual([s["overdue"] for s in got], [False])
        self.assertFalse(got[0]["nudge_due"])

    def test_work_done_clears_only_its_own_item(self):
        got = stalls([owed(30, "w1"), owed(30, "w2"),
                      ev("work.done", 1, work_id="w1", owner="North Idaho Labs", outcome="done")])
        self.assertEqual([s["match"]["work_id"] for s in got], ["w2"])

    def test_dropped_also_clears(self):
        self.assertEqual(stalls([owed(30), ev("work.done", 1, work_id="w1", owner="North Idaho Labs", outcome="dropped")]), [])

    def test_republishing_moves_the_item_to_its_new_owner(self):
        got = stalls([owed(40), owed(30, owner="NatureNet")])
        self.assertEqual([(s["owner"], s["match"]["work_id"]) for s in got], [("NatureNet", "w1")])

    def test_other_chases_keep_their_fixed_owner(self):
        e = {"topic": "critic.pass", "ts": (NOW - timedelta(hours=2)).isoformat(timespec="seconds"), "idempotency_key": "k",
             "actor": "Writing Critic", "state_delta": {},
             "refs": {"company": "Acme", "role": "Engineer", "lane": "fte", "packet_path": "/tmp/p", "owner": "Someone Else"}}
        report = chase.detect_stalls(CAT, [e], {"ws": {}, "parks": {}, "owed": {}}, NOW)
        self.assertEqual([s["owner"] for s in report["stalls"] if s["source_topic"] == "critic.pass"], ["Job Search"])


class Publish(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / "catalog", self.root / "catalog")
        (self.root / "events").mkdir()
        (self.root / "state").mkdir()
        self.env = {**os.environ, "AGENT_BUS_ROOT": str(self.root), "AGENT_BUS_SKIP_HOOKS": "1"}

    def tearDown(self):
        self.tmp.cleanup()

    def publish(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "bin" / "publish.py"), *args],
                              capture_output=True, text=True, env=self.env, cwd=self.root)

    def test_claude_code_can_hand_work_to_a_cursor_agent(self):
        p = self.publish("--topic", "work.owed", "--actor", "claude", "--key", "t-1",
                         "--ref", "work_id=t-1", "--ref", "owner=North Idaho Labs", "--ref", "repo=o/r",
                         "--ref", "done=pr_merged", "--ref", "pr=https://github.com/o/r/pull/1")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual(out["must_act"], ["North Idaho Labs"])
        self.assertEqual(out["event"]["actor"], "Claude Code")

    def test_work_handed_to_claude_code_is_pull_only(self):
        p = self.publish("--topic", "work.owed", "--actor", "Grok Bot", "--key", "t-2",
                         "--ref", "work_id=t-2", "--ref", "owner=Claude Code", "--ref", "repo=o/r",
                         "--ref", "done=file_committed", "--ref", "path=reviews/x.md")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["must_act"], [])

    def test_missing_done_is_refused(self):
        p = self.publish("--topic", "work.owed", "--actor", "Grok Bot", "--key", "t-3",
                         "--ref", "work_id=t-3", "--ref", "owner=NatureNet", "--ref", "repo=o/r")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("missing required refs: done", p.stderr)
        self.assertEqual(list((self.root / "events").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
