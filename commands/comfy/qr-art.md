# comfy qr-art

```spec
{
  "name": "comfy.qr-art",
  "domain": "comfy",
  "subcommand": "qr-art",
  "summary": "Generate scannable QR-code art (ControlNet)",
  "handler": "comfy.qr_art",
  "params": [
    {"name": "url",   "positional": true, "required": true, "help": "URL/text the QR encodes"},
    {"name": "style", "type": "string", "default": "cyberpunk", "options": ["cyberpunk", "anime"], "help": "Art style (checkpoint + prompt preset)"},
    {"name": "mode",  "type": "string", "default": "stylized", "options": ["stylized", "art"], "help": "stylized = reliable native scan; art = lower QR strength, more scene (curate seeds)"},
    {"name": "seed",  "type": "int", "help": "Seed (default: random) — re-roll for a different take"},
    {"name": "out",   "type": "string", "help": "Local output path (default: ./qr_art.png)"}
  ]
}
```

Turns a URL into a **scannable QR-code-art** image: builds a high-ECC control QR,
then runs SD1.5 + the **QR-Monster** ControlNet (structure) + **brightness** ControlNet
(luminance) on ComfyUI so the code sinks into the art. Verifies the result scans (if
`opencv-python-headless` is installed) and reports it.

Requires `qrcode`+`Pillow` locally and the QR models on the DGX
(`spark comfy pull-models --set qr-art`). `mode=art` looks better but scans less often —
re-roll `--seed` until it scans (see the `qr-art` playbook for the curate loop).

Example:

    spark comfy qr-art https://example.com --style cyberpunk --mode stylized --out qr.png
