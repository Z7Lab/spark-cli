"""core handlers — top-level commands: init, status, models, download, queue, logs-dl."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from sparkcore import (
    CONFIG_PATH, bold, dim, red, green, yellow, cyan, ok, warn, fail,
    ssh, ssh_screen, docker_probe, _docker_env,
    _llm_instances, _is_quant_dir, _parse_quant, _human,
    config_schema, _coerce, remote_script,
)


def init(params, cfg):
    """First-time setup — create ~/.config/spark.json.

    Walks through each setting, showing the current default so you can
    accept it by pressing Enter or type a new value.
    """
    print(bold("spark init — configure DGX Spark connection\n"))
    print(f"  Config will be saved to: {cyan(str(CONFIG_PATH))}\n")

    new_cfg = {}
    for c in config_schema():
        if not c.get("init"):
            continue
        current = cfg[c["key"]]
        val = input(f"  {c['help']} [{current}]: ").strip()
        new_cfg[c["key"]] = _coerce(val, c["type"]) if val else current

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(new_cfg, indent=2) + "\n")
    print(f"\n  {ok(f'Config saved to {CONFIG_PATH}')}")
    print(f"\n  Test your setup with: {cyan('spark status')}")
    return {"action": "core.init", "config_path": str(CONFIG_PATH), "config": new_cfg}


def status(params, cfg):
    """Show what's currently running on the DGX."""
    host = cfg["dgx_host"]
    port = cfg["port"]

    print(bold("DGX Spark status"))

    # Config file present?
    if CONFIG_PATH.exists():
        print(f"  Config   {ok(str(CONFIG_PATH))}")
    else:
        print(f"  Config   {warn('no config file — using defaults')}  Run: {cyan('spark init')}")

    print(f"  Host     {cyan(host)}")

    # SSH reachable?
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
                        f"{cfg['dgx_user']}@{host}", "echo ok"],
                       capture_output=True, text=True)
    if r.stdout.strip() != "ok":
        print(f"  SSH      {fail('unreachable')}  Check host and SSH key")
        return {"action": "core.status", "host": host,
                "config_present": CONFIG_PATH.exists(), "ssh": "unreachable"}
    print(f"  SSH      {ok('connected')}")

    state = {"action": "core.status", "host": host,
             "config_present": CONFIG_PATH.exists(), "ssh": "connected",
             "instances": [], "vllm": None, "comfy": None,
             "whisper": None, "ram": None, "disk": None, "gpu": None}

    # Detect server type — one llama-server per loaded model
    instances = _llm_instances(cfg)
    vllm_pid  = ssh(cfg, "pgrep -f '[v]llm.entrypoints' | head -1 || true")

    state["instances"] = [{"port": int(i["port"]), "name": i["name"],
                           "quant": i["quant"], "pid": i["pid"]} for i in instances]
    state["vllm"] = vllm_pid or None

    if instances:
        for inst in instances:
            iport  = inst["port"]
            health = ssh(cfg, f"curl -sf http://localhost:{iport}/health || echo unreachable")
            ready  = "ok" in health.lower() or "true" in health.lower()
            label  = f"{cyan(inst['name'])} {dim(inst['quant'])}  :{iport}  {dim('pid ' + inst['pid'])}"
            if ready:
                print(f"  Server   {ok('llama-server')}  {label}")
                print(f"           {ok(f'http://{host}:{iport}/v1')}  "
                      f"UI {cyan(f'http://{host}:{iport}')}")
            else:
                print(f"  Server   {warn('loading...')}  {label}  "
                      f"Run: {cyan(f'spark llm logs --port {iport}')}")
    elif vllm_pid:
        print(f"  Server   {ok('vLLM')}  (pid {vllm_pid})")
        health = ssh(cfg, f"curl -sf http://localhost:{port}/health || echo unreachable")
        if "ok" in health.lower() or "true" in health.lower():
            print(f"  API      {ok(f'http://{host}:{port}/v1')}")
            print(f"  Chat UI  {dim('none built-in — use Open WebUI')}")
        else:
            print(f"  API      {warn('still loading...')}")
    else:
        print(f"  Server   {dim('not running')}  Run: {cyan('spark llm serve <model>')}")

    # ComfyUI
    comfy_container = ssh(cfg, _docker_env(cfg) + "docker ps --filter name=comfy --format '{{.Names}} {{.Status}}' 2>/dev/null | head -1")
    state["comfy"] = comfy_container or None
    if comfy_container:
        cport = cfg["comfy_port"]
        c_health = ssh(cfg, f"curl -sf http://localhost:{cport}/ -o /dev/null -w '%{{http_code}}' || echo 0")
        if c_health.strip() in ("200", "101"):
            print(f"  ComfyUI  {ok(f'http://{host}:{cport}')}")
        else:
            print(f"  ComfyUI  {warn('starting...')}  {dim(comfy_container)}")
    else:
        # No comfy container: tell apart a healthy idle daemon from a daemon
        # that is down/denied — the latter must not read as 'not running' (bug A2).
        # NB: bind to a NEW name — `state` is the result dict returned below.
        dstate, _ = docker_probe(cfg)
        if dstate == "ok":
            print(f"  ComfyUI  {dim('not running')}  Run: {cyan('spark comfy start')}")
        elif dstate == "permission":
            print(f"  ComfyUI  {warn('Docker permission denied')}  "
                  f"Fix: {cyan('sudo usermod -aG docker ' + cfg['dgx_user'])} {dim('(see: spark comfy status)')}")
        elif dstate == "absent":
            print(f"  ComfyUI  {warn('Docker not installed')}")
        else:
            print(f"  ComfyUI  {warn('Docker daemon down')}  "
                  f"Fix: {cyan('sudo systemctl restart docker')} {dim('(see: spark comfy status)')}")

    # Whisper server
    w_pid = ssh(cfg, "pgrep whisper-server | head -1 || true")
    state["whisper"] = w_pid or None
    if w_pid:
        w_model = ""
        w_line  = ssh(cfg, f"ps -p {w_pid} -o args= 2>/dev/null || true")
        if "-m " in w_line:
            w_model = f"  {dim(Path(w_line.split('-m ')[1].strip().split()[0]).stem)}"
        w_health = ssh(cfg, "curl -sf http://localhost:8081/health || echo unreachable")
        if "ok" in w_health.lower() or "whisper" in w_health.lower():
            print(f"  Whisper  {ok('whisper-server')}{w_model}  http://{host}:8081/v1/audio/transcriptions")
        else:
            print(f"  Whisper  {warn('starting...')}")
    else:
        print(f"  Whisper  {dim('not running')}  Run: {cyan('spark transcribe start')}")

    # Memory
    mem = ssh(cfg, "free -h | awk '/^Mem:/ {print $3\" used / \"$2\" total — \"$7\" available\"}'")
    state["ram"] = mem or None
    if mem:
        avail_gb = ssh(cfg, "free -g | awk '/^Mem:/ {print $7}'")
        try:
            avail = int(avail_gb.strip())
            color = green if avail >= 30 else yellow if avail >= 10 else red
            print(f"  RAM      {color(mem)}")
        except Exception:
            print(f"  RAM      {mem}")

    # Disk
    disk = ssh(cfg, f"df -h {cfg['models_dir']} 2>/dev/null | tail -1 | awk '{{print $4\" free of \"$2}}'")
    state["disk"] = disk or None
    if disk:
        print(f"  Disk     {disk}")

    # HF token PRESENCE (never the value) — what authenticates gated base downloads
    # (klein-9B / FLUX.2-dev). Reports only present/absent for spark's own env and
    # for the DGX (env var or ~/.cache/huggingface/token, which the downloader uses).
    import os as _os
    local_tok = bool((_os.environ.get("HF_TOKEN") or _os.environ.get("HUGGING_FACE_HUB_TOKEN") or "").strip())
    dgx_tok = ssh(cfg, '[ -n "$HF_TOKEN" ] || [ -s ~/.cache/huggingface/token ] '
                       '&& echo present || echo absent').strip() == "present"
    state["hf_token"] = {"local": local_tok, "dgx": dgx_tok}
    def _badge(p): return ok("present") if p else dim("absent")
    print(f"  HF token {_badge(dgx_tok)} on DGX  ·  {_badge(local_tok)} in spark env  "
          + dim("(gated bases need one)"))

    # GPU temperature / throttle (one line; full detail in `spark temp`)
    g = _gpu_query(cfg)
    state["gpu"] = g
    if g and g["temp"] is not None:
        t = g["temp"]
        tcolor = green if t < 70 else yellow if t < 84 else red
        u = f", {g['util']:.0f}% util" if g["util"] is not None else ""
        thr = red("  THROTTLING: " + ", ".join(g["throttling"])) if g["throttling"] else ""
        print(f"  GPU      {tcolor(f'{t:.0f}°C')}{u}{thr}")

    return state


