"""train handlers — FLUX.2 style-LoRA training on the dedicated DGX.

`spark train start/pause/resume/status` drive ai-toolkit (ostris) inside a
dedicated CUDA-13/sm_121 container (templates/train/), in a detached screen
session (like `serve`), over SSH. A run is pausable + resumable in time-boxed
sessions: `--max-hours` auto-stops cleanly just after the next checkpoint, and
resume continues from the latest checkpoint until the configured step target.
The trained LoRA is published into ComfyUI's models/loras for `comfy generate
--lora`.

The fragile bits (the time-budget safe-stop, checkpoint detection) live in the
in-container watchdog bin/spark_train.py; this host-side module deploys assets,
stages the corpus, renders the ai-toolkit config, launches/controls the screen
session, and reports progress.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from sparkcore import (
    REPO_ROOT, bold, dim, red, green, yellow, cyan, ok, warn, fail,
    ssh, ssh_screen, _docker_env, docker_probe, print_docker_remedy,
)

TRAIN_SESSION = "spark-train"          # one dedicated session: the box trains, nothing else
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff")
_ASSETS = REPO_ROOT / "templates" / "train"


# ── Remote layout / state ────────────────────────────────────────────────────────

def _name_from_corpus(corpus: str) -> str:
    """A safe run name derived from the corpus directory name."""
    stem = Path(corpus).expanduser().resolve().name or "style"
    return re.sub(r"[^A-Za-z0-9_-]", "_", stem).strip("_") or "style"


def _paths(cfg: dict, name: str) -> dict:
    root = cfg["train_dir"].rstrip("/")
    return {
        "root":     root,
        "compose":  f"{root}/compose.yaml",
        "dataset":  f"{root}/datasets/{name}",
        "config":   f"{root}/configs/{name}.yaml",
        "output":   f"{root}/output/{name}",
        "state":    f"{root}/state/{name}.json",
        "control":  f"{root}/control/{name}",
        "log":      f"{root}/logs/{name}.log",
    }


def _loras_dir(cfg: dict) -> str:
    return f"{cfg['comfy_dir'].rstrip('/')}/workspace/models/loras"


def _abs(cfg: dict, path: str) -> str:
    """Resolve a remote dir to an absolute path, expanding a leading ~ on the DGX.

    Paths under train_dir/comfy_dir get shlex.quote'd into ssh commands, and quoting
    blocks the remote shell's ~ expansion — so a ~-relative dir must be resolved to
    absolute first (else `mkdir`/`scp`/`cat` hit a literal '~' directory).
    """
    if not path.startswith("~"):
        return path.rstrip("/")
    out = ssh(cfg, f"readlink -m {path}").strip()   # unquoted: remote shell expands ~
    return (out or path).rstrip("/")


def _resolved(cfg: dict) -> dict:
    """A cfg whose train_dir/comfy_dir are absolute (one ssh only if a ~ is present)."""
    return dict(cfg, train_dir=_abs(cfg, cfg["train_dir"]),
                comfy_dir=_abs(cfg, cfg["comfy_dir"]))


def _read_state(cfg: dict, name: str) -> dict | None:
    raw = ssh(cfg, f"cat {shlex.quote(_paths(cfg, name)['state'])} 2>/dev/null || true")
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _list_runs(cfg: dict) -> list[str]:
    root = cfg["train_dir"].rstrip("/")
    raw = ssh(cfg, f"ls -1 {shlex.quote(root)}/state/*.json 2>/dev/null || true")
    return sorted(Path(l).stem for l in raw.splitlines() if l.strip())


def _session_running(cfg: dict) -> bool:
    return bool(ssh(cfg, f"screen -ls 2>/dev/null | grep -F {shlex.quote(TRAIN_SESSION)} || true").strip())


def _resolve_name(cfg: dict, name: str | None) -> str | None:
    """Pick the run to act on: the given name, the only run, or None (ambiguous)."""
    if name:
        return name
    runs = _list_runs(cfg)
    if len(runs) == 1:
        return runs[0]
    if not runs:
        print(warn("No training runs yet.  Start one: ") + cyan("spark train start <corpus> --trigger <word>"))
    else:
        print(warn(f"Multiple runs ({', '.join(runs)}). Pass one, e.g. ")
              + cyan(f"spark train status {runs[0]}"))
    return None


# ── Deploy / launch ────────────────────────────────────────────────────────────

def _scp(cfg: dict, local: Path, remote: str) -> bool:
    return subprocess.run(
        ["scp", "-q", str(local), f"{cfg['dgx_user']}@{cfg['dgx_host']}:{remote}"]
    ).returncode == 0


def _deploy_assets(cfg: dict) -> None:
    """Ensure the training stack (dirs, compose, watchdog, .env) is on the DGX.

    spark deploys only the orchestration — the compose file, the watchdog, and a
    .env that points compose at the operator-provided image and the comfy base
    weights. It does NOT ship a Dockerfile: the ai-toolkit image is operator-
    provided and digest-pinned (cfg.aitoolkit_image), like the ComfyUI image.
    """
    p = _paths(cfg, "_")
    root = p["root"]
    ssh(cfg, "mkdir -p " + " ".join(shlex.quote(f"{root}/{d}") for d in
             ("datasets", "configs", "output", "state", "control", "logs", "bin", "cache/huggingface")))
    print(dim(f"Syncing training stack → {cfg['dgx_host']}:{root}"))
    pairs = [(_ASSETS / "compose.yaml", f"{root}/compose.yaml"),
             (REPO_ROOT / "bin" / "spark_train.py", f"{root}/bin/spark_train.py")]
    for local, remote in pairs:
        if not _scp(cfg, local, remote):
            print(fail(f"Could not deploy {local.name} to the DGX (scp failed).")); sys.exit(1)
    ssh(cfg, f"chmod +x {shlex.quote(root)}/bin/spark_train.py")
    # compose reads {train_dir}/.env: the operator's ai-toolkit image, and an HF
    # token ONLY if one is in spark's environment (needed only for a gated base like
    # the FLUX.2-dev opt-in; the default klein-4B base is ungated). ai-toolkit fetches
    # the base into the mounted HF cache itself.
    import os
    env = f"AITOOLKIT_IMAGE={cfg['aitoolkit_image']}\n"
    if os.environ.get("HF_TOKEN"):
        env += f"HF_TOKEN={os.environ['HF_TOKEN']}\n"
    ssh(cfg, f"printf %s {shlex.quote(env)} > {shlex.quote(root)}/.env")


def _ensure_image(cfg: dict) -> None:
    """Make sure the operator-provided ai-toolkit image is present (pull if not).

    Mirrors how `spark comfy start` relies on a digest-pinned, operator-provided
    image — spark drives it, never builds it. If it can't be obtained, fail with a
    setup hint instead of trying to vendor ai-toolkit's brittle GPU dep tree.
    """
    image = cfg["aitoolkit_image"]
    present = ssh(cfg, _docker_env(cfg) + f"docker images -q {shlex.quote(image)} 2>/dev/null || true")
    if present.strip():
        return
    print(dim(f"Pulling ai-toolkit image {cyan(image)} (operator-provided)…"))
    root = cfg["train_dir"].rstrip("/")
    cmd = _docker_env(cfg) + f"cd {shlex.quote(root)} && docker compose pull trainer 2>&1"
    rc = subprocess.run(["ssh", "-t", f"{cfg['dgx_user']}@{cfg['dgx_host']}", cmd]).returncode
    present = ssh(cfg, _docker_env(cfg) + f"docker images -q {shlex.quote(image)} 2>/dev/null || true")
    if rc != 0 or not present.strip():
        print(fail(f"Couldn't obtain the ai-toolkit image '{image}'."))
        print( "  spark drives an operator-provided, digest-pinned ai-toolkit image "
               "(like the ComfyUI image) — it does not build one.")
        print(f"  Point spark at a GB10/sm_121-compatible image:  "
              f"{cyan('spark config set aitoolkit_image <image@sha256:…>')}")
        print(dim("  To build your own, see templates/train/Dockerfile.reference. "
                  "Image contract: python3 + ai-toolkit run.py (AITOOLKIT_RUN)."))
        sys.exit(1)
    print(ok("ai-toolkit image ready."))


def _render_config(cfg: dict, name: str, trigger: str, steps: int,
                   save_every: int, rank: int, resolution: int) -> Path:
    tmpl = (_ASSETS / "aitoolkit_config.yaml").read_text()
    arch = cfg["train_arch"]
    quantize = "false" if "klein" in arch else "true"   # 4B fits unquantized; quantize 32B dev
    subs = {
        "@@NAME@@": name, "@@TRIGGER@@": trigger, "@@STEPS@@": str(steps),
        "@@SAVE_EVERY@@": str(save_every), "@@RANK@@": str(rank),
        "@@RESOLUTION@@": str(resolution), "@@BASE_MODEL@@": cfg["train_base_model"],
        "@@ARCH@@": arch, "@@QUANTIZE@@": quantize,
    }
    for k, v in subs.items():
        tmpl = tmpl.replace(k, v)
    out = Path("/tmp") / f"spark_train_{name}.yaml"
    out.write_text(tmpl)
    return out


def _launch(cfg: dict, name: str, steps: int, max_seconds: int) -> None:
    """Start the watchdog (which runs ai-toolkit) in the dedicated screen session."""
    p = _paths(cfg, name)
    root = shlex.quote(p["root"])
    run = (f"docker compose run --rm -T --name spark-train-{name} trainer "
           f"/workspace/bin/spark_train.py "
           f"--config /workspace/configs/{name}.yaml "
           f"--output /workspace/output/{name} "
           f"--state /workspace/state/{name}.json "
           f"--control /workspace/control/{name} "
           f"--max-seconds {max_seconds} --target-steps {steps}")
    inner = (f"cd {root} && {_docker_env(cfg)}{run} 2>&1 | tee -a logs/{name}.log")
    ssh_screen(cfg, TRAIN_SESSION, inner)


# ── Verbs ────────────────────────────────────────────────────────────────────────

def start(params, cfg):
    """Train a FLUX.2 style LoRA from a corpus directory on the dedicated DGX."""
    cfg = _resolved(cfg)
    corpus = Path(params["corpus"]).expanduser()
    trigger = params["trigger"]
    name = params["name"] or _name_from_corpus(str(corpus))
    steps = params["steps"]
    save_every = params["save_every"]
    rank = params["rank"]
    resolution = params["resolution"]
    max_hours = params["max_hours"]
    max_seconds = int(max_hours * 3600) if max_hours and max_hours > 0 else 0

    if not corpus.is_dir():
        print(fail(f"Corpus directory not found: {corpus}")); sys.exit(1)
    imgs = [f for f in corpus.iterdir() if f.suffix.lower() in _IMG_EXTS]
    if not imgs:
        print(fail(f"No images in {corpus} (looked for {', '.join(_IMG_EXTS)}).")); sys.exit(1)

    # A dedicated box trains one LoRA at a time — refuse to stomp a live session.
    if _session_running(cfg):
        print(fail(f"A training session is already running ({TRAIN_SESSION})."))
        print(f"  Inspect it: {cyan('spark train status')}   pause it: {cyan('spark train pause')}")
        sys.exit(1)

    state, _ = docker_probe(cfg)
    if state != "ok":
        print(fail("Cannot train — Docker is not usable on the DGX:"))
        print_docker_remedy(cfg, state); sys.exit(1)

    # ai-toolkit fetches the base model itself on first run (into the mounted HF
    # cache). Warn if a gated base is configured without a token in the environment.
    import os
    if "black-forest-labs/FLUX.2-dev" in cfg["train_base_model"] and not os.environ.get("HF_TOKEN"):
        print(warn("Base is the gated FLUX.2-dev but no HF_TOKEN is set."))
        print(dim("  Accept its license on HF, then re-run with HF_TOKEN=… (the default klein-4B "
                  "base is Apache/ungated and needs no token).  See docs/training.md."))

    missing = [f for f in imgs if not f.with_suffix(".txt").exists()]
    print(bold(f"Train LoRA '{name}'") + dim(f"  ({len(imgs)} images, trigger '{trigger}')"))
    if missing:
        if params["auto_caption"]:
            _auto_caption(cfg, missing, trigger)
        else:
            print(warn(f"{len(missing)} image(s) have no sidecar .txt caption."))
            print(dim("  ai-toolkit will still train on them, but content captions help the trigger "
                      "word carry only the style.  Add <image>.txt files, or pass ")
                  + cyan("--auto-caption") + dim("."))

    _deploy_assets(cfg)
    _ensure_image(cfg)

    # Stage the corpus (rsync is resume-friendly; falls back to scp -r).
    p = _paths(cfg, name)
    print(dim(f"Staging corpus → {p['dataset']}"))
    ssh(cfg, f"mkdir -p {shlex.quote(p['dataset'])}")
    dest = f"{cfg['dgx_user']}@{cfg['dgx_host']}:{p['dataset']}/"
    staged = False
    try:
        rs = subprocess.run(["rsync", "-a", "--include=*/",
                             *sum((["--include", f"*{e}"] for e in _IMG_EXTS), []),
                             "--include", "*.txt", "--exclude", "*",
                             f"{corpus}/", dest])
        staged = rs.returncode == 0
    except FileNotFoundError:
        pass  # no rsync on this workstation — fall back to scp -r
    if not staged:
        rc = subprocess.run(["scp", "-q", "-r", f"{corpus}/.",
                             f"{cfg['dgx_user']}@{cfg['dgx_host']}:{p['dataset']}/"]).returncode
        if rc != 0:
            print(fail("Could not stage the corpus to the DGX.")); sys.exit(1)

    local_cfg = _render_config(cfg, name, trigger, steps, save_every, rank, resolution)
    if not _scp(cfg, local_cfg, p["config"]):
        print(fail("Could not upload the ai-toolkit config.")); sys.exit(1)
    ssh(cfg, f"rm -f {shlex.quote(p['control'])}/stop 2>/dev/null || true")

    _launch(cfg, name, steps, max_seconds)
    budget = f"{max_hours:g}h" if max_seconds else "until target steps"
    print(ok(f"Training started in screen session {cyan(TRAIN_SESSION)}."))
    print(f"  Run:    {cyan(name)}   steps {steps}   save every {save_every}   budget {budget}")
    print(f"  Watch:  {cyan('spark train status ' + name)}   logs: {cyan('spark train status ' + name + ' --logs')}")
    print(f"  Pause:  {cyan('spark train pause ' + name)}   (stops cleanly after the next checkpoint)")
    return {"action": "train.start", "name": name, "trigger": trigger, "steps": steps,
            "save_every": save_every, "rank": rank, "max_seconds": max_seconds,
            "images": len(imgs), "captioned": len(imgs) - len(missing)}


def pause(params, cfg):
    """Request a clean stop after the next checkpoint (resumable)."""
    cfg = _resolved(cfg)
    name = _resolve_name(cfg, params["name"])
    if not name:
        return {"action": "train.pause", "paused": False}
    if not _session_running(cfg):
        print(dim("No training session is running — nothing to pause."))
        return {"action": "train.pause", "name": name, "paused": False}
    p = _paths(cfg, name)
    ssh(cfg, f"mkdir -p {shlex.quote(p['control'])} && touch {shlex.quote(p['control'])}/stop")
    print(ok(f"Pause requested for {cyan(name)}."))
    print(dim("  The run stops right after the next checkpoint completes (never mid-save)."))
    print(f"  Resume later: {cyan('spark train resume ' + name)}")
    return {"action": "train.pause", "name": name, "paused": True}


def resume(params, cfg):
    """Resume a paused run from its latest checkpoint."""
    cfg = _resolved(cfg)
    name = _resolve_name(cfg, params["name"])
    if not name:
        return {"action": "train.resume", "resumed": False}
    if _session_running(cfg):
        print(warn(f"A training session is already running — {cyan('spark train status')} to inspect it."))
        return {"action": "train.resume", "name": name, "resumed": False}
    st = _read_state(cfg, name)
    if not st:
        print(fail(f"No run state for '{name}'. Start it with {cyan('spark train start')}."))
        sys.exit(1)
    if st.get("status") == "complete":
        print(ok(f"'{name}' is already complete (step {st.get('current_step')}). Nothing to resume."))
        return {"action": "train.resume", "name": name, "resumed": False}
    steps = st.get("target_steps") or params.get("steps")
    max_hours = params["max_hours"]
    max_seconds = int(max_hours * 3600) if max_hours and max_hours > 0 else int(st.get("max_seconds") or 0)
    state, _ = docker_probe(cfg)
    if state != "ok":
        print(fail("Cannot resume — Docker is not usable on the DGX:")); print_docker_remedy(cfg, state); sys.exit(1)
    p = _paths(cfg, name)
    ssh(cfg, f"rm -f {shlex.quote(p['control'])}/stop 2>/dev/null || true")
    _deploy_assets(cfg)
    _ensure_image(cfg)
    _launch(cfg, name, steps, max_seconds)
    budget = f"{max_seconds/3600:g}h" if max_seconds else "until target steps"
    print(ok(f"Resumed {cyan(name)} from step {st.get('current_step', 0)} (budget {budget})."))
    print(f"  Watch: {cyan('spark train status ' + name)}")
    return {"action": "train.resume", "name": name, "resumed": True,
            "from_step": st.get("current_step", 0), "max_seconds": max_seconds}


def status(params, cfg):
    """Show a run's progress, ETA, and where to use the trained LoRA."""
    cfg = _resolved(cfg)
    if params.get("logs"):
        name = _resolve_name(cfg, params["name"])
        if not name:
            return {"action": "train.status"}
        log = _paths(cfg, name)["log"]
        print(dim(f"Tailing {log} — Ctrl+C to stop\n"))
        subprocess.run(["ssh", f"{cfg['dgx_user']}@{cfg['dgx_host']}", f"tail -n 80 -f {shlex.quote(log)}"])
        return {"action": "train.status", "name": name, "logs": True}

    name = _resolve_name(cfg, params["name"])
    if not name:
        return {"action": "train.status", "runs": _list_runs(cfg)}
    st = _read_state(cfg, name)
    running = _session_running(cfg)
    if not st:
        print(f"  {bold(name)}  {dim('no state yet')}  {('(session live)' if running else '')}")
        return {"action": "train.status", "name": name, "running": running, "state": None}

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

    published = None
    if status_str == "complete":
        published = _publish(cfg, name)
    if published:
        print(f"    lora       {ok(published)}")
        print(f"    use it:    {cyan(f'spark comfy generate \"<trigger> ...\" --lora {Path(published).name}')}")
    elif status_str == "complete":
        print(f"    {warn('complete but no checkpoint found to publish')}")
    elif not running and status_str in ("paused", "stopping"):
        print(f"    {dim('resume:')} {cyan('spark train resume ' + name)}")
    return {"action": "train.status", "name": name, "running": running,
            "state": st, "published": published}


