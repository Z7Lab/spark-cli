"""finetune handlers — Unsloth QLoRA LLM fine-tuning on the DGX.

Its own command domain (`spark finetune start/status/pause/resume`), mirroring `train`
(image LoRAs): both train a model on the dedicated box in a time-boxed, resumable screen
session and reuse the shared watchdog (bin/spark_watchdog.py). finetune produces a GGUF
that `spark llm serve` loads. Data sourcing (KB->pairs, RAG export) is upstream + out of
scope — spark consumes a standard `messages` JSONL, source-agnostic.
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from sparkcore import (
    REPO_ROOT, bold, dim, red, green, yellow, cyan, ok, warn, fail,
    ssh, ssh_screen, _docker_env, docker_probe, print_docker_remedy,
    _llm_instances, _comfy_mem_bytes, _human, _free_bytes,
)


# ── LLM fine-tune (Unsloth QLoRA on the DGX) ──────────────────────────────────────
#
# `spark finetune start <dataset.jsonl>` trains a small coder model with Unsloth
# QLoRA in an operator-provided, digest-pinned GB10 container, in a detached screen
# session — the LLM analogue of `spark train` (which trains image LoRAs). It reuses
# the generalized watchdog (bin/spark_watchdog.py) for the time-box / pause / resume
# / state machinery; the fragile GPU work lives in the in-container trainer
# (templates/finetune/unsloth_train.py). On completion it merges the LoRA, exports a
# GGUF (q4_k_m), and publishes it into models_dir for `spark llm serve` — keeping the
# adapter for incremental retrain. Data sourcing (KB→pairs, RAG export) is upstream
# and out of scope: spark consumes a standard `messages` JSONL, source-agnostic.

FINETUNE_SESSION = "spark-finetune"      # one dedicated session: the box fine-tunes, nothing else
_FT_ASSETS = REPO_ROOT / "templates" / "finetune"
_FT_VALID_ROLES = {"system", "user", "assistant", "tool"}
# Effective batch = batch × grad_accum; used both for the trainer and to derive the
# optimizer-step target the watchdog reports progress/ETA against.
_FT_BATCH, _FT_GRAD_ACCUM = 2, 4


def _ft_name_from_dataset(dataset: str) -> str:
    """A safe run/model name derived from the dataset filename."""
    stem = Path(dataset).expanduser().name
    stem = re.sub(r"\.jsonl?$", "", stem, flags=re.I) or "finetune"
    return re.sub(r"[^A-Za-z0-9_-]", "_", stem).strip("_") or "finetune"


def _ft_abs(cfg: dict, path: str) -> str:
    """Resolve a remote dir to absolute, expanding a leading ~ on the DGX (quoting
    in ssh commands blocks the remote shell's ~ expansion — mirrors train._abs)."""
    if not path.startswith("~"):
        return path.rstrip("/")
    out = ssh(cfg, f"readlink -m {path}").strip()    # unquoted: remote shell expands ~
    return (out or path).rstrip("/")


def _ft_resolved(cfg: dict) -> dict:
    """A cfg whose finetune_dir / models_dir are absolute (ssh only if a ~ is present)."""
    return dict(cfg, finetune_dir=_ft_abs(cfg, cfg["finetune_dir"]),
                models_dir=_ft_abs(cfg, cfg["models_dir"]))


def _ft_paths(cfg: dict, name: str) -> dict:
    root = cfg["finetune_dir"].rstrip("/")
    return {
        "root":     root,
        "datasets": f"{root}/datasets",
        "dataset":  f"{root}/datasets/{name}.jsonl",
        "eval":     f"{root}/datasets/{name}.eval.jsonl",
        "config":   f"{root}/configs/{name}.json",
        "output":   f"{root}/output/{name}",
        "gguf":     f"{root}/output/{name}/gguf",
        "adapter":  f"{root}/output/{name}/adapter",
        "state":    f"{root}/state/{name}.json",
        "control":  f"{root}/control/{name}",
        "log":      f"{root}/logs/{name}.log",
    }


def _ft_read_state(cfg: dict, name: str) -> dict | None:
    raw = ssh(cfg, f"cat {shlex.quote(_ft_paths(cfg, name)['state'])} 2>/dev/null || true")
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _ft_list_runs(cfg: dict) -> list[str]:
    root = cfg["finetune_dir"].rstrip("/")
    raw = ssh(cfg, f"ls -1 {shlex.quote(root)}/state/*.json 2>/dev/null || true")
    return sorted(Path(l).stem for l in raw.splitlines() if l.strip())


def _ft_session_running(cfg: dict) -> bool:
    return bool(ssh(cfg, f"screen -ls 2>/dev/null | grep -F {shlex.quote(FINETUNE_SESSION)} || true").strip())


def _ft_resolve_name(cfg: dict, name: str | None) -> str | None:
    """Pick the run to act on: the given name, the only run, or None (ambiguous)."""
    if name:
        return name
    runs = _ft_list_runs(cfg)
    if len(runs) == 1:
        return runs[0]
    if not runs:
        print(warn("No fine-tune runs yet.  Start one: ") + cyan("spark finetune start <dataset.jsonl>"))
    else:
        print(warn(f"Multiple runs ({', '.join(runs)}). Pass one, e.g. ")
              + cyan(f"spark finetune status {runs[0]}"))
    return None


def _validate_dataset(path: Path) -> tuple[int, list[str]]:
    """Strict `messages` JSONL validation, run on the host BEFORE the expensive
    container launch — hard-fail upfront. Returns (#usable rows, errors
    with line numbers). A bad line fails in seconds, not after a 20-min base load.
    """
    errors: list[str] = []
    n = 0
    try:
        lines = path.read_text().splitlines()
    except OSError as e:
        return 0, [f"cannot read {path}: {e}"]
    for lineno, line in enumerate(lines, 1):
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except ValueError as e:
            errors.append(f"line {lineno}: invalid JSON ({e})")
        else:
            msgs = obj.get("messages") if isinstance(obj, dict) else None
            if not isinstance(msgs, list) or not msgs:
                errors.append(f"line {lineno}: missing non-empty 'messages' array")
            else:
                bad = False
                for j, m in enumerate(msgs):
                    if not isinstance(m, dict):
                        errors.append(f"line {lineno}: messages[{j}] is not an object"); bad = True; break
                    if m.get("role") not in _FT_VALID_ROLES:
                        errors.append(f"line {lineno}: messages[{j}] role {m.get('role')!r} "
                                      f"not one of {sorted(_FT_VALID_ROLES)}"); bad = True; break
                    if not isinstance(m.get("content"), str) or not m["content"].strip():
                        errors.append(f"line {lineno}: messages[{j}] has empty/non-string content"); bad = True; break
                if not bad:
                    if not any(m.get("role") == "assistant" for m in msgs):
                        errors.append(f"line {lineno}: no assistant turn (nothing to train on)")
                    else:
                        n += 1
        if len(errors) >= 20:
            errors.append("… (further errors suppressed — fix these first)")
            break
    return n, errors


def _ft_scp(cfg: dict, local: Path, remote: str) -> bool:
    return subprocess.run(
        ["scp", "-q", str(local), f"{cfg['dgx_user']}@{cfg['dgx_host']}:{remote}"]
    ).returncode == 0


def _ft_deploy_assets(cfg: dict) -> None:
    """Ensure the fine-tune stack (dirs, compose, watchdog + trainer, .env) is on the DGX.

    spark deploys only the orchestration — the compose file, the shared watchdog,
    the finetune wrapper, the Unsloth trainer, and a .env pointing compose at the
    operator-provided image. It does NOT ship a Dockerfile: the Unsloth image is
    operator-provided and digest-pinned (cfg.unsloth_image), like the ComfyUI image.
    """
    if not (cfg.get("unsloth_image") or "").strip():
        print(fail("No Unsloth image configured (unsloth_image is unset)."))
        print("  spark drives an operator-provided GB10/sm_121 Unsloth image — it does not build one.")
        print(f"  Set it:  {cyan('spark config set unsloth_image <image[@sha256:…]>')}")
        print(dim("  Build reference: templates/finetune/Dockerfile.reference, or use a published GB10 image."))
        sys.exit(1)
    root = cfg["finetune_dir"].rstrip("/")
    ssh(cfg, "mkdir -p " + " ".join(shlex.quote(f"{root}/{d}") for d in
             ("datasets", "configs", "output", "state", "control", "logs", "bin", "cache/huggingface")))
    print(dim(f"Syncing fine-tune stack → {cfg['dgx_host']}:{root}"))
    pairs = [(_FT_ASSETS / "compose.yaml",        f"{root}/compose.yaml"),
             (REPO_ROOT / "bin" / "spark_watchdog.py", f"{root}/bin/spark_watchdog.py"),
             (REPO_ROOT / "bin" / "spark_finetune.py", f"{root}/bin/spark_finetune.py"),
             (_FT_ASSETS / "unsloth_train.py",     f"{root}/bin/unsloth_train.py")]
    for local, remote in pairs:
        if not _ft_scp(cfg, local, remote):
            print(fail(f"Could not deploy {local.name} to the DGX (scp failed).")); sys.exit(1)
    ssh(cfg, f"chmod +x {shlex.quote(root)}/bin/spark_finetune.py {shlex.quote(root)}/bin/unsloth_train.py")
    # compose reads {finetune_dir}/.env: the operator's Unsloth image + an HF token
    # ONLY if one is in spark's environment (needed for a gated base repo). Force
    # huggingface_hub's stable single-stream downloader for the in-container base
    # fetch (hf_transfer stalls on flaky links — stability > peak speed).
    import os
    env = f"UNSLOTH_IMAGE={cfg['unsloth_image']}\n"
    env += "HF_HUB_ENABLE_HF_TRANSFER=0\n"
    if os.environ.get("HF_TOKEN"):
        env += f"HF_TOKEN={os.environ['HF_TOKEN']}\n"
    ssh(cfg, f"printf %s {shlex.quote(env)} > {shlex.quote(root)}/.env")


def _ft_ensure_image(cfg: dict) -> None:
    """Make sure the operator-provided Unsloth image is present (pull if not).

    Mirrors `spark train`/`spark comfy`: spark drives a digest-pinned, operator-
    provided image, never builds it. If it can't be obtained, fail with a setup hint.
    """
    image = cfg["unsloth_image"]
    present = ssh(cfg, _docker_env(cfg) + f"docker images -q {shlex.quote(image)} 2>/dev/null || true")
    if present.strip():
        return
    print(dim(f"Pulling Unsloth image {cyan(image)} (operator-provided)…"))
    root = cfg["finetune_dir"].rstrip("/")
    cmd = _docker_env(cfg) + f"cd {shlex.quote(root)} && docker compose pull trainer 2>&1"
    rc = subprocess.run(["ssh", "-t", f"{cfg['dgx_user']}@{cfg['dgx_host']}", cmd]).returncode
    present = ssh(cfg, _docker_env(cfg) + f"docker images -q {shlex.quote(image)} 2>/dev/null || true")
    if rc != 0 or not present.strip():
        print(fail(f"Couldn't obtain the Unsloth image '{image}'."))
        print( "  spark drives an operator-provided, digest-pinned Unsloth image "
               "(like the ComfyUI image) — it does not build one.")
        print(f"  Point spark at a GB10/sm_121-compatible image:  "
              f"{cyan('spark config set unsloth_image <image@sha256:…>')}")
        print(dim("  To build your own, see templates/finetune/Dockerfile.reference."))
        sys.exit(1)
    print(ok("Unsloth image ready."))


def _ft_target_steps(n_examples: int, epochs: int) -> int:
    """Optimizer steps the run targets = ceil(n / effective_batch) × epochs (≥ 1).

    Used for progress/ETA and the watchdog's completion fallback; the trainer itself
    trains by epochs, so an off-by-a-bit estimate never changes what gets trained.
    """
    per_epoch = math.ceil(n_examples / (_FT_BATCH * _FT_GRAD_ACCUM))
    return max(1, per_epoch * max(1, epochs))


def _render_job(cfg: dict, name: str, params: dict, base: str) -> Path:
    """Render the Unsloth job-config JSON the in-container trainer consumes.

    In-container (compose-mounted) paths; the host never imports unsloth. The
    contract mirrors unsloth_train.py's _CONFIG_KEYS.
    """
    rank = params["rank"]
    job = {
        "name": name,
        "base": base,
        "dataset": f"/workspace/datasets/{name}.jsonl",
        "eval": f"/workspace/datasets/{name}.eval.jsonl" if params.get("eval") else None,
        "output": f"/workspace/output/{name}",
        "adapter_dir": f"/workspace/output/{name}/adapter",
        "gguf_dir": f"/workspace/output/{name}/gguf",
        "epochs": params["epochs"],
        "rank": rank,
        "alpha": rank * 2,                       # alpha = 2×rank
        "dropout": 0.05,
        "lr": params["lr"],
        "max_seq_len": params["max_seq_len"],
        "save_every": params["save_every"],
        "batch": _FT_BATCH,
        "grad_accum": _FT_GRAD_ACCUM,
        "quant": not params.get("no_quant"),     # QLoRA 4-bit on by default
        "gguf_quant": params["gguf_quant"],
    }
    out = Path("/tmp") / f"spark_finetune_{name}.json"
    out.write_text(json.dumps(job, indent=2))
    return out


def _ft_launch(cfg: dict, name: str, steps: int, max_seconds: int) -> None:
    """Start the watchdog (which runs the Unsloth trainer) in the dedicated session."""
    p = _ft_paths(cfg, name)
    root = shlex.quote(p["root"])
    run = (f"docker compose run --rm -T --name spark-finetune-{name} trainer "
           f"/workspace/bin/spark_finetune.py "
           f"--config /workspace/configs/{name}.json "
           f"--output /workspace/output/{name} "
           f"--state /workspace/state/{name}.json "
           f"--control /workspace/control/{name} "
           f"--max-seconds {max_seconds} --target-steps {steps}")
    inner = f"cd {root} && {_docker_env(cfg)}{run} 2>&1 | tee -a logs/{name}.log"
    ssh_screen(cfg, FINETUNE_SESSION, inner)


def _ft_publish(cfg: dict, name: str) -> str | None:
    """Copy the exported GGUF into models_dir/<name>/ for `spark llm serve`.

    Idempotent (re-publishing a current GGUF is a no-op copy). Returns the remote
    GGUF path, or None if the export produced nothing yet.
    """
    p = _ft_paths(cfg, name)
    gguf = ssh(cfg, f"ls -1 {shlex.quote(p['gguf'])}/*.gguf 2>/dev/null | sort | head -1").strip()
    if not gguf:
        return None
    dest_dir = f"{cfg['models_dir'].rstrip('/')}/{name}"
    target = f"{dest_dir}/{Path(gguf).name}"
    ssh(cfg, f"mkdir -p {shlex.quote(dest_dir)} && cp -f {shlex.quote(gguf)} {shlex.quote(target)}")
    return target


def _ft_free_box(cfg: dict, params: dict) -> None:
    """Memory pre-flight + opt-in `--free` (mirror `spark train start`).

    Fine-tuning wants the dedicated box (GB10 unified memory is shared). If ComfyUI
    or llama-servers are resident, surface it; --free stops them first (never automatic).
    """
    comfy_mem = _comfy_mem_bytes(cfg)
    llm_insts = _llm_instances(cfg)
    if not (comfy_mem or llm_insts):
        return
    holders = ([f"ComfyUI (~{_human(comfy_mem)})"] if comfy_mem else []) + \
              ([f"{len(llm_insts)} llama-server(s)"] if llm_insts else [])
    if params.get("free"):
        print(warn(f"Freeing the box for fine-tuning — stopping {', '.join(holders)}…"))
        if comfy_mem:
            from . import comfy as _comfy
            _comfy.stop({}, cfg)
        if llm_insts:
            from . import llm as _llm
            _llm.stop({}, cfg)
        time.sleep(2)
        print(dim(f"  {_human(_free_bytes(cfg))} free now."))
    else:
        print(warn(f"Box isn't dedicated to fine-tuning — {', '.join(holders)} resident "
                   f"({_human(_free_bytes(cfg))} free)."))
        print(dim("  Fine-tuning shares unified memory (slower / OOM risk). Free it with ")
              + cyan("--free") + dim(", or `spark comfy stop` / `spark llm stop`."))


def start(params, cfg):
    """Fine-tune a small LLM (Unsloth QLoRA) from a `messages` JSONL on the DGX."""
    cfg = _ft_resolved(cfg)
    dataset = Path(params["dataset"]).expanduser()
    name = params["name"] or _ft_name_from_dataset(str(dataset))
    epochs = params["epochs"]
    base = params["base"] or cfg["finetune_base_model"]
    max_hours = params["max_hours"]
    max_seconds = int(max_hours * 3600) if max_hours and max_hours > 0 else 0

    if not dataset.is_file():
        print(fail(f"Dataset file not found: {dataset}")); sys.exit(1)

    # Strict upfront validation — fail in seconds, not after a 20-min base load.
    n, errors = _validate_dataset(dataset)
    if errors:
        print(fail(f"Dataset failed validation ({dataset.name}) — fix these first:"))
        for e in errors:
            print(dim("  " + e))
        sys.exit(1)
    if n == 0:
        print(fail(f"No usable training examples in {dataset.name} (need rows with an assistant turn).")); sys.exit(1)
    if params.get("eval"):
        eval_path = Path(params["eval"]).expanduser()
        if not eval_path.is_file():
            print(fail(f"Eval file not found: {eval_path}")); sys.exit(1)
        en, eerr = _validate_dataset(eval_path)
        if eerr:
            print(fail(f"Eval set failed validation ({eval_path.name}):"))
            for e in eerr:
                print(dim("  " + e))
            sys.exit(1)

    # A dedicated box fine-tunes one model at a time — refuse to stomp a live session.
    if _ft_session_running(cfg):
        print(fail(f"A fine-tune session is already running ({FINETUNE_SESSION})."))
        print(f"  Inspect it: {cyan('spark finetune status')}   pause it: {cyan('spark finetune pause')}")
        sys.exit(1)

    state, _ = docker_probe(cfg)
    if state != "ok":
        print(fail("Cannot fine-tune — Docker is not usable on the DGX:"))
        print_docker_remedy(cfg, state); sys.exit(1)

    _ft_free_box(cfg, params)

    steps = _ft_target_steps(n, epochs)
    quant_label = "QLoRA 4-bit" if not params.get("no_quant") else "full-precision LoRA"
    print(bold(f"Fine-tune '{name}'")
          + dim(f"  ({n} examples, {epochs} epoch(s), rank {params['rank']}, {quant_label})"))
    print(dim(f"  base {base}  →  GGUF ({params['gguf_quant']}) + adapter"))

    _ft_deploy_assets(cfg)
    _ft_ensure_image(cfg)

    p = _ft_paths(cfg, name)
    ssh(cfg, f"mkdir -p {shlex.quote(p['datasets'])}")
    print(dim(f"Staging dataset → {p['dataset']}"))
    if not _ft_scp(cfg, dataset, p["dataset"]):
        print(fail("Could not stage the dataset to the DGX.")); sys.exit(1)
    if params.get("eval"):
        _ft_scp(cfg, Path(params["eval"]).expanduser(), p["eval"])

    local_job = _render_job(cfg, name, params, base)
    if not _ft_scp(cfg, local_job, p["config"]):
        print(fail("Could not upload the job config.")); sys.exit(1)
    ssh(cfg, f"rm -f {shlex.quote(p['control'])}/stop 2>/dev/null || true")

    _ft_launch(cfg, name, steps, max_seconds)
    budget = f"{max_hours:g}h" if max_seconds else "until target epochs"
    print(ok(f"Fine-tune started in screen session {cyan(FINETUNE_SESSION)}."))
    print(f"  Run:    {cyan(name)}   ~{steps} steps ({epochs} epoch(s))   save every {params['save_every']}   budget {budget}")
    print(f"  Watch:  {cyan('spark finetune status ' + name)}   logs: {cyan('spark finetune status ' + name + ' --logs')}")
    print(f"  Pause:  {cyan('spark finetune pause ' + name)}   (stops cleanly after the next checkpoint)")
    return {"action": "finetune.start", "name": name, "base": base, "examples": n,
            "epochs": epochs, "target_steps": steps, "rank": params["rank"],
            "quant": not params.get("no_quant"), "gguf_quant": params["gguf_quant"],
            "max_seconds": max_seconds}


def pause(params, cfg):
    """Request a clean stop after the next checkpoint (resumable)."""
    cfg = _ft_resolved(cfg)
    name = _ft_resolve_name(cfg, params["name"])
    if not name:
        return {"action": "finetune.pause", "paused": False}
    if not _ft_session_running(cfg):
        print(dim("No fine-tune session is running — nothing to pause."))
        return {"action": "finetune.pause", "name": name, "paused": False}
    p = _ft_paths(cfg, name)
    ssh(cfg, f"mkdir -p {shlex.quote(p['control'])} && touch {shlex.quote(p['control'])}/stop")
    print(ok(f"Pause requested for {cyan(name)}."))
    print(dim("  The run stops right after the next checkpoint completes (never mid-save)."))
    print(f"  Resume later: {cyan('spark finetune resume ' + name)}")
    return {"action": "finetune.pause", "name": name, "paused": True}


def resume(params, cfg):
    """Resume a paused fine-tune from its latest checkpoint."""
    cfg = _ft_resolved(cfg)
    name = _ft_resolve_name(cfg, params["name"])
    if not name:
        return {"action": "finetune.resume", "resumed": False}
    if _ft_session_running(cfg):
        print(warn(f"A fine-tune session is already running — {cyan('spark finetune status')} to inspect it."))
        return {"action": "finetune.resume", "name": name, "resumed": False}
    st = _ft_read_state(cfg, name)
    if not st:
        print(fail(f"No run state for '{name}'. Start it with {cyan('spark finetune start')}."))
        sys.exit(1)
    if st.get("status") == "complete":
        print(ok(f"'{name}' is already complete (step {st.get('current_step')}). Nothing to resume."))
        return {"action": "finetune.resume", "name": name, "resumed": False}
    steps = st.get("target_steps") or 1
    max_hours = params["max_hours"]
    max_seconds = int(max_hours * 3600) if max_hours and max_hours > 0 else int(st.get("max_seconds") or 0)
    state, _ = docker_probe(cfg)
    if state != "ok":
        print(fail("Cannot resume — Docker is not usable on the DGX:")); print_docker_remedy(cfg, state); sys.exit(1)
    p = _ft_paths(cfg, name)
    ssh(cfg, f"rm -f {shlex.quote(p['control'])}/stop 2>/dev/null || true")
    _ft_deploy_assets(cfg)
    _ft_ensure_image(cfg)
    _ft_launch(cfg, name, steps, max_seconds)
    budget = f"{max_seconds/3600:g}h" if max_seconds else "until target epochs"
    print(ok(f"Resumed {cyan(name)} from step {st.get('current_step', 0)} (budget {budget})."))
    print(f"  Watch: {cyan('spark finetune status ' + name)}")
    return {"action": "finetune.resume", "name": name, "resumed": True,
            "from_step": st.get("current_step", 0), "max_seconds": max_seconds}


def status(params, cfg):
    """Show a fine-tune run's progress, ETA, and the published GGUF."""
    cfg = _ft_resolved(cfg)
    if params.get("logs"):
        name = _ft_resolve_name(cfg, params["name"])
        if not name:
            return {"action": "finetune.status"}
        log = _ft_paths(cfg, name)["log"]
        print(dim(f"Tailing {log} — Ctrl+C to stop\n"))
        subprocess.run(["ssh", f"{cfg['dgx_user']}@{cfg['dgx_host']}", f"tail -n 80 -f {shlex.quote(log)}"])
        return {"action": "finetune.status", "name": name, "logs": True}

    name = _ft_resolve_name(cfg, params["name"])
    if not name:
        return {"action": "finetune.status", "runs": _ft_list_runs(cfg)}
    st = _ft_read_state(cfg, name)
    running = _ft_session_running(cfg)
    if not st:
        print(f"  {bold(name)}  {dim('no state yet')}  {('(session live)' if running else '')}")
        return {"action": "finetune.status", "name": name, "running": running, "state": None}

    status_str = st.get("status", "?")
    cur, tgt = st.get("current_step", 0), st.get("target_steps", 0)
    pct = f"{100*cur/tgt:.0f}%" if tgt else "—"
    sym = {"training": green("● training"), "stopping": yellow("◐ stopping after next checkpoint"),
           "paused": yellow("⏸ paused"), "complete": green("✓ complete"),
           "error": red("✗ error")}.get(status_str, status_str)
    print(f"\n  {bold(name)}   {sym}{dim('  (session live)') if running else ''}")
    print(f"    steps      {cur} / {tgt}   {dim(pct)}")
    if st.get("resumed_from_step"):
        print(f"    resumed    from step {st['resumed_from_step']}")
    elapsed = st.get("elapsed_seconds")
    if elapsed:
        print(f"    elapsed    {elapsed//60}m{elapsed%60:02d}s")
        if status_str == "training" and cur > st.get("resumed_from_step", 0) and tgt:
            done = cur - st.get("resumed_from_step", 0)
            eta = int(elapsed / done * (tgt - cur)) if done else 0
            print(f"    eta        ~{eta//60}m to target {dim('(this session, at current rate)')}")
    if st.get("max_seconds"):
        print(f"    budget     {st['max_seconds']//3600}h{dim(' — auto-stops after a checkpoint past the budget')}")
    if st.get("stop_reason"):
        print(f"    last stop  {dim(st['stop_reason'])}")

    # A live run still at step 0 is in the base-model phase (first run fetches +
    # loads the base into the HF cache — minutes for a multi-GB coder). Surface it
    # with the cache size as a download proxy, so "0/N" doesn't read as stuck.
    if running and cur == 0 and status_str not in ("complete", "error"):
        cache = ssh(cfg, f"du -sh {shlex.quote(cfg['finetune_dir'])}/cache/huggingface 2>/dev/null | cut -f1").strip()
        hint = "downloading / loading base model" + (f" — HF cache {cache}" if cache else "")
        print(f"    preparing  {dim(hint)}")

    published = None
    if status_str == "complete":
        published = _ft_publish(cfg, name)
    if published:
        print(f"    gguf       {ok(published)}")
        print(f"    adapter    {dim(_ft_paths(cfg, name)['adapter'] + '  (retained for incremental retrain)')}")
        print(f"    serve it:  {cyan('spark llm serve ' + name)}")
    elif status_str == "complete":
        print(f"    {warn('complete but no GGUF found to publish')}")
        print(dim(f"      the adapter may still be saved at {_ft_paths(cfg, name)['adapter']} "
                  f"(GGUF export can fail if the image lacks a llama.cpp converter — see the logs)."))
    elif not running and status_str in ("paused", "stopping"):
        print(f"    {dim('resume:')} {cyan('spark finetune resume ' + name)}")
    elif running and status_str == "training":
        print(f"    {dim('pause:')}  {cyan('spark finetune pause ' + name)}"
              f"  {dim('(stops cleanly after the next checkpoint)')}")
    return {"action": "finetune.status", "name": name, "running": running,
            "state": st, "published": published}


HANDLERS = {
    "finetune.start":  start,
    "finetune.status": status,
    "finetune.pause":  pause,
    "finetune.resume": resume,
}
