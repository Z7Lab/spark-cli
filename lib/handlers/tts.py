"""tts handlers — on-Spark speech synthesis with Qwen3-TTS.

`say` syncs the bundled generator (`bin/tts_gen.py`) to the DGX, runs it in
the qwen-tts venv against the catalog model, and copies the .wav back. `pull_models`
fetches the model. Paths come from config (`tts_venv`, `tts_gen`), not hardcoded.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

from sparkcore import (
    REPO_ROOT, dim, cyan, ok, fail,
    ssh, _models_catalog, _run_pull,
)


def _model_entry():
    """The catalog tts.model entry, or exit with a hint."""
    catalog, _ = _models_catalog()
    entry = catalog.get("tts", {}).get("model")
    if not entry:
        print(fail("No 'tts.model' in the catalog (templates/models.json)."))
        sys.exit(1)
    return entry


def _model_dir(cfg):
    """Where the model lives on the DGX: models_dir/<name>."""
    return f"{cfg['models_dir']}/{_model_entry()['name']}"


def pull_models(params, cfg):
    """Download the Qwen3-TTS model `spark tts` needs (structure-preserving)."""
    e = _model_entry()
    jobs = [{"repo": e["repo_id"], "dest": f"{cfg['models_dir']}/{e['name']}",
             "glob": e["glob"], "label": e["label"]}]  # no flat: keep repo dir layout
    _run_pull(cfg, jobs, done_hint=f"Now try {cyan('spark tts say \"hello from the Spark\"')}.")
    return {"action": "tts.pull_models", "pulled": [j["dest"] for j in jobs]}


def say(params, cfg):
    """Synthesize speech from text on the Spark and download the .wav locally."""
    text = params["text"]
    out = Path(params["out"]).expanduser()
    model_dir = _model_dir(cfg)

    present = ssh(cfg, f"[ -f {shlex.quote(model_dir)}/model.safetensors ] && echo yes || echo no")
    if present.strip() != "yes":
        print(fail(f"Qwen3-TTS model not on the Spark ({model_dir})."))
        print(f"  Pull it first: {cyan('spark tts pull-models')}")
        sys.exit(1)

    # Sync the generator engine to the DGX (same pattern as the downloader).
    engine = REPO_ROOT / "bin" / "tts_gen.py"
    if subprocess.run(["scp", "-q", str(engine),
                       f"{cfg['dgx_user']}@{cfg['dgx_host']}:{cfg['tts_gen']}"]).returncode != 0:
        print(fail("Could not deploy the TTS generator to the DGX (scp failed)."))
        sys.exit(1)

    remote_wav = "/tmp/spark_tts_out.wav"
    cmd = (f"{shlex.quote(cfg['tts_venv'])}/bin/python {shlex.quote(cfg['tts_gen'])} "
           f"--model {shlex.quote(model_dir)} --text {shlex.quote(text)} "
           f"--out {remote_wav} --speaker {shlex.quote(params['speaker'])} "
           f"--instruct {shlex.quote(params['instruct'])} "
           f"--language {shlex.quote(params['language'])} > /tmp/spark_tts.log 2>&1")
    print(dim(f"Synthesizing on {cfg['dgx_host']} (speaker={params['speaker']})…"))
    if subprocess.run(["ssh", f"{cfg['dgx_user']}@{cfg['dgx_host']}", cmd]).returncode != 0:
        print(fail("tts_gen.py failed — last log lines:"))
        print(dim(ssh(cfg, "tail -8 /tmp/spark_tts.log")))
        sys.exit(1)

    out.parent.mkdir(parents=True, exist_ok=True)
    if subprocess.run(["scp", "-q", f"{cfg['dgx_user']}@{cfg['dgx_host']}:{remote_wav}",
                       str(out)]).returncode != 0:
        print(fail("Synthesis ran but copying the .wav back failed (scp)."))
        sys.exit(1)
    print(ok(f"Wrote {out}"))
    return {"action": "tts.say", "out": str(out), "text": text, "speaker": params["speaker"]}


HANDLERS = {
    "tts.say":         say,
    "tts.pull_models": pull_models,
}
