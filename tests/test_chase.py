#!/usr/bin/env python3
"""Chase stalls and the absolute fte.submit count."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import chase  # noqa: E402

EASTERN = timezone(timedelta(hours=-4))
NOW = datetime(2026, 10, 1, 18, 0, tzinfo=EASTERN)


def ts(hours: int, minutes: int = 0, day: int = 1) -> str:
    return datetime(2026, 10, day, hours, minutes, tzinfo=EASTERN).isoformat(timespec="seconds")


def event(topic: str, when: str, refs: dict, delta: dict | None = None, key: str = "k") -> dict:
    return {
        "topic": topic,
        "ts": when,
        "idempotency_key": key,
        "actor": "Test",
        "refs": refs,
        "state_delta": delta or {},
    }


def catalog() -> dict:
    return {
        "owner": "Admin",
        "follow_through": {
            "primary": "Admin",
            "proof": "next bus event",
            "debounce_minutes": 30,
            "lookback_days": 2,
        },
        "topics": {
            "critic.pass": {
                "chase": {
                    "owner": "FTE Apply",
                    "expect": ["fte.submit", "fte.park"],
                    "match": ["company", "role"],
                    "debounce_minutes": 30,
                }
            },
            "ws.short_of_target": {
                "state_file": "ws",
                "chase": {
                    "owner": "FTE Apply",
                    "expect": ["fte.submit"],
                    "while": "submitted_below_target",
                    "clear_refs": {"inventory": "empty"},
                    "debounce_minutes": 30,
                },
            },
            "fte.submit": {"state_file": "ws"},
            "fte.park": {"state_file": "parks"},
            "chase.owed": {"state_file": "owed"},
        },
    }


def stalls(events, states=None, now=NOW):
    report = chase.detect_stalls(catalog(), events, states or {}, now)
    return report


class FloorTests(unittest.TestCase):
    def test_repeated_one_cannot_stick_below_ledger(self):
        stored, adjusted = chase.submitted_floor(1, 2)
        self.assertEqual(stored, 2)
        self.assertTrue(adjusted)

    def test_higher_absolute_claim_wins(self):
        stored, adjusted = chase.submitted_floor(5, 2)
        self.assertEqual(stored, 5)
        self.assertFalse(adjusted)

    def test_missing_claim_uses_ledger(self):
        stored, adjusted = chase.submitted_floor(None, 2)
        self.assertEqual(stored, 2)
        self.assertTrue(adjusted)


class StallTests(unittest.TestCase):
    def test_critic_pass_without_submit_is_nudge_due(self):
        report = stalls([
            event("critic.pass", ts(16), {
                "company": "Acme", "role": "Engineer", "lane": "fte", "packet_path": "/tmp/p",
            }, key="pass-1"),
        ])
        self.assertEqual(report["nudge_due_count"], 1)
        row = report["stalls"][0]
        self.assertEqual(row["owner"], "FTE Apply")
        self.assertEqual(row["expect"], ["fte.submit", "fte.park"])
        self.assertEqual(row["stall_id"], "critic.pass:acme:engineer")
        self.assertTrue(row["nudge_due"])
        self.assertEqual(report["open_parks"]["count"], 0)

    def test_submit_clears_matching_pass_only(self):
        events = [
            event("critic.pass", ts(15), {"company": "Acme", "role": "Engineer"}, key="p1"),
            event("critic.pass", ts(15, 5), {"company": "Other", "role": "Engineer"}, key="p2"),
            event("fte.submit", ts(16), {"company": "acme", "role": "engineer", "source_id": "1"}, key="s1"),
        ]
        report = stalls(events)
        ids = [row["stall_id"] for row in report["stalls"]]
        self.assertNotIn("critic.pass:acme:engineer", ids)
        self.assertIn("critic.pass:other:engineer", ids)

    def test_same_second_submit_still_clears_pass(self):
        when = ts(16)
        report = stalls([
            event("critic.pass", when, {"company": "Acme", "role": "Engineer"}, key="p"),
            event("fte.submit", when, {"company": "Acme", "role": "Engineer", "source_id": "1"}, key="s"),
        ])
        self.assertEqual(report["stalls"], [])

    def test_park_clears_pass(self):
        report = stalls([
            event("critic.pass", ts(15), {"company": "Acme", "role": "Engineer"}, key="p"),
            event("fte.park", ts(16), {
                "company": "Acme", "role": "Engineer", "status": "open", "source_id": "9",
            }, key="park"),
        ])
        self.assertEqual(report["stalls"], [])

    def test_fresh_pass_is_not_nudge_due(self):
        report = stalls([
            event("critic.pass", ts(17, 50), {"company": "Acme", "role": "Engineer"}, key="p"),
        ])
        self.assertEqual(len(report["stalls"]), 1)
        self.assertFalse(report["stalls"][0]["overdue"])
        self.assertFalse(report["stalls"][0]["nudge_due"])

    def test_recent_nudge_suppresses_another(self):
        owed = {
            "owed": {
                "critic.pass:acme:engineer": {
                    "status": "open",
                    "updated_at": ts(17, 45),
                    "owner": "FTE Apply",
                }
            }
        }
        report = stalls([
            event("critic.pass", ts(16), {"company": "Acme", "role": "Engineer"}, key="p"),
        ], {"owed": owed})
        row = report["stalls"][0]
        self.assertTrue(row["overdue"])
        self.assertFalse(row["nudge_due"])
        self.assertEqual(row["last_nudge"], ts(17, 45))

    def test_ws_short_and_honest_empty(self):
        short = stalls([
            event("ws.short_of_target", ts(16), {
                "date": "2026-10-01", "submitted": 1, "target": 6,
            }, key="short"),
            event("fte.submit", ts(15), {"company": "A", "role": "R", "source_id": "1"}, {"submitted": 1}, key="s"),
        ])
        self.assertEqual(short["stalls"][0]["kind"], "submitted_below_target")
        self.assertEqual(short["stalls"][0]["detail"]["submitted"], 1)
        self.assertEqual(short["stalls"][0]["detail"]["gap"], 5)
        self.assertTrue(short["stalls"][0]["nudge_due"])

        empty = stalls([
            event("ws.short_of_target", ts(17), {
                "date": "2026-10-01", "submitted": 1, "target": 6, "inventory": "empty",
            }, key="empty"),
        ])
        self.assertEqual(empty["stalls"], [])

    def test_ledger_raises_a_low_claim(self):
        report = stalls([
            event("fte.submit", ts(15), {"submitted": 1, "target": 6, "company": "A", "role": "R"}, {"submitted": 1}, key="s1"),
            event("fte.submit", ts(16), {"submitted": 1, "target": 6, "company": "B", "role": "R"}, {"submitted": 1}, key="s2"),
        ], {"ws": {"submitted": 1, "target": 6, "date": "2026-10-01"}})
        self.assertEqual(report["stalls"][0]["detail"]["submitted"], 2)
        self.assertEqual(report["stalls"][0]["detail"]["gap"], 4)

    def test_target_met_and_stale_state_are_quiet(self):
        met = stalls([], {"ws": {"submitted": 6, "target": 6, "date": "2026-10-01"}})
        self.assertEqual(met["stalls"], [])
        stale = stalls([], {"ws": {"submitted": 1, "target": 6, "date": "2026-09-30"}})
        self.assertEqual(stale["stalls"], [])

    def test_open_parks_count(self):
        report = stalls([], {"parks": {"parks": {
            "1": {"status": "open", "company": "Acme", "role": "Engineer", "source_id": "1", "do_not_reprompt": True},
            "2": {"status": "cleared", "company": "Old", "role": "Role", "source_id": "2"},
        }}})
        self.assertEqual(report["open_parks"]["count"], 1)
        self.assertTrue(report["open_parks"]["items"][0]["do_not_reprompt"])

    def test_manual_owed_row_survives_without_a_chase_block(self):
        report = stalls([], {"owed": {"owed": {
            "custom:1": {
                "status": "open",
                "owed_topic": "task.done",
                "expect": ["task.done"],
                "owner": "Worker",
                "since": ts(12),
                "updated_at": ts(12),
                "match": {"item_id": "1"},
            }
        }}})
        self.assertEqual(report["stalls"][0]["stall_id"], "custom:1")
        self.assertTrue(report["stalls"][0]["nudge_due"])

    def test_proof_clears_owed_row(self):
        row = {
            "status": "open",
            "owed_topic": "fte.submit",
            "expect": ["fte.submit", "fte.park"],
            "since": ts(15),
            "match": {"company": "Acme", "role": "Engineer"},
        }
        proof = event("fte.submit", ts(16), {"company": "Acme", "role": "Engineer"})
        other = event("fte.submit", ts(16), {"company": "Other", "role": "Engineer"})
        self.assertTrue(chase.proof_clears_owed(row, proof))
        self.assertFalse(chase.proof_clears_owed(row, other))


class ReportIOTests(unittest.TestCase):
    def test_lookback_skips_older_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "catalog").mkdir()
            (root / "events").mkdir()
            (root / "state").mkdir()
            (root / "catalog" / "topics.json").write_text(json.dumps(catalog()))
            old = NOW - timedelta(days=5)
            old_day = old.date().isoformat()
            (root / "events" / f"{old_day}.jsonl").write_text(json.dumps(event(
                "critic.pass", old.isoformat(timespec="seconds"),
                {"company": "Acme", "role": "Engineer"}, key="old",
            )) + "\n")
            report = chase.build_stall_report(root, NOW)
            self.assertEqual(report["stalls"], [])


class PublishIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / "catalog", self.root / "catalog")
        (self.root / "events").mkdir()
        (self.root / "state").mkdir()
        self.env = os.environ.copy()
        self.env["AGENT_BUS_ROOT"] = str(self.root)
        self.env["AGENT_BUS_SKIP_HOOKS"] = "1"
        self.publish = ROOT / "bin" / "publish.py"
        self.busctl = ROOT / "bin" / "busctl.py"

    def tearDown(self):
        self.tmp.cleanup()

    def run_json(self, script: Path, args: list[str]) -> dict:
        proc = subprocess.run(
            [sys.executable, str(script), *args],
            check=True,
            capture_output=True,
            text=True,
            env=self.env,
            cwd=self.root,
        )
        return json.loads(proc.stdout)

    def test_two_submitted_ones_floor_to_two(self):
        common = [
            "--topic", "fte.submit", "--actor", "FTE Apply",
            "--ref", "company=Acme", "--ref", "role=Engineer",
            "--ref", "packet_path=/tmp/packet",
            "--delta", "submitted=1", "--delta", "target=6", "--delta", "date=2026-10-01",
        ]
        first = self.run_json(self.publish, [*common, "--key", "sub-1", "--ref", "source_id=1"])
        self.assertEqual(first["status"], "published")
        self.assertNotIn("submitted_floor", first)
        second = self.run_json(self.publish, [*common, "--key", "sub-2", "--ref", "source_id=2", "--ref", "company=Other"])
        self.assertEqual(second["submitted_floor"]["claimed"], 1)
        self.assertEqual(second["submitted_floor"]["ledger"], 2)
        self.assertEqual(second["submitted_floor"]["stored"], 2)
        # The jsonl event keeps the publisher's claim.
        self.assertEqual(second["event"]["state_delta"]["submitted"], 1)
        state = json.loads((self.root / "state" / "ws.json").read_text())
        self.assertEqual(state["submitted"], 2)
        self.assertEqual(state["state"]["submitted"], 2)

    def test_short_event_cannot_clobber_the_ledger(self):
        common = [
            "--topic", "fte.submit", "--actor", "FTE Apply",
            "--ref", "role=Engineer", "--ref", "packet_path=/tmp/packet",
            "--delta", "submitted=1", "--delta", "target=6", "--delta", "date=2026-10-01",
        ]
        self.run_json(self.publish, [*common, "--key", "s1", "--ref", "company=A", "--ref", "source_id=1"])
        self.run_json(self.publish, [*common, "--key", "s2", "--ref", "company=B", "--ref", "source_id=2"])
        self.run_json(self.publish, [
            "--topic", "ws.short_of_target", "--actor", "day-ledger", "--key", "short-1",
            "--ref", "date=2026-10-01", "--ref", "submitted=1", "--ref", "target=6",
            "--delta", "submitted=1", "--delta", "target=6",
        ])
        state = json.loads((self.root / "state" / "ws.json").read_text())
        self.assertEqual(state["submitted"], 2)

    def test_stall_nudge_and_proof(self):
        self.run_json(self.publish, [
            "--topic", "critic.pass", "--actor", "Writing Critic", "--key", "pass-acme",
            "--ref", "company=Acme", "--ref", "role=Engineer",
            "--ref", "packet_path=/tmp/packet", "--ref", "lane=fte",
        ])
        report = self.run_json(self.busctl, ["stall"])
        alias = self.run_json(self.busctl, ["owed"])
        self.assertEqual(report, alias)
        row = next(item for item in report["stalls"] if item["stall_id"] == "critic.pass:acme:engineer")
        self.assertFalse(row["nudge_due"])  # just published; inside debounce
        self.run_json(self.publish, [
            "--topic", "chase.owed", "--actor", "Admin", "--key", "nudge-acme",
            "--ref", f"stall_id={row['stall_id']}",
            "--ref", "owed_topic=fte.submit",
            "--ref", "expect=fte.submit,fte.park",
            "--ref", "owner=FTE Apply",
            "--ref", "status=open",
            "--ref", f"since={row['since']}",
            "--ref", "company=Acme",
            "--ref", "role=Engineer",
        ])
        owed = json.loads((self.root / "state" / "owed.json").read_text())
        self.assertEqual(owed["open_count"], 1)
        self.assertIn("critic.pass:acme:engineer", owed["owed"])
        proof = self.run_json(self.publish, [
            "--topic", "fte.submit", "--actor", "FTE Apply", "--key", "sub-acme",
            "--ref", "company=Acme", "--ref", "role=Engineer",
            "--ref", "source_id=42", "--ref", "packet_path=/tmp/packet",
            "--delta", "submitted=1", "--delta", "target=6", "--delta", "date=2026-10-01",
        ])
        self.assertEqual(proof["chase_cleared"], ["critic.pass:acme:engineer"])
        owed_after = json.loads((self.root / "state" / "owed.json").read_text())
        self.assertEqual(owed_after["open_count"], 0)
        self.assertNotIn("critic.pass:acme:engineer", owed_after.get("owed") or {})
        after = self.run_json(self.busctl, ["stall"])
        ids = [item["stall_id"] for item in after["stalls"]]
        self.assertNotIn("critic.pass:acme:engineer", ids)

    def test_open_park_shows_up_in_stall(self):
        self.run_json(self.publish, [
            "--topic", "fte.park", "--actor", "FTE Apply", "--key", "park-1",
            "--ref", "company=Acme", "--ref", "role=Engineer", "--ref", "source_id=7",
            "--ref", "reason=captcha", "--ref", "status=open", "--ref", "do_not_reprompt=true",
        ])
        report = self.run_json(self.busctl, ["stall"])
        self.assertEqual(report["open_parks"]["count"], 1)
        self.assertEqual(report["open_parks"]["items"][0]["reason"], "captcha")
        self.assertTrue(report["open_parks"]["items"][0]["do_not_reprompt"])


if __name__ == "__main__":
    unittest.main()