def _publish(cfg: dict, name: str) -> str | None:
    """Copy the latest checkpoint into ComfyUI's models/loras as <name>.safetensors.

    Idempotent: re-publishing an already-current LoRA is a no-op copy. Returns the
    remote LoRA path, or None if no checkpoint exists yet.
    """
    p = _paths(cfg, name)
    loras = _loras_dir(cfg)
    out = shlex.quote(p["output"])
    # Prefer ai-toolkit's final, unsuffixed `<name>.safetensors` (the completed
    # weights). It sorts BEFORE the step-suffixed intermediates ('.' < '_'), so a
    # naive sort|tail would wrongly pick the last intermediate — pick it explicitly.
    fin = shlex.quote(f"{p['output']}/{name}.safetensors")
    latest = ssh(cfg, f"if [ -f {fin} ]; then echo {fin}; else "
                      f"ls -1 {out}/*.safetensors 2>/dev/null | grep -v -i optimizer | sort | tail -1; fi").strip()
    if not latest:
        return None
    target = f"{loras}/{name}.safetensors"
    ssh(cfg, f"mkdir -p {shlex.quote(loras)} && cp -f {shlex.quote(latest)} {shlex.quote(target)}")
    return target


# ── Auto-caption (Phase 3) ────────────────────────────────────────────────────────

