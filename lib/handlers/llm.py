"""llm handlers — serve, list, unload, stop, logs, open, pull-models."""

from __future__ import annotations

import re
import sys
import time
import webbrowser
from pathlib import Path

from sparkcore import (
    bold, dim, red, green, yellow, cyan, ok, warn, fail,
    ssh, ssh_screen,
    _llm_instances, _parse_quant, _quant_glob, _du_bytes, _free_bytes,
    _kv_cache_bytes, _human, _port_log, _models_catalog, _run_pull,
)


def serve(params, cfg):
    """Start (load) a model on its own llama-server (fit-checked)."""
    name  = params["model"]
    quant_filter = params["quant"]
    explicit_port = params["port"] is not None
    port  = params["port"] if explicit_port else cfg["port"]
    ctx   = params["ctx"]
    par   = params["parallel"]

    # Find all matching GGUFs, then pick the first part only per quant
    all_matches = ssh(cfg, f"find {cfg['models_dir']} -name '*.gguf' 2>/dev/null | grep -i '{name}' | sort")
    if not all_matches:
        print(fail(f"No GGUF found matching '{name}'"))
        print(f"  Run {cyan('spark models')} to see available models.")
        sys.exit(1)

    all_files = all_matches.strip().splitlines()
    # Keep only part 1 of multi-part sets (or single-file models)
    # Part numbers are 5 digits: 00001, 00002, etc. — keep only 00001.
    def is_first_part(p):
        m = re.search(r'-(\d{5})-of-\d{5}', Path(p).name)
        return int(m.group(1)) == 1 if m else True
    entry_files = [f for f in all_files if is_first_part(f)]

    if len(entry_files) > 1:
        # Filter by --quant if provided
        if quant_filter:
            filtered = [f for f in entry_files if quant_filter.lower() in f.lower()]
            if not filtered:
                print(fail(f"No quant matching '{quant_filter}' found."))
                print(f"  Available: {', '.join(cyan(_parse_quant(Path(f).name)) for f in entry_files)}")
                sys.exit(1)
            model_path = filtered[0]
        else:
            # Multiple quants — show them and ask
            print(f"Multiple quants available for {bold(name)}:\n")
            for i, f in enumerate(entry_files, 1):
                q    = _parse_quant(Path(f).name)
                mdir = cfg['models_dir'].replace('~', '$HOME')
                size = ssh(cfg, f"du -sh {mdir}/{name} 2>/dev/null | cut -f1") if i == 1 else ""
                print(f"  {bold(str(i))}  {cyan(q):<22} {dim(size)}")
            print()
            print(dim(f"  Tip: pass --quant <name> to skip this prompt"))
            try:
                choice = input(f"  Which quant? [1-{len(entry_files)}]: ").strip()
                idx = int(choice) - 1
                if not (0 <= idx < len(entry_files)):
                    raise ValueError
            except (ValueError, KeyboardInterrupt):
                print(red("\nCancelled."))
                sys.exit(1)
            model_path = entry_files[idx]
    else:
        model_path = entry_files[0]

    quant = _parse_quant(Path(model_path).name)

    # Each model runs as its own llama-server on its own port — never evict
    # another model implicitly. The live processes are the registry.
    instances = _llm_instances(cfg)
    for inst in instances:
        if inst["name"] == name and inst["quant"] == quant:
            print(warn(f"{name} {quant} is already loaded on port {inst['port']} "
                       f"(pid {inst['pid']})."))
            return {"action": "llm.serve", "model": name, "quant": quant,
                    "port": int(inst["port"]), "status": "already_loaded"}

    used_ports = {i["port"] for i in instances}
    if str(port) in used_ports:
        if explicit_port:
            holder = next(i for i in instances if i["port"] == str(port))
            print(fail(f"Port {port} is in use by {holder['name']} {holder['quant']}."))
            print(f"  Pick another {cyan('--port N')}, or free it: "
                  f"{cyan(f'spark llm unload --port {port}')}")
            sys.exit(1)
        port = cfg["port"]
        while str(port) in used_ports:
            port += 1

    # Deterministic fit check — refuse rather than silently evict. The estimate is
    # weights (on disk) + KV cache (from GGUF dims, scales with ctx×parallel), and
    # must leave a reserve margin free for the OS and co-running services.
    weights = _du_bytes(cfg, _quant_glob(model_path, quant))
    kv      = _kv_cache_bytes(cfg, model_path, ctx, par)
    reserve = int(cfg.get("mem_reserve_gb", 8)) * 1024**3
    needed  = weights + kv
    avail   = _free_bytes(cfg)
    if needed and avail and needed + reserve > avail:
        kv_part = f" + ~{_human(kv)} KV @ ctx {ctx}×{par}" if kv else ""
        print(fail(f"{name} {quant} needs ~{_human(needed)} "
                   f"(~{_human(weights)} weights{kv_part}) plus a {_human(reserve)} "
                   f"reserve, but only {_human(avail)} is free."))
        if instances:
            print(dim("  Resident models (unload some to make room):"))
            for inst in instances:
                sz   = _human(_du_bytes(cfg, _quant_glob(inst["model_path"], inst["quant"])))
                hint = dim(f"spark llm unload --port {inst['port']}")
                print(f"    :{inst['port']}  {cyan(inst['name'])} {dim(inst['quant'])}  {sz}   {hint}")
        if not kv:
            print(dim("  (KV cache could not be estimated; weights + reserve only)"))
        print(dim("  Free memory, lower --ctx/--parallel, or adjust mem_reserve_gb."))
        sys.exit(1)

    session = f"llama-{port}"
    log_path = _port_log(cfg, port)
    print(f"Loading {bold(name)}  {cyan(quant)}  on port {port}...")

    serve_cmd = (
        f"{cfg['server_bin']} "
        f"--model {model_path} "
        f"--port {port} --host 0.0.0.0 "
        f"--ctx-size {ctx} --n-gpu-layers 999 --parallel {par} "
        f"--jinja --tools all "
        f"2>&1 | tee {log_path}"
    )
    ssh_screen(cfg, session, serve_cmd)

    print(dim("Waiting for server to be ready..."))
    for i in range(90):
        time.sleep(2)
        log = ssh(cfg, f"tail -3 {log_path} 2>/dev/null || true")
        if "server is listening" in log:
            print(f"\n  {ok('Model loaded and server ready')}")
            print(f"  API      http://{cfg['dgx_host']}:{port}/v1")
            chat_url = f"http://{cfg['dgx_host']}:{port}"
            print(f"  Chat UI  {cyan(chat_url)}  {dim('(open in browser)')}")
            return {"action": "llm.serve", "model": name, "quant": quant,
                    "port": port, "status": "ready"}
        if "error" in log.lower() and "loading" not in log.lower():
            print(fail(f"\nServer error — run: {cyan('spark llm logs')}"))
            return {"action": "llm.serve", "model": name, "quant": quant,
                    "port": port, "status": "error"}
        print(f"\r  Loading... ({(i+1)*2}s)", end="", flush=True)

    print(f"\n  {warn('Still loading')} — run: {cyan('spark llm logs')} to watch progress")
    return {"action": "llm.serve", "model": name, "quant": quant,
            "port": port, "status": "loading"}


