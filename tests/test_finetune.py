"""Unit tests for the LLM fine-tune handlers (no DGX required).

The finetune handlers drive Unsloth over SSH; these tests monkeypatch the SSH /
session probes (so nothing touches a real DGX) and cover the pure helpers — the
strict dataset validator, the job-config renderer, step math, publish — plus the
early-return control paths. The in-container finetune watchdog
(bin/spark_finetune.py) is exercised for its HF-checkpoint-dir step parsing.

Run: python3 tests/test_finetune.py
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "lib"))

from handlers import finetune as ft  # noqa: E402


class TestFinetuneHelpers(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "dgx_host": "dgx.local", "dgx_user": "spark", "port": 8080,
            # absolute so _ft_resolved() is a no-op (no ssh) in these unit tests
            "finetune_dir": "/srv/spark-finetune", "models_dir": "/srv/models",
            "finetune_base_model": "unsloth/Qwen2.5-Coder-7B-Instruct-bnb-4bit",
            "unsloth_image": "my-unsloth:gb10",
        }
        self._orig = {k: getattr(ft, k) for k in
                      ("ssh", "_ft_session_running", "_ft_read_state", "_ft_list_runs")}

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(ft, k, v)

    def _call(self, fn, params):
        buf = io.StringIO()
        with redirect_stdout(buf):
            return fn(params, self.cfg)

    def test_name_from_dataset_strips_ext_and_sanitises(self):
        self.assertEqual(ft._ft_name_from_dataset("/tmp/House Style!.jsonl"), "House_Style")
        self.assertEqual(ft._ft_name_from_dataset("/tmp/pairs.jsonl"), "pairs")
        self.assertEqual(ft._ft_name_from_dataset("/tmp/pairs.json"), "pairs")

    def test_validate_dataset_accepts_good_rows(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"}]}) + "\n")
            f.write("\n")  # blank lines tolerated
            f.write(json.dumps({"messages": [
                {"role": "system", "content": "be terse"},
                {"role": "user", "content": "2+2"},
                {"role": "assistant", "content": "4"}]}) + "\n")
            path = Path(f.name)
        n, errors = ft._validate_dataset(path)
        self.assertEqual(errors, [])
        self.assertEqual(n, 2)

    def test_validate_dataset_reports_line_numbers(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "ok"}]}) + "\n")   # line 1 good
            f.write("not json\n")                                    # line 2 bad JSON
            f.write(json.dumps({"messages": []}) + "\n")             # line 3 empty messages
            f.write(json.dumps({"messages": [
                {"role": "user", "content": "no answer"}]}) + "\n")  # line 4 no assistant
            f.write(json.dumps({"messages": [
                {"role": "user", "content": "x"},
                {"role": "bot", "content": "y"}]}) + "\n")           # line 5 bad role
            path = Path(f.name)
        n, errors = ft._validate_dataset(path)
        self.assertEqual(n, 1)
        joined = "\n".join(errors)
        self.assertIn("line 2", joined)
        self.assertIn("line 3", joined)
        self.assertIn("line 4", joined)
        self.assertIn("line 5", joined)

    def test_target_steps_math(self):
        # effective batch = 2×4 = 8 → ceil(20/8)=3 steps/epoch × 3 epochs = 9
        self.assertEqual(ft._ft_target_steps(20, 3), 9)
        self.assertEqual(ft._ft_target_steps(1, 1), 1)     # never zero
        self.assertEqual(ft._ft_target_steps(0, 5), 1)     # floor at 1 (degenerate empty set)
        self.assertEqual(ft._ft_target_steps(16, 2), 4)    # ceil(16/8)=2 × 2 epochs

    def test_render_job_contract(self):
        params = {"epochs": 2, "rank": 64, "lr": 2e-4, "max_seq_len": 2048,
                  "save_every": 50, "gguf_quant": "q4_k_m", "eval": None, "no_quant": False}
        p = ft._render_job(self.cfg, "house", params, "unsloth/Qwen2.5-Coder-7B-Instruct-bnb-4bit")
        job = json.loads(p.read_text())
        self.assertEqual(job["name"], "house")
        self.assertEqual(job["base"], "unsloth/Qwen2.5-Coder-7B-Instruct-bnb-4bit")
        self.assertEqual(job["alpha"], 128)                 # alpha = 2×rank
        self.assertTrue(job["quant"])                       # QLoRA 4-bit default
        self.assertIsNone(job["eval"])
        self.assertEqual(job["dataset"], "/workspace/datasets/house.jsonl")
        self.assertEqual(job["gguf_dir"], "/workspace/output/house/gguf")

    def test_render_job_no_quant_and_eval(self):
        params = {"epochs": 3, "rank": 32, "lr": 1e-4, "max_seq_len": 4096,
                  "save_every": 25, "gguf_quant": "q5_k_m", "eval": "/x.jsonl", "no_quant": True}
        job = json.loads(ft._render_job(self.cfg, "h", params, "base/x").read_text())
        self.assertFalse(job["quant"])                      # --no-quant → full-precision LoRA
        self.assertEqual(job["alpha"], 64)
        self.assertEqual(job["eval"], "/workspace/datasets/h.eval.jsonl")
        self.assertEqual(job["gguf_quant"], "q5_k_m")

    def test_paths_layout(self):
        p = ft._ft_paths(self.cfg, "house")
        self.assertEqual(p["dataset"], "/srv/spark-finetune/datasets/house.jsonl")
        self.assertEqual(p["gguf"], "/srv/spark-finetune/output/house/gguf")
        self.assertEqual(p["state"], "/srv/spark-finetune/state/house.json")

    def test_pause_no_session(self):
        ft._ft_session_running = lambda cfg: False
        r = self._call(ft.pause, {"name": "house"})
        self.assertEqual(r, {"action": "finetune.pause", "name": "house", "paused": False})

    def test_resume_already_complete(self):
        ft._ft_session_running = lambda cfg: False
        ft._ft_read_state = lambda cfg, name: {"status": "complete", "current_step": 9}
        r = self._call(ft.resume, {"name": "house", "max_hours": None})
        self.assertFalse(r["resumed"])

    def test_status_ambiguous_lists_runs(self):
        ft._ft_list_runs = lambda cfg: ["a", "b"]
        ft._ft_session_running = lambda cfg: False
        r = self._call(ft.status, {"name": None, "logs": False})
        self.assertEqual(r["action"], "finetune.status")
        self.assertEqual(r["runs"], ["a", "b"])

    def test_publish_copies_exported_gguf(self):
        calls = []

        def fake_ssh(cfg, cmd, **kw):
            calls.append(cmd)
            if "cp -f" in cmd:
                return ""
            if cmd.strip().startswith("ls -1") and "*.gguf" in cmd:
                return "/srv/spark-finetune/output/house/gguf/house.q4_k_m.gguf"
            return ""
        ft.ssh = fake_ssh
        target = ft._ft_publish(self.cfg, "house")
        self.assertEqual(target, "/srv/models/house/house.q4_k_m.gguf")
        self.assertTrue(any("cp -f" in c for c in calls))

    def test_publish_none_when_no_gguf(self):
        ft.ssh = lambda cfg, cmd, **kw: ""
        self.assertIsNone(ft._ft_publish(self.cfg, "house"))


class TestFinetuneWatchdog(unittest.TestCase):
    def _load(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("spark_finetune", ROOT / "bin" / "spark_finetune.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_latest_step_parses_checkpoint_dirs(self):
        mod = self._load()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "checkpoint-50").mkdir()
            (out / "checkpoint-100").mkdir()
            (out / "checkpoint-not").mkdir()       # ignored (no number)
            (out / "adapter").mkdir()              # ignored (not a checkpoint)
            self.assertEqual(mod._latest_step(out), 100)
            self.assertEqual(mod._latest_step(Path(d) / "nope"), 0)

    def test_stable_latest_sums_shard_sizes(self):
        mod = self._load()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            ck = out / "checkpoint-100"
            ck.mkdir()
            (ck / "adapter_model.safetensors").write_bytes(b"x" * 10)
            (ck / "extra.safetensors").write_bytes(b"y" * 5)
            (ck / "trainer_state.json").write_text("{}")   # not counted
            step, size = mod._stable_latest(out)
            self.assertEqual(step, 100)
            self.assertEqual(size, 15)


if __name__ == "__main__":
    unittest.main(verbosity=2)
