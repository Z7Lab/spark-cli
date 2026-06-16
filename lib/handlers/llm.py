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
    _kv_cache_bytes, _comfy_mem_bytes, _human, _port_log, _models_catalog, _run_pull,
    _engine_state,
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

    # Best-effort: flag if llama.cpp drifted from its pin (an out-of-band rebuild
    # is what broke serving before). Informational — never blocks the load.
    try:
        st = _engine_state(cfg, "llama")
        if st and st["state"] == "drifted":
            print(warn(f"llama.cpp drifted from pin (installed {st['installed'][:12]} ≠ "
                       f"pinned {st['pinned'][:12]}) — {cyan('spark engine status')}"))
    except Exception:
        pass

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
    # must leave a reserve margin free for the OS and co-running services. The
    # unified memory is shared, so ComfyUI's resident weights count against us too;
    # we surface that and let the user opt into freeing it with --free-comfy (never
    # automatic — same "no silent eviction" rule as for other models).
    weights = _du_bytes(cfg, _quant_glob(model_path, quant))
    kv      = _kv_cache_bytes(cfg, model_path, ctx, par)
    kv_est  = kv or int(weights * 0.15)   # conservative fallback when GGUF dims unreadable
    reserve = int(cfg.get("mem_reserve_gb", 8)) * 1024**3
    needed  = weights + kv_est
    avail   = _free_bytes(cfg)
    if needed and avail:
        comfy_mem = _comfy_mem_bytes(cfg) if needed + reserve > avail else 0
        if needed + reserve > avail and params["free_comfy"] and comfy_mem:
            print(warn(f"Freeing ComfyUI (~{_human(comfy_mem)}) to make room "
                       f"({cyan('--free-comfy')})..."))
            from . import comfy as _comfy
            _comfy.stop({}, cfg)
            time.sleep(2)
            avail, comfy_mem = _free_bytes(cfg), 0
        if needed + reserve > avail:
            kv_part = f" + ~{_human(kv_est)} KV @ ctx {ctx}×{par}" + ("" if kv else " est.")
            print(fail(f"{name} {quant} needs ~{_human(needed)} "
                       f"(~{_human(weights)} weights{kv_part}) plus a {_human(reserve)} "
                       f"reserve, but only {_human(avail)} is free."))
            if instances:
                print(dim("  Resident models (unload some to make room):"))
                for inst in instances:
                    sz   = _human(_du_bytes(cfg, _quant_glob(inst["model_path"], inst["quant"])))
                    hint = dim(f"spark llm unload --port {inst['port']}")
                    print(f"    :{inst['port']}  {cyan(inst['name'])} {dim(inst['quant'])}  {sz}   {hint}")
            if comfy_mem:
                print(f"    {cyan('ComfyUI')} is holding ~{_human(comfy_mem)} — free it: "
                      + cyan('spark comfy stop') + dim(", or re-run with ")
                      + cyan('--free-comfy'))
            print(dim("  Free memory, lower --ctx/--parallel, or adjust mem_reserve_gb."))
            sys.exit(1)

    session = f"llama-{port}"
    log_path = _port_log(cfg, port)
    print(f"Loading {bold(name)}  {cyan(quant)}  on port {port}...")

    # Newer llama.cpp builds are split into shared libs (libllama-server-impl.so
    # etc.) that ship beside the binary; the loader doesn't search a binary's own
    # directory, so point LD_LIBRARY_PATH there or the server dies with
    # "error while loading shared libraries". Harmless for self-contained builds.
    lib_dir = str(Path(cfg["server_bin"]).parent)
    serve_cmd = (
        f"LD_LIBRARY_PATH={lib_dir}${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}} "
        f"{cfg['server_bin']} "
        f"--model {model_path} "
        f"--port {port} --host 0.0.0.0 "
        f"--ctx-size {ctx} --n-gpu-layers 999 --parallel {par} "
        f"--jinja --tools all "
        f"2>&1 | tee {log_path}"
    )
    ssh_screen(cfg, session, serve_cmd)

    # Markers that mean the server will never come up — fail fast with the real
    # cause instead of waiting out the full timeout (these can contain the word
    # "loading", so match them explicitly rather than guessing from "error").
    FATAL = ("error while loading shared libraries", "failed to load model",
             "terminate called", "out of memory", "cudamalloc", "command not found")
    print(dim("Waiting for server to be ready..."))
    for i in range(90):
        time.sleep(2)
        log = ssh(cfg, f"tail -5 {log_path} 2>/dev/null || true")
        low = log.lower()
        if "server is listening" in low:
            print(f"\n  {ok('Model loaded and server ready')}")
            print(f"  API      http://{cfg['dgx_host']}:{port}/v1")
            chat_url = f"http://{cfg['dgx_host']}:{port}"
            print(f"  Chat UI  {cyan(chat_url)}  {dim('(open in browser)')}")
            return {"action": "llm.serve", "model": name, "quant": quant,
                    "port": port, "status": "ready"}
        if any(m in low for m in FATAL):
            bad = next((ln.strip() for ln in log.splitlines()
                        if any(m in ln.lower() for m in FATAL)), log.strip())
            print(fail(f"\nllama-server failed to start:"))
            print(dim(f"  {bad}"))
            print(f"  Full log: {cyan('spark llm logs --port ' + str(port))}")
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