def ls(params, cfg):
    """List loaded LLM models — one llama-server instance per line."""
    instances = _llm_instances(cfg)
    if not instances:
        print(dim("No models loaded.")
              + f"  Load one: {cyan('spark llm serve <model>')}")
        return {"action": "llm.list", "loaded": [], "free_bytes": _free_bytes(cfg)}

    print(bold("Loaded models"))
    total = 0
    loaded = []
    for inst in instances:
        nbytes = _du_bytes(cfg, _quant_glob(inst["model_path"], inst["quant"]))
        total += nbytes
        print(f"  :{inst['port']}  {bold(inst['name'])} {cyan(inst['quant'])}"
              f"  {dim(_human(nbytes))}  {dim('pid ' + inst['pid'])}")
        loaded.append({"port": int(inst["port"]), "name": inst["name"],
                       "quant": inst["quant"], "bytes": nbytes})
    avail = _free_bytes(cfg)
    print(dim(f"\n  {len(instances)} loaded — ~{_human(total)} resident, {_human(avail)} free"))
    print(dim(f"  Unload one: {cyan('spark llm unload <name|--port N>')}"))
    return {"action": "llm.list", "loaded": loaded, "free_bytes": avail}


def unload(params, cfg):
    """Unload one loaded model, freeing its memory. Leaves others running."""
    instances = _llm_instances(cfg)
    if not instances:
        print(dim("No models loaded."))
        return {"action": "llm.unload", "unloaded": None}

    port  = params["port"]
    quant = params["quant"]
    name  = params["name"]

    if port:
        matches = [i for i in instances if i["port"] == str(port)]
        if not matches:
            print(fail(f"No model loaded on port {port}."))
            sys.exit(1)
    elif name:
        matches = [i for i in instances if i["name"].lower() == name.lower()]
        if quant:
            matches = [i for i in matches if i["quant"].lower() == quant.lower()]
        if not matches:
            print(fail(f"No loaded model matches '{name}'"
                       + (f" {quant}" if quant else "") + "."))
            print(f"  Run {cyan('spark llm list')} to see what's loaded.")
            sys.exit(1)
    else:
        print(fail("Specify a model name or --port N to unload."))
        print(f"  Run {cyan('spark llm list')} to see what's loaded.")
        sys.exit(1)

    if len(matches) > 1:
        print(fail(f"'{name}' is ambiguous — {len(matches)} instances loaded:"))
        for i in matches:
            print(f"    --port {i['port']}  {cyan(i['name'])} {dim(i['quant'])}")
        print(dim("  Re-run with --port N or --quant Q."))
        sys.exit(1)

    inst = matches[0]
    ssh(cfg, f"kill {inst['pid']} 2>/dev/null || true; "
             f"screen -S llama-{inst['port']} -X quit 2>/dev/null || true")
    print(ok(f"Unloaded {inst['name']} {inst['quant']} (was on port {inst['port']}). "
             f"Memory freed."))
    return {"action": "llm.unload",
            "unloaded": {"name": inst["name"], "quant": inst["quant"],
                         "port": int(inst["port"])}}


