"""transcribe handlers — start, stop, status, logs, pull-models (whisper-server)."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from sparkcore import (
    bold, dim, red, green, yellow, cyan, ok, warn, fail,
    ssh, _models_catalog, _run_pull,
)


def start(params, cfg):
    """Start whisper-server (default: large-v3, port 8081)."""
    whisper_bin = cfg["whisper_bin"]
    whisper_log = cfg["whisper_log"]
    models_dir  = cfg["whisper_models_dir"]
    model = params["model"]
    port  = params["port"]

    model_file = ssh(cfg, f"find {models_dir} -name 'ggml-{model}.bin' 2>/dev/null | head -1")
    if not model_file:
        print(fail(f"Model ggml-{model}.bin not found in {models_dir}"))
        print(f"  Download it with: {cyan(f'spark transcribe pull-models --model {model}')}")
        sys.exit(1)

    existing = ssh(cfg, "pgrep whisper-server || true")
    if existing:
        print(yellow(f"⚠ Stopping existing whisper-server (pid {existing.split()[0]})..."))
        ssh(cfg, "pkill whisper-server || true")
        time.sleep(1)

    print(f"Starting whisper-server  {cyan(model)}  on port {port}...")
    # Serve whisper.cpp's inference handler at the OpenAI transcription path
    # so OpenAI-compatible clients work — whisper.cpp has no native /v1 route,
    # but its /inference handler accepts the same multipart 'file' upload and
    # returns {"text": ...}. --inference-path relocates it to the OpenAI path.
    serve_cmd = (f"nohup {whisper_bin} -m {model_file} --port {port} --host 0.0.0.0 "
                 f"--inference-path /v1/audio/transcriptions > {whisper_log} 2>&1 &")
    ssh(cfg, serve_cmd, capture=False)

    for i in range(15):
        time.sleep(2)
        health = ssh(cfg, f"curl -sf http://localhost:{port}/health || echo unreachable")
        if "ok" in health.lower() or "whisper" in health.lower():
            print(f"\n  {ok('whisper-server ready')}")
            endpoint = f"http://{cfg['dgx_host']}:{port}/v1/audio/transcriptions"
            print(f"  Endpoint  {cyan(endpoint)}")
            print(f"  mdkb config: audio_provider=remote, audio_api_base=http://{cfg['dgx_host']}:{port}")
            return {"action": "transcribe.start", "model": model, "port": port,
                    "status": "ready", "endpoint": endpoint}
        print(f"\r  Starting... ({(i+1)*2}s)", end="", flush=True)
    print(f"\n  {warn('Still starting')} — run: {cyan('spark transcribe logs')}")
    return {"action": "transcribe.start", "model": model, "port": port,
            "status": "starting"}


def stop(params, cfg):
    """Stop the running whisper-server."""
    pid = ssh(cfg, "pgrep whisper-server || true")
    if not pid:
        print(dim("whisper-server is not running."))
        return {"action": "transcribe.stop", "stopped": False}
    ssh(cfg, "pkill whisper-server || true")
    print(ok("whisper-server stopped."))
    return {"action": "transcribe.stop", "stopped": True}


def status(params, cfg):
    """Show whisper-server state and endpoint URL."""
    port = params["port"]
    pid  = ssh(cfg, "pgrep whisper-server | head -1 || true")
    state = {"action": "transcribe.status", "port": port, "pid": pid or None,
             "running": bool(pid), "model": None, "ready": False}
    if pid:
        print(f"  Transcription  {ok('whisper-server')}  (pid {pid})")
        model_line = ssh(cfg, f"ps -p {pid} -o args= 2>/dev/null || true")
        if "-m " in model_line:
            mfile = model_line.split("-m ")[1].strip().split()[0]
            state["model"] = Path(mfile).stem
            print(f"  Model          {cyan(Path(mfile).stem)}")
        health = ssh(cfg, f"curl -sf http://localhost:{port}/health || echo unreachable")
        if "ok" in health.lower() or "whisper" in health.lower():
            endpoint = f"http://{cfg['dgx_host']}:{port}/v1/audio/transcriptions"
            state["ready"] = True
            state["endpoint"] = endpoint
            print(f"  Endpoint       {ok(endpoint)}")
        else:
            print(f"  Endpoint       {warn('still starting...')}")
    else:
        print(f"  Transcription  {dim('not running')}  Run: {cyan('spark transcribe start')}")
    return state


def logs(params, cfg):
    """Tail the whisper-server log. Ctrl+C to exit."""
    whisper_log = cfg["whisper_log"]
    lines = params["lines"]
    print(dim(f"Tailing whisper-server log — Ctrl+C to stop\n"))
    subprocess.run(["ssh", f"{cfg['dgx_user']}@{cfg['dgx_host']}",
                    f"tail -n {lines} -f {whisper_log}"])
    return {"action": "transcribe.logs", "log": whisper_log}


def pull_models(params, cfg):
    """Download whisper ggml model(s) from the catalog into whisper_models_dir."""
    catalog, _ = _models_catalog()
    models = catalog.get("whisper", {}).get("models", [])
    names = [m["model"] for m in models]
    pick = params["model"]
    grab_all = params["all"]

    if not grab_all and pick not in names:
        print("spark transcribe pull-models [--model <name>|--all]")
        print(f"\n  Download whisper ggml model(s) into the DGX's whisper models dir.")
        print(f"  Available: {', '.join(names)}  (default: large-v3)")
        print(red(f"\n  Unknown --model '{pick}'"))
        return {"action": "transcribe.pull_models", "listed": names}

    chosen = models if grab_all else [m for m in models if m["model"] == pick]
    dest = cfg["whisper_models_dir"]
    jobs = [{"repo": m["repo_id"], "dest": dest, "glob": m["glob"], "label": m["label"]}
            for m in chosen]
    _run_pull(cfg, jobs, done_hint=f"Start it with {cyan('spark transcribe start')}.")
    return {"action": "transcribe.pull_models", "pulled": [m["model"] for m in chosen]}


HANDLERS = {
    "transcribe.start":       start,
    "transcribe.stop":        stop,
    "transcribe.status":      status,
    "transcribe.logs":        logs,
    "transcribe.pull_models": pull_models,
}
