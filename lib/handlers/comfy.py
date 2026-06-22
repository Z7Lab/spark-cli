"""comfy handlers — start, stop, status, logs, generate, animate, pull-models."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from sparkcore import (
    REPO_ROOT, bold, dim, red, green, yellow, cyan, ok, warn, fail,
    ssh, docker_probe, _docker_env, print_docker_remedy,
    _models_catalog, _run_pull, _human,
)

# Few-step distilled FLUX.2 LoRA used by `generate --turbo` (the "step distillation"
# speed lever). Public, in Comfy-Org/flux2-dev → fetched by `comfy pull-models
# --set generate`. Lives in ComfyUI's models/loras/ like any other LoRA.
_TURBO_LORA = "Flux2TurboComfyv2.safetensors"

# `generate --base` model profiles: which UNET / text-encoder / VAE to load. Both use
# the flux2 CLIP type. FLUX.2-dev (default, fp8) is what the box serves; klein-4B is the
# Apache-2.0 base `spark train` defaults to — so klein LoRAs render here too.
# Fetch a profile's files with `comfy pull-models --set <its set>`.
_BASES = {
    "flux2-dev":      {"model": "flux2_dev_fp8mixed.safetensors",
                       "encoder": "mistral_3_small_flux2_bf16.safetensors",
                       "vae": "flux2-vae.safetensors", "set": "generate"},
    "flux2-klein-4b": {"model": "flux-2-klein-base-4b.safetensors",
                       "encoder": "qwen_3_4b.safetensors",
                       "vae": "flux2-vae.safetensors", "set": "generate-klein"},
}

def start(params, cfg):
    """Start AEON-Spark ComfyUI via docker compose (port 8188)."""
    compose_dir = cfg["comfy_dir"]
    port = cfg["comfy_port"]
    # Pre-flight: a dead/unreachable daemon must fail fast with the real
    # remedy — never fall through to polling :8188 for 90s (bug A1).
    state, raw = docker_probe(cfg)
    if state != "ok":
        print(fail("Cannot start ComfyUI — Docker is not usable:"))
        print_docker_remedy(cfg, state)
        sys.exit(1)

    existing = ssh(cfg, _docker_env(cfg) + f"docker ps --filter name=comfy --format '{{{{.Names}}}}' 2>/dev/null | head -1")
    if existing:
        print(warn(f"ComfyUI container already running: {existing}"))
        ui_url = f"http://{cfg['dgx_host']}:{port}"
        print(f"  UI: {cyan(ui_url)}")
        return {"action": "comfy.start", "port": port, "status": "already_running",
                "container": existing, "url": ui_url}

    print(f"Starting AEON-Spark ComfyUI on port {port}...")
    result = ssh(cfg, _docker_env(cfg) + f"cd {compose_dir} && docker compose up -d 2>&1")
    if "error" in result.lower() or "permission denied" in result.lower():
        # Daemon was up at pre-flight but compose still failed — reclassify
        # so we print the matching remedy. If the daemon is still fine the
        # failure is compose/image-level, so show the raw error alone.
        state2, _ = docker_probe(cfg)
        print(fail("Docker error starting ComfyUI:"))
        print(f"  {result}")
        if state2 != "ok":
            print_docker_remedy(cfg, state2)
        sys.exit(1)

    for i in range(30):
        time.sleep(3)
        health = ssh(cfg, f"curl -sf http://localhost:{port}/ -o /dev/null -w '%{{http_code}}' || echo 0")
        if health.strip() in ("200", "101"):
            print(f"\n  {ok('ComfyUI ready')}")
            ready_url = f"http://{cfg['dgx_host']}:{port}"
            print(f"  UI  {cyan(ready_url)}")
            return {"action": "comfy.start", "port": port, "status": "ready",
                    "url": ready_url}
        print(f"\r  Starting... ({(i+1)*3}s)", end="", flush=True)
    print(f"\n  {warn('Still starting')} — run: {cyan('spark comfy logs')}")
    return {"action": "comfy.start", "port": port, "status": "starting"}


def stop(params, cfg):
    """Stop the running ComfyUI container."""
    compose_dir = cfg["comfy_dir"]
    result = ssh(cfg, _docker_env(cfg) + f"cd {compose_dir} && docker compose down 2>&1")
    if "error" in result.lower() or "permission denied" in result.lower():
        state, _ = docker_probe(cfg)
        print(fail(f"Docker error: {result}"))
        if state != "ok":
            print_docker_remedy(cfg, state)
        sys.exit(1)
    print(ok("ComfyUI stopped."))
    return {"action": "comfy.stop", "stopped": True}


def status(params, cfg):
    """Show ComfyUI state and UI URL."""
    port = cfg["comfy_port"]
    container = ssh(cfg, _docker_env(cfg) + "docker ps --filter name=comfy --format '{{.Names}} {{.Status}}' 2>/dev/null | head -1")
    result = {"action": "comfy.status", "port": port, "container": container or None,
              "running": bool(container), "ready": False, "docker": None}
    if container:
        print(f"  ComfyUI   {ok(container)}")
        health = ssh(cfg, f"curl -sf http://localhost:{port}/ -o /dev/null -w '%{{http_code}}' || echo 0")
        if health.strip() in ("200", "101"):
            comfy_ui = f"http://{cfg['dgx_host']}:{port}"
            result["ready"] = True
            result["url"] = comfy_ui
            print(f"  UI        {ok(comfy_ui)}")
        else:
            print(f"  UI        {warn('loading...')}")
    else:
        # Distinguish "no container, daemon fine" from "daemon down/denied"
        # — a down or unreachable daemon must not read as 'not running' (bug A2).
        state, _ = docker_probe(cfg)
        result["docker"] = state
        if state == "ok":
            print(f"  ComfyUI   {dim('not running')}  Run: {cyan('spark comfy start')}")
        else:
            print(f"  ComfyUI   {warn('unavailable — Docker not usable')}")
            print_docker_remedy(cfg, state)
    return result


def queue(params, cfg):
    """Show ComfyUI's render queue (running + pending prompts)."""
    port = cfg["comfy_port"]
    raw = ssh(cfg, f"curl -sf http://localhost:{port}/queue 2>/dev/null || true")
    try:
        data = json.loads(raw) if raw.strip() else {}
    except (ValueError, AttributeError):
        data = None
    if data is None:
        print(warn("ComfyUI did not return a queue — is it running? "
                   f"Check: {cyan('spark comfy status')}"))
        return {"action": "comfy.queue", "port": port, "reachable": False,
                "running": [], "pending": []}
    running = [r[1] for r in data.get("queue_running", []) if len(r) > 1]
    pending = [r[1] for r in data.get("queue_pending", []) if len(r) > 1]
    if running:
        print(f"  running   {ok(str(len(running)))}")
        for pid in running:
            print(f"    {dim(pid)}")
    else:
        print(f"  running   {dim('idle')}")
    print(f"  pending   {ok(str(len(pending))) if pending else dim('0')}")
    for pid in pending:
        print(f"    {dim(pid)}")
    return {"action": "comfy.queue", "port": port, "reachable": True,
            "running": running, "pending": pending}


