#!/usr/bin/env python3
"""site.deployed must name the commit that is live (Jim 2026-11-25: 'Apply all three').

Ten deploys in November 2026 named no pull request and five came from uncommitted local files,
so nothing could check them. A deploy with no repo or commit is now refused at publish."""
from __future__ import annotations
import json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
T = json.loads((ROOT / "catalog" / "topics.json").read_text())["topics"]
SHA = "a" * 40


class SiteDeployed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / "catalog", self.root / "catalog")
        (self.root / "events").mkdir(); (self.root / "state").mkdir()
        self.env = {**os.environ, "AGENT_BUS_ROOT": str(self.root), "AGENT_BUS_SKIP_HOOKS": "1"}

    def tearDown(self):
        self.tmp.cleanup()

    def publish(self, *refs):
        args = [sys.executable, str(ROOT / "bin" / "publish.py"), "--topic", "site.deployed", "--actor", "North Idaho Labs", "--key", "k1"]
        for r in refs:
            args += ["--ref", r]
        return subprocess.run(args, capture_output=True, text=True, env=self.env, cwd=self.root)

    def test_required_refs(self):
        self.assertEqual(T["site.deployed"]["required_refs"], ["site", "repo", "commit", "pr", "live_url"])
        self.assertIn("Never deploy from uncommitted files", T["site.deployed"]["done_means"])

    def test_a_deploy_without_its_commit_is_refused(self):
        p = self.publish("site=example.com", "pr=none-local-edit", "live_url=https://example.com/")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("missing required refs: repo, commit", p.stderr)
        self.assertEqual(list((self.root / "events").iterdir()), [])

    def test_a_deploy_from_a_direct_commit_is_accepted_with_pr_none(self):
        p = self.publish("site=example.com", "repo=o/r", f"commit={SHA}", "pr=none", "live_url=https://example.com/")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["event"]["refs"]["commit"], SHA)

    def test_site_pr_opened_is_unchanged(self):
        self.assertEqual(T["site.pr_opened"]["required_refs"], ["site", "pr"])


if __name__ == "__main__":
    unittest.main()
