"""Structured-return contract tests for lib/handlers/*.

Handlers print operator output AND return a structured result dict so a future
MCP server can reuse the same handler for tool output. These tests monkeypatch
the sparkcore query functions the handlers imported (so no SSH happens) and
assert the contract for a few pure / early-return paths.

Run: python3 -m pytest tests/test_handler_returns.py  (or run the file directly)
"""
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "lib"))

from handlers import llm  # noqa: E402


class TestHandlerReturns(unittest.TestCase):
    def setUp(self):
        # Minimal cfg — handlers under test never reach a real SSH call once the
        # query functions below are patched.
        self.cfg = {
            "dgx_host": "dgx.local", "dgx_user": "spark", "port": 8080,
            "models_dir": "~/models",
        }
        self.params = {"port": None, "quant": None, "name": None}
        # Save originals so each test restores cleanly.
        self._orig = {
            "_llm_instances": llm._llm_instances,
            "_free_bytes": llm._free_bytes,
            "ssh": llm.ssh,
        }

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(llm, k, v)

    def _call(self, fn, params=None):
        """Invoke a handler with stdout suppressed; return its structured result."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            return fn(params if params is not None else self.params, self.cfg)

    def test_llm_list_empty(self):
        llm._llm_instances = lambda cfg: []
        llm._free_bytes = lambda cfg: 1234
        result = self._call(llm.ls)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["action"], "llm.list")
        self.assertEqual(result["loaded"], [])
        self.assertEqual(result["free_bytes"], 1234)

    def test_llm_stop_none_running(self):
        # ssh returns "" → no pids → nothing stopped.
        llm.ssh = lambda cfg, cmd, **kw: ""
        result = self._call(llm.stop)
        self.assertEqual(result, {"action": "llm.stop", "stopped": 0})

    def test_llm_unload_no_models(self):
        llm._llm_instances = lambda cfg: []
        result = self._call(llm.unload)
        self.assertEqual(result, {"action": "llm.unload", "unloaded": None})

    def test_llm_logs_no_models(self):
        llm._llm_instances = lambda cfg: []
        result = self._call(llm.logs, {"port": None, "lines": 50})
        self.assertEqual(result["action"], "llm.logs")
        self.assertIsNone(result["port"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
