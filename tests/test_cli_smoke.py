"""End-to-end CLI smoke / behavior-parity tests (no DGX required).

Exercises routing, the three-level help hierarchy, and parse-error handling by
invoking bin/spark as a subprocess — the layer that must behave identically after
the manifest refactor. Commands that would touch the DGX over SSH are only probed
via --help, never run.

Run: python3 -m pytest tests/test_cli_smoke.py   (or: python3 tests/test_cli_smoke.py)
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPARK = ROOT / "bin" / "spark"
sys.path.insert(0, str(ROOT / "lib"))

import manifest


def run(*args):
    env = dict(os.environ, NO_COLOR="1")
    return subprocess.run([sys.executable, str(SPARK), *args],
                          capture_output=True, text=True, env=env)


class TestCli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = manifest.discover()
        cls.routing = manifest.build_routing(cls.entries)

    def test_top_help_exits_zero(self):
        for arg in ([], ["--help"], ["-h"]):
            r = run(*arg)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Commands:", r.stdout)

    def test_top_help_lists_every_domain(self):
        r = run("--help")
        for domain in self.routing:
            self.assertIn(domain, r.stdout)

    def test_every_command_help_exits_zero(self):
        for e in self.entries:
            spec = e["spec"]
            argv = [spec["domain"]]
            if spec.get("subcommand"):
                argv.append(spec["subcommand"])
            argv.append("--help")
            r = run(*argv)
            self.assertEqual(r.returncode, 0, f"{argv}: {r.stderr}")
            self.assertIn(spec["summary"], r.stdout, f"{argv} help missing summary")
            self.assertIn("Usage:", r.stdout, f"{argv} help missing usage")

    def test_grouped_domain_help_lists_subcommands(self):
        for domain, group in self.routing.items():
            if not manifest.is_grouped(group):
                continue
            r = run(domain)
            self.assertEqual(r.returncode, 0, r.stderr)
            for sub in (s for s in group if s):
                self.assertIn(sub, r.stdout, f"{domain} help missing {sub}")

    def test_unknown_command(self):
        r = run("definitely-not-a-command")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Unknown command", r.stdout)

    def test_unknown_subcommand(self):
        r = run("llm", "frobnicate")
        self.assertEqual(r.returncode, 1)
        self.assertIn("subcommand", r.stdout)

    def test_missing_required_positional(self):
        r = run("download", "only-one-arg")
        self.assertEqual(r.returncode, 1)
        self.assertIn("missing required", r.stdout)

    def test_unknown_flag_rejected(self):
        r = run("llm", "serve", "m", "--nope", "x")
        self.assertEqual(r.returncode, 1)
        self.assertIn("unknown flag", r.stdout)

    def test_bad_enum_rejected(self):
        r = run("comfy", "pull-models", "--set", "nonsense")
        self.assertEqual(r.returncode, 1)
        self.assertIn("must be one of", r.stdout)

    def test_schema_single_and_all(self):
        import json
        one = run("_schema", "llm.serve")
        self.assertEqual(one.returncode, 0, one.stderr)
        obj = json.loads(one.stdout)
        self.assertEqual(obj["name"], "llm_serve")

        allspecs = run("_schema")
        self.assertEqual(allspecs.returncode, 0, allspecs.stderr)
        arr = json.loads(allspecs.stdout)
        self.assertEqual(len(arr), len(self.entries))

    def test_playbook_check_and_show(self):
        # llm-serve ships as a structured-command-ref playbook
        chk = run("playbook", "check", "llm-serve")
        self.assertEqual(chk.returncode, 0, chk.stdout)
        self.assertIn("valid", chk.stdout)
        show = run("playbook", "run", "llm-serve", "--step", "serve", "--model", "demo")
        self.assertIn("spark llm serve demo", show.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