def logs(params, cfg):
    """Show (or follow) the ComfyUI container logs."""
    compose_dir = cfg["comfy_dir"]
    lines = params["lines"]
    follow = "-f" if params.get("follow") else ""
    if follow:
        print(dim("Following ComfyUI logs — Ctrl+C to stop\n"))
    subprocess.run(["ssh", f"{cfg['dgx_user']}@{cfg['dgx_host']}",
                    _docker_env(cfg) + f"cd {compose_dir} && docker compose logs --tail={lines} {follow}"])
    return {"action": "comfy.logs", "port": cfg["comfy_port"], "followed": bool(follow)}


def generate(params, cfg):
    """Generate a FLUX.2 image via the ComfyUI API and download the PNG locally."""
    import random, urllib.request, urllib.parse, urllib.error

    prompt   = params["prompt"]
    width    = params["width"]
    height   = params["height"]
    seed     = params["seed"] if params["seed"] is not None else random.randint(1, 2**31 - 1)
    out      = params["out"]
    base     = params.get("base") or "flux2-dev"
    prof     = _BASES.get(base)
    if prof is None:
        print(fail(f"Unknown --base '{base}'. Options: {', '.join(_BASES)}")); sys.exit(1)
    # --base picks the UNET/encoder/VAE; explicit --model/--encoder/--vae still override.
    model    = params["model"]   or prof["model"]
    encoder  = params["encoder"] or prof["encoder"]
    vae      = params["vae"]     or prof["vae"]
    init     = params.get("init")
    inpaint  = params.get("inpaint")
    lora     = params.get("lora")
    lora_strength = params.get("lora_strength")
    if lora_strength is None:
        lora_strength = 1.0
    # --turbo applies a few-step distilled LoRA and drops the step/guidance defaults
    # (the "distillation" speed lever). FLUX.2-dev only — the turbo LoRA is a dev LoRA;
    # there's no klein turbo. Explicit --steps/--guidance still win (null = unset).
    turbo    = params.get("turbo")
    if turbo and base != "flux2-dev":
        print(warn(f"--turbo is FLUX.2-dev only (no klein turbo LoRA) — ignoring for base '{base}'."))
        turbo = False
    steps    = params["steps"]    if params["steps"]    is not None else (8   if turbo else 20)
    guidance = params["guidance"] if params["guidance"] is not None else (1.5 if turbo else 3.5)
    region_str = params.get("region") or "0.4,0.4,0.55,0.6"
    denoise  = params.get("denoise")
    # img2img keeps more of the init the lower the denoise (0.65 default). Inpaint
    # only repaints the masked region, so it fully regenerates it (denoise 1.0).
    if init and inpaint and denoise is None:
        denoise = 1.0
    elif init and denoise is None:
        denoise = 0.65
    elif denoise is None:
        denoise = 1.0
    if inpaint:
        if not init:
            print(fail("--inpaint needs --init <image> (the image to repaint into)")); sys.exit(1)
        try:
            rx, ry, rw, rh = (float(v) for v in region_str.split(","))
        except Exception:
            print(fail("--region must be 'x,y,w,h' fractions, e.g. 0.4,0.4,0.55,0.6")); sys.exit(1)

    base = f"http://{cfg['dgx_host']}:{cfg['comfy_port']}"
    graph = {
        "1":  {"class_type": "UNETLoader", "inputs": {"unet_name": model, "weight_dtype": "default"}},
        "2":  {"class_type": "ModelSamplingFlux", "inputs": {"model": ["1", 0], "max_shift": 1.15, "base_shift": 0.5, "width": width, "height": height}},
        "3":  {"class_type": "CLIPLoader", "inputs": {"clip_name": encoder, "type": "flux2"}},
        "4":  {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "5":  {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": prompt}},
        "6":  {"class_type": "FluxGuidance", "inputs": {"conditioning": ["5", 0], "guidance": guidance}},
        "7":  {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": ""}},
        "8":  {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "9":  {"class_type": "KSampler", "inputs": {"model": ["2", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["8", 0],
                "seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": denoise}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["4", 0]}},
        "11": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": "spark_gen"}},
    }

    # LoRAs load as a chain of LoraLoaderModelOnly between the UNET loader and
    # ModelSamplingFlux — FLUX.2 LoRAs are model-only (no CLIP side), so the text
    # encoder is untouched. --turbo's few-step LoRA goes first, then a style/subject
    # LoRA (e.g. from `spark train`, whose trigger word goes in the prompt).
    loras = []
    if turbo:
        loras.append((_TURBO_LORA, 1.0))
    if lora:
        loras.append((lora, lora_strength))
    prev = ["1", 0]
    for i, (lname, lstr) in enumerate(loras):
        nid = str(19 + i)
        graph[nid] = {"class_type": "LoraLoaderModelOnly",
                      "inputs": {"model": prev, "lora_name": lname, "strength_model": lstr}}
        prev = [nid, 0]
    graph["2"]["inputs"]["model"] = prev

    def _get(path):
        return json.load(urllib.request.urlopen(base + path, timeout=60))

    print(f"Generating  {dim(f'{width}x{height}, {steps} steps, guidance {guidance}, seed {seed}')}")
    print(f"  Prompt: {cyan(prompt)}")
    # ComfyUI can accept TCP connections before it's ready to serve (cold
    # container, model load) and then drop them mid-response
    # (http.client.RemoteDisconnected, an OSError that the old code didn't
    # catch). Wait until a cheap endpoint answers cleanly before submitting, so
    # a slow cold start doesn't crash the client or orphan a queued prompt.
    print(dim("  Waiting for ComfyUI to be ready..."))
    rdeadline = time.time() + 600
    while time.time() < rdeadline:
        try:
            with urllib.request.urlopen(base + "/system_stats", timeout=10) as r:
                if r.status == 200:
                    break
        except Exception:
            pass
        time.sleep(5)
    else:
        print(fail(f"ComfyUI at {base} didn't become ready (10 min).  "
                   f"Check: {cyan('spark comfy status')} / {cyan('spark comfy logs')}")); sys.exit(1)

    # Validate each LoRA name against ComfyUI's own node catalog (HTTP-only, keeps
    # generate SSH-free) so a typo or a not-yet-installed LoRA fails fast with the
    # available names — instead of a cryptic mid-graph ComfyUI rejection.
    if loras:
        try:
            info = _get("/object_info/LoraLoaderModelOnly")
            avail = info["LoraLoaderModelOnly"]["input"]["required"]["lora_name"][0]
        except Exception:
            avail = []
        for lname, lstr in loras:
            if avail and lname not in avail:
                print(fail(f"LoRA '{lname}' is not in ComfyUI's models/loras/."))
                print(f"  Available: {cyan(', '.join(avail)) if avail else dim('(none)')}")
                if lname == _TURBO_LORA:
                    print(dim(f"  Get it with {cyan('spark comfy pull-models --set generate')}."))
                else:
                    print(dim(f"  Train one with {cyan('spark train start')}, or drop a .safetensors into "
                              f"{cfg['comfy_dir']}/workspace/models/loras/."))
                sys.exit(1)
            tag = "turbo" if lname == _TURBO_LORA else "lora"
            print(f"  {tag:<7}{cyan(lname)}  {dim(f'(strength {lstr})')}")

    # img2img: upload the init still, encode it to a latent, and sample from that
    # at --denoise (keeps the source composition; the prompt edits it). Without
    # --init the graph samples from an empty latent (plain text-to-image).
    if init:
        import uuid, mimetypes
        ip = Path(init).expanduser()
        if not ip.is_file():
            print(fail(f"Init image not found: {init}")); sys.exit(1)
        boundary = "----spark" + uuid.uuid4().hex
        ictype = mimetypes.guess_type(str(ip))[0] or "application/octet-stream"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
                f"filename=\"{ip.name}\"\r\nContent-Type: {ictype}\r\n\r\n").encode() + ip.read_bytes() + \
               (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\n"
                f"true\r\n--{boundary}--\r\n").encode()
        up = None
        for _ in range(5):
            try:
                up = json.load(urllib.request.urlopen(urllib.request.Request(
                    base + "/upload/image", data=body,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}), timeout=120))
                break
            except (urllib.error.URLError, OSError):
                time.sleep(5)
        if up is None:
            print(fail(f"Couldn't upload the init image to ComfyUI at {base}.")); sys.exit(1)
        graph["12"] = {"class_type": "LoadImage", "inputs": {"image": up.get("name", ip.name)}}
        if inpaint:
            # Repaint only a rectangular region: scale the init to W×H, build a
            # white box mask over the region on a black field, and noise-mask the
            # latent so the sampler regenerates the box and keeps the rest exact.
            W, H = width, height
            bx, by, bw, bh = int(rx * W), int(ry * H), int(rw * W), int(rh * H)
            graph["18"] = {"class_type": "ImageScale", "inputs": {"image": ["12", 0],
                           "upscale_method": "lanczos", "width": W, "height": H, "crop": "center"}}
            graph["13"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["18", 0], "vae": ["4", 0]}}
            graph["14"] = {"class_type": "SolidMask", "inputs": {"value": 0.0, "width": W, "height": H}}
            graph["15"] = {"class_type": "SolidMask", "inputs": {"value": 1.0, "width": bw, "height": bh}}
            graph["16"] = {"class_type": "MaskComposite", "inputs": {"destination": ["14", 0],
                           "source": ["15", 0], "x": bx, "y": by, "operation": "add"}}
            graph["17"] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["13", 0], "mask": ["16", 0]}}
            graph["9"]["inputs"]["latent_image"] = ["17", 0]
            print(dim(f"  inpaint region {region_str} of {cyan(ip.name)}  (denoise {denoise})"))
        else:
            graph["13"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["12", 0], "vae": ["4", 0]}}
            graph["9"]["inputs"]["latent_image"] = ["13", 0]
            print(dim(f"  img2img from {cyan(ip.name)}  (denoise {denoise})"))

    # Submit; tolerate a connection dropped during the still-warming window. The
    # readiness gate above makes a duplicate queue from a retry unlikely.
    pid = None
    for _ in range(5):
        try:
            req = urllib.request.Request(base + "/prompt", data=json.dumps({"prompt": graph}).encode(),
                                         headers={"Content-Type": "application/json"})
            pid = json.load(urllib.request.urlopen(req, timeout=120))["prompt_id"]
            break
        except urllib.error.HTTPError as e:
            print(fail(f"ComfyUI rejected the request: {e.read().decode()[:600]}")); sys.exit(1)
        except (urllib.error.URLError, OSError):
            time.sleep(5)
    if not pid:
        print(fail(f"Couldn't submit to ComfyUI at {base} after retries.  "
                   f"Check: {cyan('spark comfy status')} / {cyan('spark comfy logs')}")); sys.exit(1)

    print(dim("  Submitted — sampling (first run loads models into VRAM, a few min)..."))
    t0 = time.time()
    img = None
    while time.time() - t0 < 1800:
        time.sleep(4)
        try:
            h = _get("/history/" + pid)
        except Exception:
            continue
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") == "error":
                print("\n" + fail("Generation failed:"))
                print(f"  {json.dumps(st.get('messages', []))[:800]}"); sys.exit(1)
            for node_out in h[pid].get("outputs", {}).values():
                for im in node_out.get("images", []):
                    img = im
                    break
                if img:
                    break
            if img:
                break
            # The prompt is in /history (so it finished) but carries no image and
            # no error: ComfyUI served an identical prompt from cache (~0s) and
            # re-emitted no SaveImage output. Surface it now instead of polling to
            # the 30-min timeout.
            if st.get("completed") or st.get("status_str") == "success":
                print("\n" + fail("ComfyUI finished the prompt but returned no image."))
                print(dim("  Most likely an identical prompt served from cache (no new render)."))
                print(f"  Re-run with a different {cyan('--seed')} (or omit it for a random one).")
                sys.exit(1)
        print(f"\r  ...{int(time.time() - t0)}s", end="", flush=True)
    print()
    if not img:
        print(fail("Timed out waiting for the image (30 min).")); sys.exit(1)

    q = urllib.parse.urlencode({"filename": img["filename"],
                                "subfolder": img.get("subfolder", ""),
                                "type": img.get("type", "output")})
    data = urllib.request.urlopen(base + "/view?" + q, timeout=120).read()
    if not out:
        out = str(Path.cwd() / img["filename"])
    Path(out).write_bytes(data)
    print(ok(f"Image saved: {cyan(out)}  {dim(f'({len(data)//1024} KB, {int(time.time() - t0)}s)')}"))
    print(f"  On DGX: {dim(cfg['comfy_dir'] + '/workspace/output/' + img['filename'])}")
    return {"action": "comfy.generate", "out": out, "seed": seed, "base": base,
            "width": width, "height": height, "steps": steps, "turbo": bool(turbo),
            "lora": lora, "lora_strength": lora_strength if lora else None,
            "bytes": len(data), "filename": img["filename"]}


