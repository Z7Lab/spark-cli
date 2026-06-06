# qr-art

```spec
{
  "name": "qr-art",
  "description": "Turn a URL into scannable QR-code art on the Spark (SD1.5 + ControlNets).",
  "requires": [
    { "what": "qrcode + Pillow (local)", "where": "local", "probe": "python3 -c 'import qrcode, PIL; print(\"ok\")'", "ready_if": "ok", "hint": "pip install --break-system-packages qrcode pillow" },
    { "what": "opencv (local, optional — verifies the result scans)", "where": "local", "probe": "python3 -c 'import cv2; print(\"ok\")'", "ready_if": "ok", "hint": "pip install --break-system-packages opencv-python-headless numpy" },
    { "what": "QR-art models on the DGX", "where": "remote", "probe": "ls {comfy_dir}/workspace/models/controlnet/control_v1p_sd15_qrcode_monster_v2.safetensors 2>/dev/null", "ready_if": "nonempty", "hint": "spark comfy pull-models --set qr-art" }
  ],
  "inputs": {
    "url":   { "type": "string", "required": true, "description": "URL or text the QR encodes" },
    "style": { "type": "enum", "options": ["cyberpunk", "anime"], "default": "cyberpunk", "description": "Art style" },
    "mode":  { "type": "enum", "options": ["stylized", "art"], "default": "stylized", "description": "stylized = reliable scan; art = more scene, lower scan rate" },
    "out":   { "type": "string", "default": "./qr_art.png", "description": "Output PNG path" }
  },
  "steps": [
    {
      "id": "ensure-comfy",
      "title": "Ensure ComfyUI is serving on the Spark",
      "precondition": { "where": "remote", "probe": "curl -sf http://localhost:{comfy_port}/ -o /dev/null && echo up", "ready_if": "up" },
      "remedy": "spark comfy start",
      "next": "ensure-models"
    },
    {
      "id": "ensure-models",
      "title": "Ensure the QR-art models are present",
      "precondition": { "where": "remote", "probe": "ls {comfy_dir}/workspace/models/checkpoints/dreamshaper_8.safetensors 2>/dev/null", "ready_if": "nonempty" },
      "remedy": "spark comfy pull-models --set qr-art",
      "next": "generate"
    },
    {
      "id": "generate",
      "title": "Generate the QR art",
      "command": { "command": "comfy.qr-art", "params": { "url": "{url}", "style": "{style}", "mode": "{mode}", "out": "{out}" } },
      "next": "verify"
    },
    {
      "id": "verify",
      "title": "Confirm it scans (and curate if needed)",
      "next": "DONE"
    }
  ]
}
```

## ensure-comfy

The art is rendered by ComfyUI on the Spark. If it's not serving, `spark comfy start`
pulls and starts the container.

## ensure-models

`qr-art` needs SD1.5 checkpoints (DreamShaper for `cyberpunk`, Counterfeit for `anime`)
plus the **QR-Monster** and **brightness** ControlNets. If missing,
`spark comfy pull-models --set qr-art` fetches all of them (public, ~5 GB).

## generate

Ask the user for the **URL** and (optionally) a **style** (`cyberpunk`/`anime`) and
**mode**. The two angles:

- **`mode=stylized`** (default) — the code is prominent but it **scans reliably** on the
  first try. Best when you just need a working branded QR.
- **`mode=art`** — lower ControlNet strength, so more of the scene shows and the code
  recedes. It looks better but **scans less often** (~1 in 3–4). This is the curate loop.

`spark comfy qr-art` auto-checks scannability if opencv is installed and prints
`scans ✓` or a warning.

## verify

If it printed `scans ✓`, you're done — hand the user the `--out` path. If it did **not**
scan (common in `mode=art`), **re-roll the seed** and regenerate until it scans:

    spark comfy qr-art <url> --style <style> --mode art --seed <N> --out <path>

Generate several seeds, keep the one that both scans and looks best — that curation is
the whole game for the art-dominant look (the published "invisible QR" pieces are picked
from dozens). For a guaranteed scan, fall back to `--mode stylized`.