def models(params, cfg):
    """List downloaded models with quant and size."""
    print(bold("Downloaded models\n"))
    output = ssh(cfg, f"find {cfg['models_dir']} -name '*.gguf' 2>/dev/null | sort")
    if not output:
        print(dim(f"  No models found in {cfg['models_dir']}"))
        print(f"\n  Download one with: {cyan('spark download <repo> <name> <pattern>')}")
        return {"action": "core.models", "models": []}

    # Group files → model name, collapsing quant subdirs
    grouped: dict = {}
    for line in output.splitlines():
        p = Path(line)
        if _is_quant_dir(p.parent.name):
            key = p.parent.parent.name   # quant subdir → use grandparent
        else:
            key = p.parent.name          # file directly in model dir
        grouped.setdefault(key, []).append(line)

    listed = []
    for name, files in sorted(grouped.items()):
        model_path_remote = cfg['models_dir'].replace('~', '$HOME')
        size = ssh(cfg, f"du -sh {model_path_remote}/{name} 2>/dev/null | cut -f1")
        # Deduplicate quants (multi-part files share the same quant)
        quants = sorted(set(_parse_quant(Path(f).name) for f in files))
        parts  = len(files)
        parts_note = f"  {dim(f'{parts} parts')}" if parts > 1 else ""
        quant_str = cyan(", ".join(quants))
        print(f"  {bold(name)}")
        print(f"    quant   {quant_str}{parts_note}")
        print(f"    size    {size}")
        print(f"    serve   {dim(f'spark llm serve {name}')}")
        print()
        listed.append({"name": name, "quants": quants, "parts": parts, "size": size})

    print(f"  Load a model: {cyan('spark llm serve <name>')}")
    return {"action": "core.models", "models": listed}