def animate(params, cfg):
    """Animate a still image into a short video (LTX-2.3 image-to-video)."""
    import random, uuid, mimetypes, urllib.request, urllib.parse, urllib.error

    image_path = params["image"]
    prompt = params["prompt"]
    seed = params["seed"] if params["seed"] is not None else random.randint(1, 2**31 - 1)
    out  = params["out"]

    p = Path(image_path).expanduser()
    if not p.is_file():
        print(fail(f"Image not found: {image_path}")); sys.exit(1)

    tmpl = REPO_ROOT / "templates" / "ltx2_i2v_api.json"
    if not tmpl.is_file():
        print(fail(f"Workflow template missing: {tmpl}")); sys.exit(1)
    graph = json.loads(tmpl.read_text())
    base = f"http://{cfg['dgx_host']}:{cfg['comfy_port']}"

    # ComfyUI can accept connections before it's ready and drop them mid-response
    # during a cold model load — wait until it answers cleanly before uploading.
    print(dim("  Waiting for ComfyUI to be ready..."))
    rdeadline = time.time() + 600
    while time.time() < rdeadline:
        try:
            with urllib.request.urlopen(base + "/system_stats", timeout=10) as r:
                if r.status == 200:
                    break
        except Exception:
            pass
        time.sleep(5)
    else:
        print(fail(f"ComfyUI at {base} didn't become ready (10 min).  "
                   f"Check: {cyan('spark comfy status')} / {cyan('spark comfy logs')}")); sys.exit(1)

    # 1) upload the still to ComfyUI's input folder (multipart)
    boundary = "----spark" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
            f"filename=\"{p.name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode() + p.read_bytes() + \
           (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\n"
            f"true\r\n--{boundary}--\r\n").encode()
    up = None
    for _ in range(5):
        try:
            up = json.load(urllib.request.urlopen(urllib.request.Request(
                base + "/upload/image", data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}), timeout=120))
            break
        except (urllib.error.URLError, OSError):
            time.sleep(5)
    if up is None:
        print(fail(f"Couldn't upload the still to ComfyUI at {base}.  Check: {cyan('spark comfy status')}")); sys.exit(1)
    uploaded = up.get("name", p.name)

    # 2) inject image + prompt + seed into the frozen graph
    for n in graph.values():
        ct = n.get("class_type")
        if ct == "LoadImage":
            n["inputs"]["image"] = uploaded
        elif ct == "PrimitiveStringMultiline":
            n["inputs"]["value"] = prompt
        elif ct == "RandomNoise":
            n["inputs"]["noise_seed"] = seed

    print(f"Animating  {cyan(p.name)}  {dim('(LTX-2.3 i2v — sampling + upscale + decode, a few min)')}")
    print(f"  Prompt: {cyan(prompt)}")
    print(f"  Seed:   {seed}  {dim('(reproduce this take with --seed ' + str(seed) + ')')}")
    pid = None
    for _ in range(5):
        try:
            pid = json.load(urllib.request.urlopen(urllib.request.Request(
                base + "/prompt", data=json.dumps({"prompt": graph}).encode(),
                headers={"Content-Type": "application/json"}), timeout=120))["prompt_id"]
            break
        except urllib.error.HTTPError as e:
            print(fail(f"ComfyUI rejected the workflow: {e.read().decode()[:700]}")); sys.exit(1)
        except (urllib.error.URLError, OSError):
            time.sleep(5)
    if not pid:
        print(fail(f"Couldn't submit to ComfyUI at {base} after retries.  Check: {cyan('spark comfy status')}")); sys.exit(1)
    print(dim("  Submitted — first run loads ~44G of LTX models into VRAM..."))

    t0 = time.time(); vid = None
    while time.time() - t0 < 3600:
        time.sleep(6)
        try:
            h = json.load(urllib.request.urlopen(base + "/history/" + pid, timeout=60))
        except Exception:
            continue
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") == "error":
                print("\n" + fail("Animation failed:"))
                print(f"  {json.dumps(st.get('messages', []))[:1000]}"); sys.exit(1)
            for o in h[pid].get("outputs", {}).values():
                for key in ("videos", "gifs", "images"):
                    for v in o.get(key, []):
                        if str(v.get("filename", "")).lower().endswith((".mp4", ".webm", ".webp", ".gif")):
                            vid = v
            if vid:
                break
            # Finished but no video — e.g. an identical prompt+seed served from cache.
            if st.get("completed") or st.get("status_str") == "success":
                print("\n" + fail("ComfyUI finished but returned no video."))
                print(dim("  Likely an identical prompt+seed served from cache."))
                print(f"  Re-run with a different {cyan('--seed')}."); sys.exit(1)
        print(f"\r  ...{int(time.time() - t0)}s", end="", flush=True)
    print()
    if not vid:
        print(fail("Timed out waiting for the video (60 min).")); sys.exit(1)

    q = urllib.parse.urlencode({"filename": vid["filename"], "subfolder": vid.get("subfolder", ""),
                                "type": vid.get("type", "output")})
    blob = urllib.request.urlopen(base + "/view?" + q, timeout=300).read()
    if not out:
        out = str(Path.cwd() / vid["filename"])
    Path(out).write_bytes(blob)
    print(ok(f"Video saved: {cyan(out)}  {dim(f'({len(blob)//1024} KB, {int(time.time() - t0)}s)')}"))
    print(f"  On DGX: {dim(cfg['comfy_dir'] + '/workspace/output/' + vid['filename'])}")
    return {"action": "comfy.animate", "out": out, "seed": seed,
            "image": str(p), "bytes": len(blob), "filename": vid["filename"]}


