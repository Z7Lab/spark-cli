"""Unit tests for the train domain (no DGX required).

The train handlers drive ai-toolkit over SSH; these tests monkeypatch the SSH /
session probes the module imported (so nothing touches a real DGX) and cover the
pure helpers plus the early-return control paths. The in-container watchdog
(bin/spark_train.py) is exercised for its checkpoint-step parsing.

Run: python3 tests/test_train.py
"""
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "lib"))

from handlers import train  # noqa: E402


class TestTrainHelpers(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "dgx_host": "dgx.local", "dgx_user": "spark", "port": 8080,
            # absolute so _resolved() is a no-op (no ssh) in these unit tests
            "comfy_dir": "/srv/comfyui", "train_dir": "/srv/spark-train",
            "train_base_model": "black-forest-labs/FLUX.2-klein-base-4B",
            "train_arch": "flux2_klein_4b",
        }
        self._orig = {k: getattr(train, k) for k in
                      ("ssh", "_session_running", "_read_state", "_list_runs")}

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(train, k, v)

    def _call(self, fn, params):
        buf = io.StringIO()
        with redirect_stdout(buf):
            return fn(params, self.cfg)

    def test_name_from_corpus_sanitises(self):
        self.assertEqual(train._name_from_corpus("/tmp/My Style!/"), "My_Style")
        self.assertEqual(train._name_from_corpus("/tmp/inkwash"), "inkwash")

    def test_render_config_substitutes_all_tokens(self):
        import re
        p = train._render_config(self.cfg, "mystyle", "trg", 2000, 250, 16, 1024)
        txt = p.read_text()
        self.assertNotRegex(txt, r"@@\w+@@")
        self.assertIn('trigger_word: "trg"', txt)
        # Default base = klein-4B (Apache/ungated), fetched by ai-toolkit by repo id.
        self.assertIn('name_or_path: "black-forest-labs/FLUX.2-klein-base-4B"', txt)
        self.assertIn('arch: "flux2_klein_4b"', txt)
        self.assertIn("quantize: false", txt)   # 4B trains unquantized
        self.assertIn("steps: 2000", txt)

    def test_render_sample_config(self):
        import re
        v = {"base": "black-forest-labs/FLUX.2-klein-base-4B", "arch": "flux2_klein_4b",
             "rank": "32", "trigger": "mystylexr", "dataset": "/workspace/datasets/mystyle",
             "resolution": "1024", "quantize": "false"}
        p = train._render_sample_config(
            self.cfg, "mystyle", ['trg a busy "neon" market', 'trg a dragon'], v,
            "/srv/out/mystyle/mystyle.safetensors", 1024, 1024, 20, 42)
        txt = p.read_text()
        self.assertNotRegex(txt, r"@@\w+@@")
        self.assertIn("job: extension", txt)                       # sample via training SampleProcess
        self.assertIn('pretrained_lora_path: "/srv/out/mystyle/mystyle.safetensors"', txt)
        self.assertIn("linear: 32", txt)                           # rank from the run config
        self.assertIn('arch: "flux2_klein_4b"', txt)
        self.assertIn("steps: 1", txt)                             # throwaway, no real training
        self.assertIn('- "trg a busy \\"neon\\" market"', txt)     # quotes escaped
        self.assertIn('- "trg a dragon"', txt)

    def test_loras_dir(self):
        self.assertEqual(train._loras_dir(self.cfg), "/srv/comfyui/workspace/models/loras")

    def test_pause_no_session(self):
        train._session_running = lambda cfg: False
        r = self._call(train.pause, {"name": "mystyle"})
        self.assertEqual(r, {"action": "train.pause", "name": "mystyle", "paused": False})

    def test_resume_already_complete(self):
        train._session_running = lambda cfg: False
        train._read_state = lambda cfg, name: {"status": "complete", "current_step": 2000}
        r = self._call(train.resume, {"name": "mystyle", "max_hours": None})
        self.assertFalse(r["resumed"])

    def test_status_ambiguous_lists_runs(self):
        train._list_runs = lambda cfg: ["a", "b"]
        train._session_running = lambda cfg: False
        r = self._call(train.status, {"name": None, "logs": False})
        self.assertEqual(r["action"], "train.status")
        self.assertEqual(r["runs"], ["a", "b"])

    def test_publish_copies_latest_checkpoint(self):
        calls = []

        def fake_ssh(cfg, cmd, **kw):
            calls.append(cmd)
            if "cp -f" in cmd:
                return ""
            if "safetensors" in cmd:   # the latest-checkpoint finder
                return "/srv/spark-train/output/mystyle/mystyle.safetensors"
            return ""
        train.ssh = fake_ssh
        target = train._publish(self.cfg, "mystyle")
        self.assertEqual(target, "/srv/comfyui/workspace/models/loras/mystyle.safetensors")
        self.assertTrue(any("cp -f" in c for c in calls))

    def test_publish_none_when_no_checkpoint(self):
        train.ssh = lambda cfg, cmd, **kw: ""
        self.assertIsNone(train._publish(self.cfg, "mystyle"))

    def test_deploy_ships_shared_watchdog(self):
        # spark_train.py does `import spark_watchdog`; the deploy MUST ship it alongside
        # or the in-container launch dies with ModuleNotFoundError. Guards that regression.
        import inspect
        self.assertIn("spark_watchdog.py", inspect.getsource(train._deploy_assets))
        wd = Path(__file__).resolve().parent.parent / "bin" / "spark_watchdog.py"
        self.assertTrue(wd.is_file(), "bin/spark_watchdog.py missing")

    def test_sample_refuses_when_box_busy(self):
        # A sample runs as a bare `docker compose run` (not a training screen), so the
        # busy-guard must catch a live spark-train/finetune container — else concurrent
        # samples collide on the GPU + the shared throwaway dir.
        orig = {k: getattr(train, k) for k in ("_latest_lora", "docker_probe")}
        self.addCleanup(lambda: [setattr(train, k, v) for k, v in orig.items()])
        train._session_running = lambda cfg: False
        train._latest_lora = lambda cfg, name: "/srv/spark-train/output/mystyle/mystyle.safetensors"
        train.docker_probe = lambda cfg: ("ok", "")
        train.ssh = lambda cfg, cmd, **kw: "spark-train-mystyle" if "docker ps" in cmd else ""
        r = self._call(train.sample, {"name": "mystyle", "prompt": ["trg a scene"],
                                      "seed": None, "width": 1024, "height": 1024,
                                      "steps": 20, "out": None})
        self.assertFalse(r["sampled"])


class TestWatchdog(unittest.TestCase):
    def test_latest_step_parses_and_ignores_optimizer(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("spark_train", ROOT / "bin" / "spark_train.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "mystyle_000000250.safetensors").write_text("x")
            (out / "mystyle_000000500.safetensors").write_text("x")
            (out / "optimizer_000000999.safetensors").write_text("x")
            self.assertEqual(mod._latest_step(out), 500)
            self.assertEqual(mod._latest_step(Path(d) / "nope"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