def download(params, cfg):
    """Download a model from HuggingFace to the DGX."""
    repo, name, pattern = params["repo"], params["local_name"], params["pattern"]
    dest = f"{cfg['models_dir']}/{name}"
    print(f"Downloading {bold(repo)}")
    print(f"  Destination: {cyan(dest)}")
    print(f"  Pattern:     {pattern}\n")
    subprocess.run(["ssh", "-t", f"{cfg['dgx_user']}@{cfg['dgx_host']}",
                    f"python3 {remote_script(cfg, 'hf_download.py')} {repo} {dest} '{pattern}'"])
    return {"action": "core.download", "repo": repo, "name": name,
            "pattern": pattern, "dest": dest}


def queue(params, cfg):
    """Queue multiple model downloads to run sequentially in the background."""
    args = params["specs"]
    if not args:
        print(red("Provide downloads as groups of 3: <repo> <name> <pattern> ..."))
        print(f"  Run {cyan('spark queue --help')} for usage.")
        sys.exit(1)
    if len(args) % 3 != 0:
        print(red(f"Arguments must be groups of 3 (repo name pattern). Got {len(args)}."))
        print(f"  Run {cyan('spark queue --help')} for usage.")
        sys.exit(1)

    triplets = [(args[i], args[i+1], args[i+2]) for i in range(0, len(args), 3)]

    print(bold(f"Queuing {len(triplets)} download(s) sequentially:\n"))
    for i, (repo, name, pattern) in enumerate(triplets, 1):
        dest = f"{cfg['models_dir']}/{name}"
        print(f"  {bold(str(i))}  {cyan(repo)}")
        print(f"     → {dest}  {dim(pattern)}")
    print()

    dl_script = remote_script(cfg, 'hf_download.py')
    log = cfg['download_log']
    chain = " && ".join(
        f"echo '[{i+1}/{len(triplets)}] {name}' | tee -a {log} && "
        f"python3 {dl_script} {repo} {cfg['models_dir']}/{name} '{pattern}' 2>&1 | tee -a {log}"
        for i, (repo, name, pattern) in enumerate(triplets)
    )
    full_cmd = f"echo '' > {log} && {chain} && echo 'ALL DONE' | tee -a {log}"

    ssh_screen(cfg, "downloads", full_cmd)
    print(ok(f"Downloads queued in background screen session 'downloads'"))
    print(f"  Monitor:  {cyan('du -sh ~/models/*/')}")
    print(f"  Log:      {cyan('spark logs-dl')}")
    print(f"  Attach:   {dim('ssh into DGX, then: screen -r downloads')}")
    return {"action": "core.queue", "session": "downloads",
            "queued": [{"repo": r, "name": n, "pattern": p}
                       for (r, n, p) in triplets]}