def pull_models(params, cfg):
    """Download the FLUX.2 / LTX-2.3 models the comfy commands need."""
    which = params["set"]
    catalog, _ = _models_catalog()
    comfy = catalog.get("comfy", {})
    sets = list(comfy.keys()) if which == "all" else [which]
    models_root = f"{cfg['comfy_dir']}/workspace/models"
    jobs = [
        {"repo": e["repo_id"], "dest": f"{models_root}/{e['subdir']}",
         "glob": e["glob"], "label": e["label"], "flat": True, "rename": e.get("rename")}
        for s in sets for e in comfy.get(s, [])
    ]
    _run_pull(cfg, jobs, done_hint=f"Verify with {cyan('spark comfy status')} and re-run a gen.")
    return {"action": "comfy.pull_models", "set": which, "sets": sets,
            "pulled": [j["dest"] for j in jobs]}


_QR_STYLES = {
    "cyberpunk": {"ckpt": "dreamshaper_8.safetensors",
        "prompt": ("a cyberpunk cyborg woman with white hair, sleek glossy bodysuit with glowing neon "
                   "accents, futuristic neon temple, teal and magenta volumetric lighting, reflective "
                   "floor, intricate sci-fi detail, cinematic, masterpiece, highly detailed")},
    "anime": {"ckpt": "Counterfeit-V3.0_fix_fp16.safetensors",
        "prompt": ("anime cyberpunk city at night, neon-lit skyscrapers with glowing grid windows, dense "
                   "futuristic megacity, teal and magenta neon signs, rain reflections, intricate detailed "
                   "architecture, masterpiece, best quality, sharp focus")},
}
_QR_MODES = {  # the two angles: reliable-stylised vs more-scene (lower scan rate — curate seeds)
    "stylized": {"qr_s": 1.35, "qr_end": 1.0, "br_s": 0.40, "br_end": 0.70},
    "art":      {"qr_s": 1.10, "qr_end": 0.90, "br_s": 0.50, "br_end": 0.75},
}


