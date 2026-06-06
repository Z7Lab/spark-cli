"""core handlers — top-level commands: init, status, models, download, queue, logs-dl."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from sparkcore import (
    CONFIG_PATH, bold, dim, red, green, yellow, cyan, ok, warn, fail,
    ssh, ssh_screen, docker_probe, _docker_env,
    _llm_instances, _is_quant_dir, _parse_quant,
)


def init(params, cfg):
    """First-time setup — create ~/.config/spark.json.

    Walks through each setting, showing the current default so you can
    accept it by pressing Enter or type a new value.
    """
    print(bold("spark init — configure DGX Spark connection\n"))
    print(f"  Config will be saved to: {cyan(str(CONFIG_PATH))}\n")

    fields = [
        ("dgx_host",   "DGX hostname or IP"),
        ("dgx_user",   "SSH username"),
        ("models_dir", "Models directory on DGX"),
        ("server_bin", "llama-server binary path"),
        ("server_log", "Server log file path"),
        ("port",       "Server port"),
    ]

    new_cfg = {}
    for key, label in fields:
        current = cfg[key]
        val = input(f"  {label} [{current}]: ").strip()
        new_cfg[key] = (int(val) if key == "port" and val else current) if not val else val

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
             "whisper": None, "ram": None, "disk": None}

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
        c_health = ssh(cfg, "curl -sf http://localhost:8188/ -o /dev/null -w '%{http_code}' || echo 0")
        if c_health.strip() in ("200", "101"):
            print(f"  ComfyUI  {ok(f'http://{host}:8188')}")
        else:
            print(f"  ComfyUI  {warn('starting...')}  {dim(comfy_container)}")
    else:
        # No comfy container: tell apart a healthy idle daemon from a daemon
        # that is down/denied — the latter must not read as 'not running' (bug A2).
        state, _ = docker_probe(cfg)
        if state == "ok":
            print(f"  ComfyUI  {dim('not running')}  Run: {cyan('spark comfy start')}")
        elif state == "permission":
            print(f"  ComfyUI  {warn('Docker permission denied')}  "
                  f"Fix: {cyan('sudo usermod -aG docker ' + cfg['dgx_user'])} {dim('(see: spark comfy status)')}")
        elif state == "absent":
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
                    f"python3 {cfg['hf_dl']} {repo} {dest} '{pattern}'"])
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

    dl_script = cfg['hf_dl']
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


HANDLERS = {
    "core.init":     init,
    "core.status":   status,
    "core.models":   models,
    "core.download": download,
    "core.queue":    queue,
    "core.logs_dl":  logs_dl,
}