def logs_dl(params, cfg):
    """Tail the download queue log. Ctrl+C to exit."""
    print(dim(f"Tailing download log on {cfg['dgx_host']} — Ctrl+C to stop\n"))
    subprocess.run(["ssh", f"{cfg['dgx_user']}@{cfg['dgx_host']}",
                    f"tail -f {cfg['download_log']}"])
    return {"action": "core.logs_dl", "log": cfg["download_log"]}


def config(params, cfg):
    """Print every setting, its current value, env var, and help (the config schema)."""
    print(bold("spark config") + dim("   precedence: env var  >  ~/.config/spark.json  >  default"))
    exists = CONFIG_PATH.exists()
    print(dim(f"  file: {CONFIG_PATH}" + ("" if exists else "  (not created — run: spark init)")) + "\n")
    rows = config_schema()
    kw = max(len(c["key"]) for c in rows)
    for c in rows:
        print(f"  {cyan(c['key'].ljust(kw))}  {str(cfg[c['key']])}")
        print(f"  {' ' * kw}  {dim(c['env'] + '  ·  ' + c['help'])}")

    # Surface keys in the file that aren't in the schema — typos or stale keys
    # (ignored at runtime), so they don't sit there silently doing nothing.
    if exists:
        try:
            file_keys = set(json.loads(CONFIG_PATH.read_text()))
        except (json.JSONDecodeError, OSError):
            file_keys = set()
        extra = sorted(file_keys - {c["key"] for c in rows})
        if extra:
            print(warn(f"\n  unrecognized keys in your config (ignored — remove them): {', '.join(extra)}"))

    print(dim(f"\n  example: templates/spark.json.example  →  copy to {CONFIG_PATH} and edit"))
    return {"action": "core.config",
            "schema": [{k: c[k] for k in ("key", "default", "env", "type", "help")} for c in rows],
            "current": {c["key"]: cfg[c["key"]] for c in rows}}


def config_set(params, cfg):
    """Set one key in ~/.config/spark.json, preserving the rest of the file."""
    key, raw = params["key"], params["value"]
    schema = {c["key"]: c for c in config_schema()}
    if key not in schema:
        print(fail(f"Unknown config key: {key}"))
        print(dim("  Valid keys: " + ", ".join(c["key"] for c in config_schema())))
        sys.exit(1)

    try:
        value = _coerce(raw, schema[key]["type"])
    except ValueError:
        print(fail(f"'{raw}' is not a valid {schema[key]['type']} for {key}."))
        sys.exit(1)

    existing = {}
    if CONFIG_PATH.exists():
        try:
            existing = json.loads(CONFIG_PATH.read_text())
        except json.JSONDecodeError as e:
            print(fail(f"Config is not valid JSON ({CONFIG_PATH}): {e}"))
            sys.exit(1)
    old = existing.get(key, cfg.get(key))

    existing[key] = value
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(existing, indent=2) + "\n")
    print(ok(f"{key}: {old!r} → {value!r}"))
    print(dim(f"  written to {CONFIG_PATH}"))
    return {"action": "core.config_set", "key": key, "old": old,
            "value": value, "config_path": str(CONFIG_PATH)}


# nvidia-smi clocks_event_reasons.active bits → (label, is_throttle). GPU-idle and
# applications-clock settings are informational; the rest mean clocks are being capped.
_CLOCK_EVENT_BITS = [
    (0x0001, "idle", False),
    (0x0002, "app clocks setting", False),
    (0x0004, "SW power cap", True),
    (0x0008, "HW slowdown", True),
    (0x0010, "sync boost", False),
    (0x0020, "SW thermal slowdown", True),
    (0x0040, "HW thermal slowdown", True),
    (0x0080, "HW power brake", True),
    (0x0100, "display clock setting", False),
]