def stop(params, cfg):
    """Stop ALL llama-server / vLLM instances at once and free their memory."""
    pids = ssh(cfg, "pgrep -f '[l]lama-server|[v]llm' || true")
    if not pids:
        print(dim("No LLM server is running."))
        return {"action": "llm.stop", "stopped": 0}
    ssh(cfg, "pkill -f '[l]lama-server|[v]llm' || true; "
             "for s in $(screen -ls 2>/dev/null | grep -oE '[0-9]+\\.llama[-0-9]*'); "
             "do screen -S \"$s\" -X quit 2>/dev/null; done")
    n = len(pids.split())
    print(ok(f"Stopped {n} LLM server(s). Memory freed."))
    return {"action": "llm.stop", "stopped": n}


def logs(params, cfg):
    """Tail a llama-server log. Ctrl+C to exit."""
    import subprocess
    lines = params["lines"]
    port  = params["port"]

    if port is None:
        instances = _llm_instances(cfg)
        if len(instances) == 1:
            port = instances[0]["port"]
        elif not instances:
            print(dim("No models loaded — nothing to tail."))
            return {"action": "llm.logs", "port": None}
        else:
            print(fail("Multiple models loaded — specify --port:"))
            for i in instances:
                print(f"    --port {i['port']}  {cyan(i['name'])} {dim(i['quant'])}")
            return {"action": "llm.logs", "port": None,
                    "ambiguous": [int(i["port"]) for i in instances]}

    log_path = _port_log(cfg, port)
    print(dim(f"Tailing {log_path} on {cfg['dgx_host']} — Ctrl+C to stop\n"))
    subprocess.run(["ssh", f"{cfg['dgx_user']}@{cfg['dgx_host']}",
                    f"tail -n {lines} -f {log_path}"])
    return {"action": "llm.logs", "port": int(port)}


def open_ui(params, cfg):
    """Open a model's built-in llama-server chat UI in your browser."""
    port = params["port"]
    if port is None:
        instances = _llm_instances(cfg)
        if len(instances) == 1:
            port = instances[0]["port"]
        elif not instances:
            print(dim("No models loaded.")); return {"action": "llm.open", "port": None}
        else:
            print(fail("Multiple models loaded — specify --port:"))
            for i in instances:
                print(f"    --port {i['port']}  {cyan(i['name'])} {dim(i['quant'])}")
            return {"action": "llm.open", "port": None,
                    "ambiguous": [int(i["port"]) for i in instances]}
    url = f"http://{cfg['dgx_host']}:{port}"
    print(f"Opening {cyan(url)}")
    webbrowser.open(url)
    return {"action": "llm.open", "port": int(port), "url": url}


def pull_models(params, cfg):
    """Download catalog LLM model(s) into models_dir, one dir per model."""
    catalog, src = _models_catalog()
    models = catalog.get("llm", {}).get("models", [])
    names = [m["name"] for m in models]
    grab_all = params["all"]
    picks = params["names"]
    unknown = [p for p in picks if p not in names]

    if not models or unknown or (not grab_all and not picks):
        print("spark llm pull-models [<name> ...] [--all]")
        print(f"\n  Download LLM model(s) from the catalog into models_dir/<name>.")
        print(f"  Source: {dim(str(src))}")
        if src.name == "models.example.json":
            print(dim("  (repo example — copy to ~/.config/spark.models.json to customize)"))
        print()
        for m in models:
            print(f"    {cyan(m['name'])}   {dim(m['label'])}")
            if m.get("why"):
                print(f"        {dim(m['why'])}")
        if names:
            print(f"\n  Pull one or more: {cyan('spark llm pull-models ' + names[0])}")
            print(f"  Pull everything:  {cyan('spark llm pull-models --all')}  {dim('(these are large — mind the sizes)')}")
        if unknown:
            print(red(f"\n  Unknown model(s): {', '.join(unknown)}"))
        return {"action": "llm.pull_models", "listed": names}

    chosen = models if grab_all else [m for m in models if m["name"] in picks]
    jobs = [{"repo": m["repo_id"], "dest": f"{cfg['models_dir']}/{m['name']}",
             "glob": m["glob"], "label": m["label"], "flat": m.get("flat", False)} for m in chosen]
    _run_pull(cfg, jobs, done_hint=f"List them with {cyan('spark models')}, then {cyan('spark llm serve <name>')}.")
    return {"action": "llm.pull_models", "pulled": [m["name"] for m in chosen]}


HANDLERS = {
    "llm.serve":       serve,
    "llm.list":        ls,
    "llm.unload":      unload,
    "llm.stop":        stop,
    "llm.logs":        logs,
    "llm.open":        open_ui,
    "llm.pull_models": pull_models,
}