def qr_art(params, cfg):
    """Generate scannable QR-code art (SD1.5 + QR-Monster + brightness ControlNets)."""
    import random, uuid, urllib.request, urllib.parse, urllib.error
    try:
        import qrcode
        from PIL import Image
    except ImportError:
        print(fail("qr-art needs qrcode + Pillow:  pip install --break-system-packages qrcode pillow")); sys.exit(1)

    url, style, mode = params["url"], params["style"], params["mode"]
    seed = params["seed"] if params["seed"] is not None else random.randint(1, 2**31 - 1)
    out = str(Path(params["out"]).expanduser()) if params["out"] else str(Path.cwd() / "qr_art.png")
    if style not in _QR_STYLES:
        print(fail(f"Unknown --style '{style}' (one of: {', '.join(_QR_STYLES)})")); sys.exit(1)
    if mode not in _QR_MODES:
        print(fail(f"Unknown --mode '{mode}' (one of: {', '.join(_QR_MODES)})")); sys.exit(1)
    st, md = _QR_STYLES[style], _QR_MODES[mode]

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(url); qr.make(fit=True)
    ctrl = Path("/tmp/spark_qr_control.png")
    qr.make_image(fill_color="black", back_color="white").convert("RGB").resize((768, 768), Image.NEAREST).save(ctrl)

    base = f"http://{cfg['dgx_host']}:{cfg['comfy_port']}"
    boundary = "----spark" + uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{ctrl.name}\"\r\n"
            f"Content-Type: image/png\r\n\r\n").encode() + ctrl.read_bytes() + \
           (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{boundary}--\r\n").encode()
    try:
        up = json.load(urllib.request.urlopen(urllib.request.Request(
            base + "/upload/image", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}), timeout=60))
    except urllib.error.URLError as e:
        print(fail(f"Cannot reach ComfyUI at {base} ({getattr(e, 'reason', e)}).  Check: {cyan('spark comfy status')}")); sys.exit(1)
    uploaded = up.get("name", ctrl.name)

    graph = json.loads((REPO_ROOT / "templates" / "qr_art_api.json").read_text())
    graph.pop("_comment", None)
    graph["4"]["inputs"]["ckpt_name"] = st["ckpt"]
    graph["6"]["inputs"]["text"] = st["prompt"]
    graph["11"]["inputs"]["image"] = uploaded
    graph["14"]["inputs"].update(strength=md["qr_s"], end_percent=md["qr_end"])
    graph["16"]["inputs"].update(strength=md["br_s"], end_percent=md["br_end"])
    graph["3"]["inputs"]["seed"] = seed

    print(f"QR art  {cyan(url)}  {dim(f'(style={style} mode={mode} seed={seed})')}")
    try:
        pid = json.load(urllib.request.urlopen(urllib.request.Request(
            base + "/prompt", data=json.dumps({"prompt": graph}).encode(),
            headers={"Content-Type": "application/json"}), timeout=60))["prompt_id"]
    except urllib.error.HTTPError as e:
        print(fail(f"ComfyUI rejected the workflow: {e.read().decode()[:700]}")); sys.exit(1)

    t0 = time.time(); img = None
    while time.time() - t0 < 600:
        time.sleep(3)
        try:
            h = json.load(urllib.request.urlopen(base + "/history/" + pid, timeout=60))
        except Exception:
            continue
        if pid in h:
            sd = h[pid].get("status", {})
            if sd.get("status_str") == "error":
                print("\n" + fail("qr-art failed:")); print(f"  {json.dumps(sd.get('messages', []))[:1000]}"); sys.exit(1)
            for o in h[pid].get("outputs", {}).values():
                for v in o.get("images", []):
                    img = v
            if img:
                break
        print(f"\r  ...{int(time.time() - t0)}s", end="", flush=True)
    print()
    if not img:
        print(fail("Timed out (10 min).")); sys.exit(1)
    q = urllib.parse.urlencode({"filename": img["filename"], "subfolder": img.get("subfolder", ""), "type": img.get("type", "output")})
    blob = urllib.request.urlopen(base + "/view?" + q, timeout=120).read()
    Path(out).write_bytes(blob)

    scanned = None
    try:
        import cv2
        a = cv2.imread(out); g = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
        _, o = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        det = cv2.QRCodeDetector()
        scanned = bool(next((d for d in (det.detectAndDecode(c)[0] for c in (a, o, 255 - o)) if d), ""))
    except ImportError:
        pass
    mark = dim("scan unverified (no opencv)") if scanned is None else \
        (ok("scans ✓") if scanned else warn("did NOT scan — re-roll --seed, or use --mode stylized"))
    print(ok(f"Saved: {cyan(out)}  {dim(f'({len(blob)//1024} KB)')}  ") + mark)
    return {"action": "comfy.qr_art", "out": out, "url": url, "style": style,
            "mode": mode, "seed": seed, "scanned": scanned}


