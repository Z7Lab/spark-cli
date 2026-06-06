"""Tests for the MCP server logic (lib/mcp.py) — no transport, no SSH."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import mcp
import manifest
import handlers


class TestMcp(unittest.TestCase):
    def test_tool_list_from_manifests(self):
        entries = manifest.discover()
        tools = mcp.tool_list(entries)
        self.assertEqual(len(tools), len(entries))
        names = {t["name"] for t in tools}
        self.assertIn("llm_serve", names)
        serve = next(t for t in tools if t["name"] == "llm_serve")
        self.assertEqual(serve["inputSchema"]["type"], "object")
        self.assertIn("model", serve["inputSchema"]["properties"])
        self.assertIn("model", serve["inputSchema"]["required"])
        self.assertTrue(serve["description"])  # body, not empty

    def test_args_to_argv_positional_flag_bool(self):
        spec = {"params": [
            {"name": "model", "positional": True, "required": True},
            {"name": "port", "type": "int"},
            {"name": "all", "type": "bool"},
        ]}
        argv = mcp.args_to_argv(spec, {"model": "m", "port": 8081, "all": True})
        self.assertEqual(argv[0], "m")              # positional first
        self.assertIn("--port", argv)
        self.assertEqual(argv[argv.index("--port") + 1], "8081")
        self.assertIn("--all", argv)                # bool true → bare flag
        # bool false → flag omitted
        self.assertNotIn("--all", mcp.args_to_argv(spec, {"model": "m", "all": False}))

    def test_call_tool_success_captures_and_returns(self):
        captured = {}

        def fake_handler(params, cfg):
            print("operator output here")
            captured["params"] = params
            return {"action": "demo.run", "ok": True}

        spec = {"domain": "demo", "subcommand": "run", "handler": "demo.run",
                "params": [{"name": "x", "positional": True, "required": True}]}
        by_tool = {"demo_run": {"spec": spec}}
        orig = handlers.get
        handlers.get = lambda name: fake_handler
        try:
            out = mcp.call_tool(by_tool, {}, "demo_run", {"x": "hello"})
        finally:
            handlers.get = orig
        self.assertNotIn("isError", out)
        self.assertEqual(out["content"][0]["text"], "operator output here")
        self.assertEqual(out["structuredContent"], {"action": "demo.run", "ok": True})
        self.assertEqual(captured["params"]["x"], "hello")

    def test_call_tool_systemexit_becomes_error(self):
        def boom(params, cfg):
            print("something failed")
            raise SystemExit(1)

        spec = {"domain": "demo", "subcommand": "run", "handler": "demo.run", "params": []}
        orig = handlers.get
        handlers.get = lambda name: boom
        try:
            out = mcp.call_tool({"demo_run": {"spec": spec}}, {}, "demo_run", {})
        finally:
            handlers.get = orig
        self.assertTrue(out["isError"])
        self.assertIn("something failed", out["content"][0]["text"])

    def test_call_tool_invalid_args_and_unknown_tool(self):
        spec = {"domain": "demo", "subcommand": "run", "handler": "demo.run",
                "params": [{"name": "x", "positional": True, "required": True}]}
        bad = mcp.call_tool({"demo_run": {"spec": spec}}, {}, "demo_run", {})
        self.assertTrue(bad["isError"])
        self.assertIn("missing required", bad["content"][0]["text"])
        unknown = mcp.call_tool({}, {}, "nope", {})
        self.assertTrue(unknown["isError"])
        self.assertIn("Unknown tool", unknown["content"][0]["text"])

    def test_handle_initialize_and_unknown_method(self):
        ctx = {"cfg": {}, "entries": [], "by_tool": {}}
        init = mcp.handle({"method": "initialize", "params": {}}, ctx)
        self.assertEqual(init["protocolVersion"], mcp.PROTOCOL_VERSION)
        self.assertIn("tools", init["capabilities"])
        with self.assertRaises(mcp._MethodNotFound):
            mcp.handle({"method": "bogus/method"}, ctx)


if __name__ == "__main__":
    unittest.main(verbosity=2)