def _gpu_query(cfg):
    """Parse one line of nvidia-smi GPU telemetry over SSH, or None if unavailable.
    Decodes the clocks-event bitmask into active reasons + which are throttling."""
    fields = "name,temperature.gpu,utilization.gpu,power.draw,clocks.sm,clocks_event_reasons.active"
    raw = ssh(cfg, f"nvidia-smi --query-gpu={fields} --format=csv,noheader,nounits 2>/dev/null | head -1")
    raw = (raw or "").strip()
    if not raw or "," not in raw:
        return None
    parts = [p.strip() for p in raw.split(",")]

    def num(i):
        try:
            return float(parts[i])
        except (IndexError, ValueError):
            return None

    try:
        bits = int(parts[5], 16)
    except (IndexError, ValueError):
        bits = 0
    active = [(label, throt) for mask, label, throt in _CLOCK_EVENT_BITS if bits & mask]
    return {"name": parts[0] if parts else "GPU",
            "temp": num(1), "util": num(2), "power": num(3), "sm_mhz": num(4),
            "events_hex": parts[5] if len(parts) > 5 else "0x0",
            "reasons": [l for l, _ in active],
            "throttling": [l for l, t in active if t]}


def _dur(seconds) -> str:
    """Compact duration: 90 -> '1m30s', 6201 -> '1h43m', 90000 -> '1d1h'."""
    try:
        s = int(float(seconds))
    except (TypeError, ValueError):
        return "?"
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _active_gpu_workloads(cfg: dict) -> list:
    """What's actively using the GPU and for how long, plus the command to get its
    detail — temp owns "what + uptime"; progress/footprints belong to those commands
    (one owner per number, no drift). [{kind, detail, uptime, hint}].

    A live training run is detected from the spark-train-<name> container (ground
    truth — state files can go stale); inference from running llama-servers."""
    import re
    work = []
    try:
        name = ssh(cfg, _docker_env(cfg) + "docker ps --filter name=spark-train- "
                   "--format '{{.Names}}' 2>/dev/null | head -1").strip()
    except Exception:
        name = ""
    if name.startswith("spark-train-"):
        run = name[len("spark-train-"):]
        uptime = "?"
        if re.fullmatch(r"[A-Za-z0-9_-]+", run):   # safe to interpolate
            raw = ssh(cfg, f"cat {cfg['train_dir'].rstrip('/')}/state/{run}.json 2>/dev/null || true")
            try:
                st = json.loads(raw) if raw.strip() else None
            except ValueError:
                st = None
            if st:
                uptime = _dur(st.get("elapsed_seconds"))
        work.append({"kind": "training", "detail": run, "uptime": uptime,
                     "hint": f"spark train status {run}"})
    for i in _llm_instances(cfg):
        et = ssh(cfg, f"ps -o etimes= -p {int(i['pid'])} 2>/dev/null || true").strip()
        work.append({"kind": "inference", "detail": f"{i['name']} :{i['port']}",
                     "uptime": _dur(et) if et.isdigit() else "?", "hint": "spark llm list"})
    return work


def temp(params, cfg):
    """Show GPU temperature, utilization, power, SM clock + active throttle reasons,
    plus what's running on the GPU (training / inference) and for how long.
    Read-only; thermal SAFETY throttling is automatic in GB10 hardware regardless."""
    g = _gpu_query(cfg)
    if not g:
        print(fail("Couldn't read GPU telemetry from nvidia-smi on the DGX."))
        print(dim(f"  Check SSH/host with {cyan('spark status')}."))
        return {"action": "core.temp", "available": False}
    print(bold(g["name"]))
    t = g["temp"]
    if t is not None:
        tcolor = green if t < 70 else yellow if t < 84 else red
        print(f"  Temp      {tcolor(f'{t:.0f}°C')}")
    if g["util"] is not None:
        print(f"  Util      {g['util']:.0f}%")
    if g["power"] is not None:
        print(f"  Power     {g['power']:.0f} W")
    if g["sm_mhz"] is not None:
        print(f"  SM clock  {g['sm_mhz']:.0f} MHz")
    if g["throttling"]:
        print(f"  Throttle  {red('THROTTLING — ' + ', '.join(g['throttling']))}")
    elif g["reasons"]:
        print(f"  Clocks    {dim(', '.join(g['reasons']))}")
    else:
        print(f"  Throttle  {green('none')}")
    # What's on the GPU + how long; the detail lives in the pointed-to command.
    work = _active_gpu_workloads(cfg)
    if work:
        for idx, w in enumerate(work):
            label = "Running" if idx == 0 else ""
            print(f"  {label:<8}  {w['kind']} {cyan(w['detail'])}  {dim('up ' + w['uptime'])}"
                  f"  {dim('→ ' + w['hint'])}")
    else:
        print(f"  {'Running':<8}  {dim('nothing (no training run or inference server)')}")
    print(dim("  Thermal-safety throttling is automatic in GB10 hardware."))
    return {"action": "core.temp", "available": True, "workloads": work, **g}