def refine(params, cfg):
    """Refine an image with a stronger model — img2img at a moderate denoise so the
    refiner fixes text and sharpens detail while keeping composition and most style.

    A thin wrapper over `generate --init`: full-image img2img (no region/inpaint),
    defaulting to FLUX.2-dev (the stronger text renderer) at denoise 0.5 — the level
    that makes text legible without drifting far from the source. Targeted region edits
    ("replace this object") are a separate, purpose-built edit verb, not this."""
    img = params["image"]
    denoise = params.get("denoise")
    if denoise is None:
        denoise = 0.5
    g = {
        "prompt": params.get("prompt") or "",
        "width": params["width"], "height": params["height"],
        "steps": params["steps"], "guidance": params["guidance"],
        "seed": params["seed"], "out": params["out"],
        "base": params.get("base") or "flux2-dev",
        "model": None, "encoder": None, "vae": None,
        "init": img, "denoise": denoise,
        "inpaint": False, "region": None,
        "lora": params.get("lora"), "lora_strength": params.get("lora_strength"),
        "turbo": False,
    }
    r = generate(g, cfg)
    r["action"] = "comfy.refine"
    r["refined_from"] = img
    r["denoise"] = denoise
    return r


def edit(params, cfg):
    """Edit an image by instruction with Qwen-Image-Edit 2509 (replace/change parts of
    an image, e.g. 'replace the sign with a clock'). The reference image is baked into
    the conditioning by TextEncodeQwenImageEditPlus, so it edits the whole image
    semantically — for a stronger text renderer over the whole frame use `comfy refine`."""
    import random, uuid, mimetypes, urllib.request, urllib.parse, urllib.error

    image  = params["image"]
    prompt = params["prompt"]
    seed   = params["seed"] if params.get("seed") is not None else random.randint(1, 2**31 - 1)
    steps  = params["steps"] if params.get("steps") is not None else 20
    cfgv   = params["cfg"] if params.get("cfg") is not None else 4.0
    out    = params.get("out")
    base   = f"http://{cfg['dgx_host']}:{cfg['comfy_port']}"

    ip = Path(image).expanduser()
    if not ip.is_file():
        print(fail(f"Image not found: {image}")); sys.exit(1)

    def _get(path):
        return json.load(urllib.request.urlopen(base + path, timeout=60))

    print(f"Editing  {cyan(ip.name)}  {dim(f'(steps {steps}, cfg {cfgv}, seed {seed})')}")
    print(f"  Edit: {cyan(prompt)}")
    print(dim("  Waiting for ComfyUI to be ready..."))
    rdeadline = time.time() + 600
    while time.time() < rdeadline:
        try:
            with urllib.request.urlopen(base + "/system_stats", timeout=10) as r:
                if r.status == 200:
                    break
        except Exception:
            pass
        time.sleep(5)
    else:
        print(fail(f"ComfyUI at {base} didn't become ready (10 min).  "
                   f"Check: {cyan('spark comfy status')}")); sys.exit(1)

    # Upload the image to edit (same multipart upload as generate --init).
    boundary = "----spark" + uuid.uuid4().hex
    ictype = mimetypes.guess_type(str(ip))[0] or "application/octet-stream"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
            f"filename=\"{ip.name}\"\r\nContent-Type: {ictype}\r\n\r\n").encode() + ip.read_bytes() + \
           (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\n"
            f"true\r\n--{boundary}--\r\n").encode()
    up = None
    for _ in range(5):
        try:
            up = json.load(urllib.request.urlopen(urllib.request.Request(
                base + "/upload/image", data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}), timeout=120))
            break
        except (urllib.error.URLError, OSError):
            time.sleep(5)
    if up is None:
        print(fail(f"Couldn't upload the image to ComfyUI at {base}.")); sys.exit(1)
    iname = up.get("name", ip.name)

    # Qwen-Image-Edit 2509 graph (full-quality path of ComfyUI's bundled template,
    # minus the optional Lightning 4-step LoRA + switch machinery). The reference
    # image is encoded into the conditioning by TextEncodeQwenImageEditPlus, so
    # KSampler runs at denoise 1.0 and the latent only sets output dimensions.
    g = {
        "1":  {"class_type": "UNETLoader", "inputs": {"unet_name": "qwen_image_edit_2509_fp8_e4m3fn.safetensors", "weight_dtype": "default"}},
        "2":  {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
        "3":  {"class_type": "CFGNorm", "inputs": {"model": ["2", 0], "strength": 1.0}},
        "4":  {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image"}},
        "5":  {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "6":  {"class_type": "LoadImage", "inputs": {"image": iname}},
        "7":  {"class_type": "FluxKontextImageScale", "inputs": {"image": ["6", 0]}},
        "8":  {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["4", 0], "vae": ["5", 0], "image1": ["7", 0], "prompt": prompt}},
        "9":  {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["4", 0], "vae": ["5", 0], "image1": ["7", 0], "prompt": ""}},
        "10": {"class_type": "VAEEncode", "inputs": {"pixels": ["7", 0], "vae": ["5", 0]}},
        "11": {"class_type": "KSampler", "inputs": {"model": ["3", 0], "positive": ["8", 0], "negative": ["9", 0],
               "latent_image": ["10", 0], "seed": seed, "steps": steps, "cfg": cfgv,
               "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["5", 0]}},
        "13": {"class_type": "SaveImage", "inputs": {"images": ["12", 0], "filename_prefix": "spark_edit"}},
    }

    pid = None
    for _ in range(5):
        try:
            req = urllib.request.Request(base + "/prompt", data=json.dumps({"prompt": g}).encode(),
                                         headers={"Content-Type": "application/json"})
            pid = json.load(urllib.request.urlopen(req, timeout=120))["prompt_id"]
            break
        except urllib.error.HTTPError as e:
            print(fail(f"ComfyUI rejected the request: {e.read().decode()[:600]}")); sys.exit(1)
        except (urllib.error.URLError, OSError):
            time.sleep(5)
    if not pid:
        print(fail(f"Couldn't submit to ComfyUI at {base}.")); sys.exit(1)

    print(dim("  Submitted — sampling (first run loads ~28 GB into memory, a few min)..."))
    t0 = time.time()
    img = None
    while time.time() - t0 < 1800:
        time.sleep(4)
        try:
            h = _get("/history/" + pid)
        except Exception:
            continue
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") == "error":
                print("\n" + fail("Edit failed:"))
                print(f"  {json.dumps(st.get('messages', []))[:800]}"); sys.exit(1)
            for node_out in h[pid].get("outputs", {}).values():
                for im in node_out.get("images", []):
                    img = im
            if img:
                break
            if st.get("completed") or st.get("status_str") == "success":
                print("\n" + fail("Finished but returned no image (cached?). "
                                  "Re-run with a different --seed.")); sys.exit(1)
        print(f"\r  ...{int(time.time() - t0)}s", end="", flush=True)
    print()
    if not img:
        print(fail("Timed out waiting for the edit (30 min).")); sys.exit(1)

    q = urllib.parse.urlencode({"filename": img["filename"], "subfolder": img.get("subfolder", ""),
                                "type": img.get("type", "output")})
    data = urllib.request.urlopen(base + "/view?" + q, timeout=120).read()
    if not out:
        out = str(Path.cwd() / img["filename"])
    Path(out).write_bytes(data)
    print(ok(f"Edited image saved: {cyan(out)}  {dim(f'({len(data)//1024} KB, {int(time.time() - t0)}s)')}"))
    print(dim(f"  On DGX: /opt/spark/comfyui/workspace/output/{img['filename']}"))
    return {"action": "comfy.edit", "out": out, "seed": seed, "image": str(ip),
            "steps": steps, "bytes": len(data), "filename": img["filename"]}


def _comfy_models_dir(cfg):
    return f"{cfg['comfy_dir']}/workspace/models"


def _referenced_models(cfg):
    """Basenames of model files referenced by the pull-models catalog or any frozen
    graph in templates/ — i.e. files some `spark comfy` command actually loads."""
    import os, re, glob
    refs = set()
    try:
        cat, _ = _models_catalog()
        for entries in cat.get("comfy", {}).values():
            for e in entries:
                g = e.get("glob") or ""
                if g:
                    refs.add(os.path.basename(g))
                if e.get("rename"):
                    refs.add(os.path.basename(e["rename"]))
    except Exception:
        pass
    for fn in glob.glob(os.path.join(REPO_ROOT, "templates", "*.json")):
        try:
            txt = open(fn, encoding="utf-8").read()
        except OSError:
            continue
        for m in re.findall(r'[A-Za-z0-9_.\-]+\.(?:safetensors|ckpt|pt|pth|gguf|bin)', txt):
            refs.add(os.path.basename(m))
    return refs


def _list_comfy_models(cfg):
    """[(size_bytes, relpath, basename, is_orphan), ...] for model files on the box,
    biggest first. Orphan = not referenced by catalog/templates AND not a user LoRA
    (loras/ holds trained/user weights — never auto-flagged)."""
    import shlex
    mdir = _comfy_models_dir(cfg)
    raw = ssh(cfg, "find " + shlex.quote(mdir) + r" -type f \( -name '*.safetensors' "
              r"-o -name '*.ckpt' -o -name '*.pt' -o -name '*.pth' -o -name '*.gguf' \) "
              r"-printf '%s\t%P\n' 2>/dev/null | sort -rn")
    refs = _referenced_models(cfg)
    rows = []
    import os
    for line in raw.strip().splitlines():
        if "\t" not in line:
            continue
        size_s, rel = line.split("\t", 1)
        try:
            size = int(size_s)
        except ValueError:
            continue
        base = os.path.basename(rel)
        is_lora = rel.split("/", 1)[0] == "loras"
        # A sharded HF checkpoint (model-00001-of-00005.safetensors, or any
        # *-NNNNN-of-NNNNN.*) is loaded by its directory, not by filename — never
        # flag those as orphans (the catalog/template won't name each shard).
        import re as _re
        is_shard = bool(_re.search(r'\d{3,}-of-\d{3,}', base))
        orphan = (base not in refs) and not is_lora and not is_shard
        rows.append((size, rel, base, orphan))
    return rows


def models(params, cfg):
    """List downloaded ComfyUI model files with sizes, flagging orphans (not used by
    any `spark comfy` command's catalog/template). Surfaces what's reclaimable."""
    rows = _list_comfy_models(cfg)
    if not rows:
        print(warn("No ComfyUI model files found (is the box reachable / path set?)"))
        return {"action": "comfy.models", "files": 0}
    total = sum(r[0] for r in rows)
    orphans = [r for r in rows if r[3]]
    orphan_bytes = sum(r[0] for r in orphans)
    cur = None
    for size, rel, base, orphan in rows:
        top = rel.split("/", 1)[0] if "/" in rel else "."
        if top != cur:
            cur = top
            print(bold(f"\n  {top}/"))
        tag = red("  orphan") if orphan else ""
        print(f"    {_human(size):>9}  {base}{tag}")
    print(bold(f"\n  {_human(total)} total") +
          (f"   {red(_human(orphan_bytes) + ' reclaimable')} in {len(orphans)} orphan(s)"
           if orphans else "   " + green("no orphans")))
    if orphans:
        print(dim(f"  Reclaim: {cyan('spark comfy rm --orphans')}  (or rm <file>)"))
    return {"action": "comfy.models", "files": len(rows), "bytes": total,
            "orphans": len(orphans), "orphan_bytes": orphan_bytes}


def rm(params, cfg):
    """Delete ComfyUI model file(s) from disk — a named file, or every orphan with
    --orphans. Destructive; confirms unless --yes. Never touches loras/ via --orphans."""
    import shlex, os
    target = params.get("file")
    do_orphans = params.get("orphans")
    assume_yes = params.get("yes")
    mdir = _comfy_models_dir(cfg)

    if do_orphans:
        sel = [(s, rel) for s, rel, base, orphan in _list_comfy_models(cfg) if orphan]
        label = "all orphans"
    elif target:
        sel = [(s, rel) for s, rel, base, orphan in _list_comfy_models(cfg)
               if base == target or rel == target or target.lower() in base.lower()]
        label = target
    else:
        print(fail("Give a <file> to delete, or --orphans. List them: "
                   + cyan("spark comfy models"))); sys.exit(1)
    if not sel:
        print(fail(f"No ComfyUI model matches '{label}'.  See {cyan('spark comfy models')}.")); sys.exit(1)

    total = sum(s for s, _ in sel)
    print(bold(f"Delete {len(sel)} ComfyUI model file(s) — {label}") + dim(f"  ({_human(total)})"))
    for s, rel in sel:
        print(f"    {_human(s):>9}  {rel}")
    print(dim("  Frees disk; irreversible (re-fetch with spark comfy pull-models)."))
    if not assume_yes:
        word = "orphans" if do_orphans else "delete"
        try:
            ans = input(f"\n  Type {bold(word)} to confirm: ").strip()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        if ans != word:
            print(red("  Cancelled — nothing deleted.")); sys.exit(1)

    paths = [shlex.quote(f"{mdir}/{rel}") for _, rel in sel]
    ssh(cfg, "rm -f " + " ".join(paths))
    free = ssh(cfg, f"df -h {shlex.quote(mdir)} 2>/dev/null | tail -1 | awk '{{print $4}}'")
    print(ok(f"Deleted {len(sel)} file(s) — freed {_human(total)}.") +
          dim(f"  Now {free.strip()} free."))
    return {"action": "comfy.rm", "files": len(sel), "freed_bytes": total}


HANDLERS = {
    "comfy.start":       start,
    "comfy.stop":        stop,
    "comfy.status":      status,
    "comfy.queue":       queue,
    "comfy.logs":        logs,
    "comfy.generate":    generate,
    "comfy.refine":      refine,
    "comfy.edit":        edit,
    "comfy.animate":     animate,
    "comfy.pull_models": pull_models,
    "comfy.models":      models,
    "comfy.rm":          rm,
    "comfy.qr_art":      qr_art,
}