def bench(params, cfg):
    """Measure a loaded model's generation speed (tokens/sec) over its API."""
    import json as _json
    import urllib.request

    instances = _llm_instances(cfg)
    if not instances:
        print(fail("No model loaded. Serve one first: ")
              + cyan("spark llm serve <model>"))
        sys.exit(1)

    port, name = params["port"], params["model"]
    if port:
        matches = [i for i in instances if i["port"] == str(port)]
    elif name:
        matches = [i for i in instances if name.lower() in i["name"].lower()]
    elif len(instances) == 1:
        matches = instances
    else:
        print(fail("Multiple models loaded — specify <model> or --port:"))
        for i in instances:
            print(f"    --port {i['port']}  {cyan(i['name'])} {dim(i['quant'])}")
        sys.exit(1)
    if not matches:
        print(fail("No loaded model matches that name/port."))
        print(f"  Run {cyan('spark llm list')} to see what's loaded.")
        sys.exit(1)

    inst = matches[0]
    bport = inst["port"]
    prompt, max_tokens, runs = params["prompt"], params["max_tokens"], params["runs"]
    url = f"http://{cfg['dgx_host']}:{bport}/v1/chat/completions"

    print(bold(f"Benchmarking {inst['name']} {cyan(inst['quant'])} on port {bport}"))
    print(dim(f"  prompt: {prompt!r}  ·  max_tokens={max_tokens}  ·  runs={runs}\n"))

    rates = []
    for r in range(1, runs + 1):
        body = _json.dumps({
            "model": inst["name"],
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "stream": False, "temperature": 0.7,
        }).encode()
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=900) as resp:
                data = _json.loads(resp.read())
        except Exception as e:
            print(fail(f"Request failed: {e}"))
            print(f"  Is the server ready? {cyan('spark llm logs --port ' + str(bport))}")
            sys.exit(1)
        wall = time.time() - t0

        # llama.cpp server reports precise gen-only timings; fall back to wall-clock.
        tm = data.get("timings") or {}
        toks = tm.get("predicted_n") or data.get("usage", {}).get("completion_tokens")
        if tm.get("predicted_per_second"):
            tps, gen_s = tm["predicted_per_second"], tm.get("predicted_ms", 0) / 1000
            src = "server"
        else:
            gen_s, tps = wall, (toks / wall if toks else None)
            src = "wall"
        rates.append(tps)
        tps_str = f"{tps:.2f} t/s" if tps else "n/a"
        print(f"  run {r}/{runs}  {bold(tps_str)}  "
              + dim(f"({toks} tok in {gen_s:.1f}s, {src})"))

    valid = [x for x in rates if x]
    avg = sum(valid) / len(valid) if valid else None
    if avg and runs > 1:
        print(ok(f"\n  mean {avg:.2f} t/s over {len(valid)} run(s)"))
    return {"action": "llm.bench", "model": inst["name"], "quant": inst["quant"],
            "port": int(bport), "prompt": prompt, "runs": runs,
            "tps": rates, "tps_mean": avg}


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
        print(dim("  (edit templates/models.json to customize)"))
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


