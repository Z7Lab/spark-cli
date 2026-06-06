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
    _models_catalog, _run_pull,
)

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
    """Tail the ComfyUI container logs."""
    compose_dir = cfg["comfy_dir"]
    lines = params["lines"]
    print(dim(f"Tailing ComfyUI logs — Ctrl+C to stop\n"))
    subprocess.run(["ssh", f"{cfg['dgx_user']}@{cfg['dgx_host']}",
                    _docker_env(cfg) + f"cd {compose_dir} && docker compose logs --tail={lines} -f"])
    return {"action": "comfy.logs", "port": _PORT}


def generate(params, cfg):
    """Generate a FLUX.2 image via the ComfyUI API and download the PNG locally."""
    import random, urllib.request, urllib.parse, urllib.error

    prompt   = params["prompt"]
    width    = params["width"]
    height   = params["height"]
    steps    = params["steps"]
    guidance = params["guidance"]
    seed     = params["seed"] if params["seed"] is not None else random.randint(1, 2**31 - 1)
    out      = params["out"]
    model    = params["model"]
    encoder  = params["encoder"]
    vae      = params["vae"]

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
                "seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["4", 0]}},
        "11": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": "spark_gen"}},
    }

    def _get(path):
        return json.load(urllib.request.urlopen(base + path, timeout=60))

    print(f"Generating  {dim(f'{width}x{height}, {steps} steps, guidance {guidance}, seed {seed}')}")
    print(f"  Prompt: {cyan(prompt)}")
    try:
        req = urllib.request.Request(base + "/prompt", data=json.dumps({"prompt": graph}).encode(),
                                     headers={"Content-Type": "application/json"})
        pid = json.load(urllib.request.urlopen(req, timeout=60))["prompt_id"]
    except urllib.error.HTTPError as e:
        print(fail(f"ComfyUI rejected the request: {e.read().decode()[:600]}")); sys.exit(1)
    except urllib.error.URLError as e:
        print(fail(f"Cannot reach ComfyUI at {base} ({e.reason}).  Check: {cyan('spark comfy status')}")); sys.exit(1)

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
    return {"action": "comfy.generate", "out": out, "seed": seed,
            "width": width, "height": height, "steps": steps,
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

    # 1) upload the still to ComfyUI's input folder (multipart)
    boundary = "----spark" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
            f"filename=\"{p.name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode() + p.read_bytes() + \
           (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\n"
            f"true\r\n--{boundary}--\r\n").encode()
    try:
        up = json.load(urllib.request.urlopen(urllib.request.Request(
            base + "/upload/image", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}), timeout=60))
    except urllib.error.URLError as e:
        print(fail(f"Cannot reach ComfyUI at {base} ({getattr(e, 'reason', e)}).  Check: {cyan('spark comfy status')}"))
        sys.exit(1)
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
    try:
        pid = json.load(urllib.request.urlopen(urllib.request.Request(
            base + "/prompt", data=json.dumps({"prompt": graph}).encode(),
            headers={"Content-Type": "application/json"}), timeout=60))["prompt_id"]
    except urllib.error.HTTPError as e:
        print(fail(f"ComfyUI rejected the workflow: {e.read().decode()[:700]}")); sys.exit(1)
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


HANDLERS = {
    "comfy.start":       start,
    "comfy.stop":        stop,
    "comfy.status":      status,
    "comfy.queue":       queue,
    "comfy.logs":        logs,
    "comfy.generate":    generate,
    "comfy.animate":     animate,
    "comfy.pull_models": pull_models,
    "comfy.qr_art":      qr_art,
}