def disk(params, cfg):
    """Show DGX disk usage and the biggest spark-managed consumers."""
    if params.get("prune"):
        print(bold("Pruning Docker — unused images + build cache (volumes kept)…\n"))
        out = ssh(cfg, _docker_env(cfg) + "docker system prune -af 2>&1 || true")
        reclaimed = next((ln.split(":", 1)[1].strip()
                          for ln in out.splitlines() if "reclaimed" in ln.lower()), None)
        if out.strip():
            print(dim(out.strip()))
        free = ssh(cfg, f"df -h {cfg['models_dir']} 2>/dev/null | tail -1 | awk '{{print $4}}'")
        print(ok(f"\n  Reclaimed {reclaimed or 'space'}.") + dim(f"  Now {free.strip()} free."))
        return {"action": "core.disk", "pruned": True, "reclaimed": reclaimed,
                "free": free.strip() or None}

    print(bold("DGX Spark disk\n"))

    # Free space on the filesystem holding the models dir (the big one).
    df = ssh(cfg, f"df -h {cfg['models_dir']} 2>/dev/null | tail -1 | awk '{{print $2, $3, $4, $5}}'")
    parts = df.split() if df else []
    full = False
    if len(parts) >= 4:
        size, used, avail, pct = parts[:4]
        try:
            p = int(pct.rstrip("%"))
            col = green if p < 80 else yellow if p < 95 else red
            full = p >= 99
        except ValueError:
            col = dim
        print(f"  Filesystem   {col(f'{used} used / {size}  ·  {avail} free  ({pct})')}\n")

    # The llama.cpp checkout is …/llama.cpp/build/bin/llama-server — strip to the root.
    sbin = cfg["server_bin"]
    llama_dir = sbin.split("/build/")[0] if "/build/" in sbin else str(Path(sbin).parent)
    targets = [
        ("LLM models (GGUF)",          cfg["models_dir"]),
        ("ComfyUI (install + models)", cfg.get("comfy_dir", "")),
        ("Whisper models",             cfg.get("whisper_models_dir", "")),
        ("llama.cpp (src + build)",    llama_dir),
        ("TTS venv",                   cfg.get("tts_venv", "")),
        ("HuggingFace cache",          "~/.cache/huggingface"),
    ]
    targets = [(lbl, p) for lbl, p in targets if p]

    # One round-trip: each existing path emits "<idx>\t<bytes>" (du is metadata-only).
    cmd = " ; ".join(
        f'du -sb {p} 2>/dev/null | cut -f1 | sed "s/^/{i}\\t/"'
        for i, (lbl, p) in enumerate(targets)
    )
    raw = ssh(cfg, cmd) or ""
    sizes = {}
    for line in raw.splitlines():
        idx, _, b = line.partition("\t")
        if idx.strip().isdigit() and b.strip().isdigit():
            sizes[int(idx)] = int(b)

    rows = sorted(((targets[i][0], sz) for i, sz in sizes.items()), key=lambda r: -r[1])
    if rows:
        print(bold("  Largest spark-managed paths"))
        w = max(len(lbl) for lbl, _ in rows)
        for lbl, sz in rows:
            print(f"    {lbl:<{w}}   {cyan(_human(sz))}")

    # Docker's own reclaimable view (images/containers/volumes/build cache).
    ddf = ssh(cfg, _docker_env(cfg) + "docker system df 2>/dev/null || true")
    if ddf.strip():
        print(bold("\n  Docker"))
        for line in ddf.splitlines():
            print(f"    {dim(line)}")

    if full or (rows and any("cache" in lbl.lower() for lbl, _ in rows)):
        print(dim("\n  Reclaim without touching models:"))
        print(dim("    · HuggingFace cache — re-downloads on demand"))
        print(dim("    · docker system prune -af  (and `--volumes` if safe)"))
        print(dim("    · drop a model dir under ") + cyan(cfg["models_dir"]) + dim(" you no longer serve"))

    return {"action": "core.disk", "free": parts[2] if len(parts) >= 4 else None,
            "paths": {targets[i][0]: sz for i, sz in sizes.items()}}


HANDLERS = {
    "core.init":       init,
    "core.config":     config,
    "core.config_set": config_set,
    "core.status":     status,
    "core.temp":       temp,
    "core.disk":       disk,
    "core.models":     models,
    "core.download":   download,
    "core.queue":      queue,
    "core.logs_dl":    logs_dl,
}