def probe(params, cfg):
    """Verify a loaded model's capabilities via the external llm-probe tool.

    Optional capability: spark shells out to the `llm-probe` CLI (installed
    separately from PyPI) rather than bundling it, so the core stays
    dependency-free. We resolve the target llama-server instance exactly like
    `bench`, generate a throwaway llm-probe config pointing at that endpoint,
    and stream llm-probe's own report.
    """
    import shutil
    import subprocess

    if not shutil.which("llm-probe"):
        print(fail("llm-probe is not installed."))
        print("  It's an optional tool that verifies tool-calling and prompt")
        print("  adherence. Install it once, then re-run:\n")
        print(f"    {cyan('pipx install llm-probe')}   {dim('# recommended')}")
        print(f"    {cyan('pip install llm-probe')}    {dim('# or into the current env')}")
        sys.exit(1)

    results_dir = Path.home() / ".cache" / "spark" / "probe"

    if params.get("report"):
        return _probe_report(params, cfg, results_dir)

    # Optional pre-step: load the model first, reusing serve() so all of its
    # fit-check / quant / port logic lives in one place. Cold loads can outlast
    # serve()'s own wait window, so we additionally poll the endpoint until it
    # actually answers before probing.
    if params.get("serve"):
        if not params["model"]:
            print(fail("--serve needs a model name: ") + cyan("spark llm probe <model> --serve"))
            sys.exit(1)
        res = serve({"model": params["model"], "quant": params.get("quant"),
                     "port": params["port"], "ctx": 8192, "parallel": 4,
                     "free_comfy": False}, cfg)
        status, sport = res.get("status"), res.get("port")
        if status == "error":
            sys.exit(1)
        params["port"] = sport
        if status != "already_loaded":
            deadline = time.time() + max(params["timeout"], 180)
            if not _endpoint_ready(cfg["dgx_host"], sport, deadline):
                print(fail("Server still not reachable — it may need more time to load."))
                print(f"  Watch it: {cyan('spark llm logs --port ' + str(sport))}, then re-run the probe.")
                sys.exit(1)
        print()

    instances = _llm_instances(cfg)
    if not instances:
        print(fail("No model loaded. Serve one first: ")
              + cyan("spark llm serve <model>"))
        sys.exit(1)

    port, name = params["port"], params["model"]
    if port:
        matches = [i for i in instances if i["port"] == str(port)]
    elif name:
        matches = [i for i in instances if name.lower() in i["name"].lower()]
    elif len(instances) == 1:
        matches = instances
    else:
        print(fail("Multiple models loaded — specify <model> or --port:"))
        for i in instances:
            print(f"    --port {i['port']}  {cyan(i['name'])} {dim(i['quant'])}")
        sys.exit(1)
    if not matches:
        print(fail("No loaded model matches that name/port."))
        print(f"  Run {cyan('spark llm list')} to see what's loaded.")
        sys.exit(1)

    inst = matches[0]
    pport = inst["port"]
    provider = f"dgx-{pport}"
    api_base = f"http://{cfg['dgx_host']}:{pport}/v1"
    results_dir.mkdir(parents=True, exist_ok=True)

    # Throwaway config: a custom OpenAI-compatible provider for this one endpoint.
    # Written by hand (no yaml dep) — the schema is trivial and fixed.
    cfg_path = results_dir / f"{provider}.config.yaml"
    cfg_path.write_text(
        "providers:\n"
        f"  {provider}:\n"
        f"    api_base: {api_base}\n"
        "tests:\n"
        f"  timeout_local: {params['timeout']}\n"
        f"  runs_per_model: {params['runs']}\n"
        "output:\n"
        f"  dir: {results_dir}\n"
    )

    print(bold(f"Probing {inst['name']} {cyan(inst['quant'])} on port {pport}"))
    print(dim(f"  endpoint: {api_base}  ·  timeout={params['timeout']}s  ·  runs={params['runs']}\n"))

    cmd = ["llm-probe", "--config", str(cfg_path), "test", provider,
           "--timeout", str(params["timeout"]), "--runs", str(params["runs"])]
    if params.get("tests"):
        cmd += ["--tests", params["tests"]]
    if params.get("model_id"):
        cmd += ["--model", params["model_id"]]

    # Stream llm-probe's own (already well-formatted) report straight through.
    # Flush our buffered header first so it isn't reordered behind the child's output.
    sys.stdout.flush()
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        print(fail(f"\nllm-probe exited with status {rc}."))
        print(f"  Is the server ready? {cyan('spark llm logs --port ' + str(pport))}")
        sys.exit(rc)

    print(ok(f"\n  Probe complete — results cached in {dim(str(results_dir))}"))

    unloaded = False
    if params.get("unload"):
        print()
        unload({"port": int(pport), "quant": None, "name": None}, cfg)
        unloaded = True

    return {"action": "llm.probe", "model": inst["name"], "quant": inst["quant"],
            "port": int(pport), "provider": provider, "timeout": params["timeout"],
            "runs": params["runs"], "results_dir": str(results_dir),
            "unloaded": unloaded}


