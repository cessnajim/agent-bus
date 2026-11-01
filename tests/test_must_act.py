#!/usr/bin/env python3
"""Pull-first bus: must_act DM list per topic (2026-10-08)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import chase  # noqa: E402

CAT = json.loads((ROOT / "catalog" / "topics.json").read_text())
T = CAT["topics"]


def dm(topic, refs=None, actor="Test"):
    return chase.resolve_must_act(T[topic], refs or {}, actor)


class MustAct(unittest.TestCase):
    def test_every_topic_has_must_act(self):
        for name, meta in T.items():
            self.assertIn("must_act", meta, name)
            self.assertIn("subscribers", meta, name)

    def test_fte_submit_dms_no_one(self):
        self.assertEqual(dm("fte.submit", actor="Job Search"), [])
        self.assertEqual(chase.pull_only(T["fte.submit"], []), ["Admin", "Unemployment"])

    def test_fte_reject_job_search(self):
        # v8: reject loop moved Admin -> Job Search
        self.assertEqual(dm("fte.reject", actor="Admin"), ["Job Search"])
        self.assertEqual(dm("fte.reject", actor="Job Search"), [])

    def test_park_admin_only_when_jim_needed(self):
        self.assertEqual(dm("fte.park", {"status": "open", "until": "jim_fill", "reason": "other"}, "Job Search"), ["Admin"])
        self.assertEqual(dm("fte.park", {"status": "open", "reason": "workday_create_account"}, "Job Search"), ["Admin"])
        self.assertEqual(dm("fte.park", {"needs_jim": True, "status": "open", "reason": "other"}, "Job Search"), ["Admin"])
        self.assertEqual(dm("fte.park", {"status": "open", "reason": "captcha", "until": "same_day"}, "Job Search"), [])
        self.assertEqual(dm("fte.park", {"status": "cleared", "reason": "workday_create_account"}, "Job Search"), [])

    def test_critic_pass_lanes(self):
        self.assertEqual(dm("critic.pass", {"lane": "fte"}, "Writing Critic"), ["Job Search"])
        self.assertEqual(dm("critic.pass", {}, "Writing Critic"), ["Job Search"])
        self.assertEqual(dm("critic.pass", {"lane": "catalant"}, "Writing Critic"), ["Consulting"])
        self.assertEqual(dm("critic.pass", {"lane": "warm_note"}, "Writing Critic"), ["Admin"])
        self.assertEqual(dm("critic.pass", {"lane": "master"}, "Writing Critic"), [])

    def test_outbound_single_owner(self):
        self.assertEqual(dm("critic.pass.outbound", {"lane": "youtube"}, "Writing Critic"), ["Content Studio"])
        self.assertEqual(dm("critic.pass.outbound", {"lane": "LinkedIn"}, "Writing Critic"), ["OAWJ Shop"])
        self.assertEqual(dm("critic.pass.outbound", {"lane": "outreach"}, "Writing Critic"), ["OAWJ Shop"])
        self.assertEqual(dm("critic.pass.outbound", {"lane": "mystery"}, "Writing Critic"), ["Admin"])

    def test_unchanged_nudge_and_chase(self):
        self.assertEqual(dm("ws.short_of_target", {}, "day-ledger"), ["Job Search"])
        self.assertEqual(dm("chase.owed", {"owner": "Consulting"}, "Admin"), ["Consulting", "Job Search"])
        self.assertEqual(dm("chase.owed", {"owner": "Job Search"}, "Admin"), ["Job Search"])

    def test_catalant_actor_dropped(self):
        self.assertEqual(dm("catalant.pitch_submitted", {}, "Consulting"), [])
        self.assertEqual(dm("catalant.email_confirmed", {}, "Ops"), ["Consulting"])

    def test_legacy_catalog_falls_back_to_subscribers(self):
        meta = {"subscribers": ["Admin", "Ops"]}
        self.assertEqual(chase.resolve_must_act(meta, {}, "Admin"), ["Ops"])


class PublishStdout(unittest.TestCase):
    """End-to-end publish into a throwaway bus root (never the real events/)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-bus-test-"))
        (self.tmp / "catalog").mkdir()
        shutil.copy(ROOT / "catalog" / "topics.json", self.tmp / "catalog" / "topics.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def publish(self, *args):
        env = dict(os.environ, AGENT_BUS_ROOT=str(self.tmp), AGENT_BUS_SKIP_HOOKS="1")
        out = subprocess.run([sys.executable, str(ROOT / "bin" / "publish.py"), *args],
                             env=env, capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def test_fte_submit_stdout_keys(self):
        r = self.publish("--topic", "fte.submit", "--actor", "Job Search", "--key", "t-1",
                         "--ref", "company=Test", "--ref", "role=Dir", "--ref", "source_id=t1",
                         "--ref", "packet_path=/tmp/x")
        self.assertEqual(r["status"], "published")
        self.assertTrue(r["path"].startswith(str(self.tmp)))
        for k in ("subscribers", "done_means", "state_path", "event", "must_act", "pull_only"):
            self.assertIn(k, r)
        self.assertEqual(r["must_act"], [])
        self.assertEqual(r["subscribers"], ["Admin", "Unemployment"])

    def test_critic_warm_note(self):
        r = self.publish("--topic", "critic.pass", "--actor", "Writing Critic", "--key", "t-2",
                         "--ref", "company=Test", "--ref", "role=Dir", "--ref", "packet_path=/tmp/n",
                         "--ref", "lane=warm_note")
        self.assertEqual(r["must_act"], ["Admin"])


if __name__ == "__main__":
    unittest.main()