def _auto_caption(cfg: dict, images: list[Path], trigger: str) -> None:
    """Generate sidecar .txt captions with a vision LLM over the OpenAI-compatible
    endpoint a running `spark llm serve` exposes. Opt-in (gated behind a served
    vision model); the trigger word is prepended so it carries the style."""
    import base64, urllib.request
    base = f"http://{cfg['dgx_host']}:{cfg['port']}"
    print(dim(f"  Auto-captioning {len(images)} image(s) via {base} "
              f"(needs a vision model served: {cyan('spark llm serve <vlm>')})"))
    done = 0
    for img in images:
        try:
            b64 = base64.b64encode(img.read_bytes()).decode()
            payload = {
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "Describe this image's CONTENT in one concise caption "
                     "(subject, composition, setting). Do not describe the artistic style."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ]}],
                "max_tokens": 80, "temperature": 0.2,
            }
            req = urllib.request.Request(base + "/v1/chat/completions",
                                         data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
            resp = json.load(urllib.request.urlopen(req, timeout=120))
            cap = resp["choices"][0]["message"]["content"].strip().replace("\n", " ")
            img.with_suffix(".txt").write_text(f"{trigger} {cap}\n")
            done += 1
            print(f"\r  captioned {done}/{len(images)}", end="", flush=True)
        except Exception as e:
            print(f"\n  {warn(f'caption failed for {img.name}: {str(e)[:80]}')}")
    print(f"\n  {ok(f'Auto-captioned {done}/{len(images)} image(s).')}" if done
          else f"  {warn('No captions generated — is a vision model served?')}")


HANDLERS = {
    "train.start":  start,
    "train.pause":  pause,
    "train.resume": resume,
    "train.status": status,
}