def _endpoint_ready(host, port, deadline) -> bool:
    """Poll an OpenAI-compatible endpoint until /v1/models answers (or time out)."""
    import urllib.request
    url = f"http://{host}:{port}/v1/models"
    print(dim("  Waiting for endpoint to answer..."))
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(3)
    return False


def _probe_report(params, cfg, results_dir):
    """`spark llm probe --report` — show cached results without re-probing.

    Reads straight from the cache, so it needs neither a loaded model nor SSH.
    Targets one provider with --port, otherwise reports every cached probe.
    """
    import subprocess

    # Result files are `<provider>.yaml`; our throwaway configs are
    # `<provider>.config.yaml` in the same dir — exclude those.
    cached = sorted(p for p in results_dir.glob("*.yaml")
                    if not p.name.endswith(".config.yaml")) if results_dir.exists() else []

    if params["port"]:
        providers = [f"dgx-{params['port']}"]
    elif params["model"]:
        print(fail("--report can't resolve a model by name (no live server is queried)."))
        print(f"  Use {cyan('spark llm probe --report --port <N>')}, or run it with no target.")
        sys.exit(1)
    else:
        providers = [p.stem for p in cached]

    if not providers:
        print(warn("No cached probe results yet."))
        print(f"  Run {cyan('spark llm probe <model>')} to create some.")
        return {"action": "llm.probe.report", "results_dir": str(results_dir), "providers": []}

    # Minimal config so llm-probe knows where to read; api_base is unused by report.
    results_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = results_dir / "_report.config.yaml"
    lines = ["providers:"]
    for p in providers:
        lines += [f"  {p}:", "    api_base: \"\""]
    lines += ["output:", f"  dir: {results_dir}", ""]
    cfg_path.write_text("\n".join(lines))

    rc = subprocess.run(["llm-probe", "--config", str(cfg_path), "report"]).returncode
    if rc != 0:
        print(fail(f"llm-probe report exited with status {rc}."))
        sys.exit(rc)
    return {"action": "llm.probe.report", "results_dir": str(results_dir),
            "providers": providers}


HANDLERS = {
    "llm.serve":       serve,
    "llm.bench":       bench,
    "llm.probe":       probe,
    "llm.list":        ls,
    "llm.unload":      unload,
    "llm.stop":        stop,
    "llm.logs":        logs,
    "llm.open":        open_ui,
    "llm.pull_models": pull_models,
}
