"""Manifest integrity, routing, handler registry, and MCP schema emission.

Run: python3 -m pytest tests/test_manifest.py   (or: python3 tests/test_manifest.py)
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import manifest
import handlers


class TestManifests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = manifest.discover()
        cls.routing = manifest.build_routing(cls.entries)

    def test_discovers_all_commands(self):
        files = list((manifest.REPO_ROOT / "commands").glob("*/*.md"))
        self.assertEqual(len(self.entries), len(files))
        self.assertGreaterEqual(len(self.entries), 33)

    def test_every_spec_has_required_fields(self):
        for e in self.entries:
            s = e["spec"]
            for field in ("name", "domain", "handler"):
                self.assertIn(field, s, f"{e['path'].name} missing '{field}'")
            self.assertIsInstance(s.get("params", []), list)
            self.assertTrue(s.get("summary"), f"{e['path'].name} missing summary")

    def test_param_names_are_snake_case(self):
        for e in self.entries:
            for p in e["spec"].get("params", []):
                self.assertRegex(p["name"], r"^[a-z][a-z0-9_]*$",
                                 f"{e['path'].name}: bad param name {p['name']!r}")

    def test_no_duplicate_routes(self):
        seen = set()
        for e in self.entries:
            key = (e["spec"]["domain"], e["spec"].get("subcommand"))
            self.assertNotIn(key, seen, f"duplicate route {key}")
            seen.add(key)

    def test_every_handler_ref_resolves(self):
        for e in self.entries:
            ref = e["spec"]["handler"]
            self.assertIn(ref, handlers.REGISTRY, f"unregistered handler {ref}")
            self.assertTrue(callable(handlers.get(ref)))

    def test_no_orphan_handlers(self):
        """Every registered handler is referenced by exactly one manifest."""
        refs = {e["spec"]["handler"] for e in self.entries}
        for name in handlers.REGISTRY:
            self.assertIn(name, refs, f"handler {name} has no manifest")

    def test_routing_groups(self):
        # grouped domains expose subcommands; atomic ones key on None
        self.assertTrue(manifest.is_grouped(self.routing["llm"]))
        self.assertFalse(manifest.is_grouped(self.routing["status"]))
        self.assertIn("serve", self.routing["llm"])
        self.assertIn(None, self.routing["status"])

    def test_canonical_and_tool_names(self):
        serve = self.routing["llm"]["serve"]["spec"]
        self.assertEqual(manifest.canonical_name(serve), "llm.serve")
        self.assertEqual(manifest.tool_name(serve), "llm_serve")
        status = self.routing["status"][None]["spec"]
        self.assertEqual(manifest.canonical_name(status), "status")
        self.assertEqual(manifest.tool_name(status), "status")

    def test_json_schema_emission(self):
        for e in self.entries:
            schema = manifest.to_json_schema(e["spec"])
            self.assertEqual(schema["name"], manifest.tool_name(e["spec"]))
            self.assertEqual(schema["inputSchema"]["type"], "object")
            props = schema["inputSchema"]["properties"]
            # required list only names actual required params
            for req in schema["inputSchema"].get("required", []):
                self.assertIn(req, props)

    def test_serve_schema_marks_model_required(self):
        schema = manifest.to_json_schema(self.routing["llm"]["serve"]["spec"])
        self.assertEqual(schema["inputSchema"]["required"], ["model"])
        self.assertEqual(schema["inputSchema"]["properties"]["ctx"]["type"], "integer")

    def test_enum_becomes_schema_enum(self):
        schema = manifest.to_json_schema(self.routing["comfy"]["pull-models"]["spec"])
        self.assertEqual(schema["inputSchema"]["properties"]["set"]["enum"],
                         ["generate", "animate", "all"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
