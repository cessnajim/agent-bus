#!/usr/bin/env python3
"""PT time pin + duplicate fte.submit guard (2026-10-08).

Runs publish.py / busctl.py against a throwaway AGENT_BUS_ROOT; the live
events/ and state/ are never touched. The calling env deliberately exports
TZ=America/New_York to prove the bus ignores it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import chase  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")


class TempBus(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-bus-test-"))
        (self.tmp / "catalog").mkdir()
        shutil.copy(ROOT / "catalog" / "topics.json", self.tmp / "catalog" / "topics.json")
        (self.tmp / "events").mkdir()
        self.env = dict(os.environ, AGENT_BUS_ROOT=str(self.tmp), AGENT_BUS_SKIP_HOOKS="1", TZ="America/New_York")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_events(self, day: str, rows: list[dict]):
        with (self.tmp / "events" / f"{day}.jsonl").open("a") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")

    def run_bin(self, *args, wrapper=False):
        cmd = [str(ROOT / "bin" / "agent-bus"), *args] if wrapper else [sys.executable, *args]
        proc = subprocess.run(cmd, env=self.env, capture_output=True, text=True, cwd=str(ROOT))
        out = None
        if proc.stdout.strip():
            try:
                out = json.loads(proc.stdout)
            except json.JSONDecodeError:
                out = proc.stdout
        return proc.returncode, out, proc.stderr

    def submit(self, key, company, source_id, *extra):
        return self.run_bin(
            str(ROOT / "bin" / "publish.py"), "--topic", "fte.submit", "--actor", "FTE Apply", "--key", key,
            "--ref", f"company={company}", "--ref", "role=Director, Engineering",
            "--ref", f"source_id={source_id}", "--ref", "packet_path=/tmp/p", *extra,
        )

    def submits_on_bus(self):
        n = 0
        for p in (self.tmp / "events").glob("*.jsonl"):
            n += sum(1 for ev in chase.read_jsonl(p) if ev.get("topic") == "fte.submit")
        return n


class TimezonePin(TempBus):
    def test_publish_stamps_pacific_even_with_eastern_shell(self):
        code, out, err = self.submit("tz-1", "Acme", "A1")
        self.assertEqual(code, 0, err)
        ts = datetime.fromisoformat(out["event"]["ts"])
        now_pt = datetime.now(PT)
        self.assertEqual(ts.utcoffset(), now_pt.utcoffset())  # -07:00 PDT / -08:00 PST, never -04:00
        self.assertEqual(Path(out["path"]).name, f"{now_pt.date().isoformat()}.jsonl")
        print(f"\n  publish ts={out['event']['ts']} file={Path(out['path']).name} (shell TZ=America/New_York)")

    def test_chase_import_pins_process_tz(self):
        code = "import sys,time; sys.path.insert(0,'bin'); import chase; print(time.strftime('%Z'))"
        proc = subprocess.run([sys.executable, "-c", code], env=self.env, capture_output=True, text=True, cwd=str(ROOT))
        self.assertIn(proc.stdout.strip(), ("PDT", "PST"))

    def test_wrapper_exports_pacific(self):
        code, out, err = self.run_bin("root", wrapper=True)
        self.assertEqual(code, 0, err)
        text = (ROOT / "bin" / "agent-bus").read_text()
        self.assertIn("export TZ=America/Los_Angeles", text)

    def legacy_fixture(self):
        # Legacy (Eastern-filed) and new (Pacific) events straddling PT midnight.
        self.write_events("2026-10-08", [
            {"topic": "fte.submit", "ts": "2026-10-08T00:30:00-04:00", "idempotency_key": "late-pt-1007", "refs": {}},
            {"topic": "fte.submit", "ts": "2026-10-08T09:00:00-04:00", "idempotency_key": "morning-pt-1008", "refs": {}},
            {"topic": "fte.submit", "ts": "2026-10-08T23:30:00-07:00", "idempotency_key": "new-style-late", "refs": {}},
        ])
        self.write_events("2026-10-09", [
            {"topic": "fte.submit", "ts": "2026-10-09T01:00:00-04:00", "idempotency_key": "legacy-pt-2200", "refs": {}},
            {"topic": "fte.submit", "ts": "2026-10-09T00:05:00-07:00", "idempotency_key": "after-pt-midnight", "refs": {}},
        ])

    def test_today_rolls_over_at_pt_midnight_and_normalizes_legacy(self):
        self.legacy_fixture()
        keys = [e["idempotency_key"] for e in chase.events_for_bus_day(self.tmp / "events", "2026-10-08")]
        self.assertEqual(keys, ["morning-pt-1008", "legacy-pt-2200", "new-style-late"])
        keys07 = [e["idempotency_key"] for e in chase.events_for_bus_day(self.tmp / "events", "2026-10-07")]
        self.assertEqual(keys07, ["late-pt-1007"])
        code, out, err = self.run_bin(str(ROOT / "bin" / "busctl.py"), "today", "--day", "2026-10-08")
        self.assertEqual(code, 0, err)
        self.assertEqual([e["idempotency_key"] for e in out], keys)
        print(f"\n  busctl today --day 2026-10-08 -> {keys}")

    def test_mixed_offsets_sort_chronologically(self):
        a = {"ts": "2026-10-08T07:00:00-07:00", "k": "10:00 ET"}
        b = {"ts": "2026-10-08T09:00:00-04:00", "k": "09:00 ET"}
        self.assertEqual([e["k"] for e in chase.order_events([a, b])], ["09:00 ET", "10:00 ET"])

    def test_parse_ts_returns_pacific(self):
        self.assertEqual(chase.parse_ts("2026-10-08T00:30:00-04:00").isoformat(), "2026-10-07T21:30:00-07:00")
        self.assertEqual(chase.bus_day("2026-10-08T00:30:00-04:00"), "2026-10-07")


class DuplicateSubmitGuard(TempBus):
    def test_same_company_and_source_id_refused(self):
        code, out, _ = self.submit("s1", "Acme, Inc.", "123")
        self.assertEqual((code, out["status"]), (0, "published"))
        code, out, _ = self.submit("s2-other-key", "ACME", "123")
        self.assertEqual(code, 3)
        self.assertEqual(out["status"], "duplicate_submit")
        self.assertTrue(out["duplicate"])
        self.assertEqual(out["prior"][0]["matched_on"], ["company+source_id"])
        self.assertEqual(self.submits_on_bus(), 1)
        print(f"\n  second submit exit={code} status={out['status']} prior={out['prior'][0]['idempotency_key']}")

    def test_prior_in_older_day_file_and_same_job_url_refused(self):
        self.write_events("2026-09-30", [{
            "topic": "fte.submit", "ts": "2026-09-30T10:00:00-04:00", "idempotency_key": "old",
            "refs": {"company": "Globex", "source_id": "gh-1", "url": "https://boards.greenhouse.io/globex/jobs/555?gh_src=li&utm_source=x"},
        }])
        code, out, _ = self.submit("new", "Globex Corporation", "different-id",
                                   "--ref", "job_url=https://www.boards.greenhouse.io/globex/jobs/555/")
        self.assertEqual(code, 3)
        self.assertEqual(out["prior"][0]["file"], "2026-09-30.jsonl")
        self.assertEqual(out["prior"][0]["matched_on"], ["job_url"])

    def test_force_requires_note_then_publishes_marked_reapply(self):
        self.submit("s1", "Acme", "123")
        code, _, err = self.submit("s2", "Acme", "123", "--force")
        self.assertNotEqual(code, 0)
        self.assertIn("--force needs --note", err)
        code, out, err = self.submit("s2", "Acme", "123", "--force", "--note", "Recruiter asked Jim to reapply to reposted req")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["status"], "published")
        self.assertTrue(out["event"]["reapply"])
        self.assertEqual(out["event"]["duplicate_of"], ["s1"])
        self.assertEqual(self.submits_on_bus(), 2)

    def test_same_key_replay_is_still_idempotent_exit_0(self):
        self.submit("s1", "Acme", "123")
        code, out, _ = self.submit("s1", "Acme", "123")
        self.assertEqual((code, out["status"]), (0, "duplicate"))
        self.assertEqual(self.submits_on_bus(), 1)

    def test_different_company_same_source_id_allowed(self):
        self.submit("s1", "Acme", "123")
        code, out, _ = self.submit("s2", "Initech", "123")
        self.assertEqual((code, out["status"]), (0, "published"))

    def test_force_rejected_on_other_topics(self):
        code, _, err = self.run_bin(str(ROOT / "bin" / "publish.py"), "--topic", "ws.short_of_target", "--actor", "Admin",
                                    "--key", "x", "--force", "--note", "n")
        self.assertNotEqual(code, 0)
        self.assertIn("--force only applies", err)


if __name__ == "__main__":
    unittest.main()
