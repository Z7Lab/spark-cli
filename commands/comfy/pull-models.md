# comfy pull-models

```spec
{
  "name": "comfy.pull_models",
  "domain": "comfy",
  "subcommand": "pull-models",
  "summary": "Download the models generate/animate need",
  "handler": "comfy.pull_models",
  "params": [
    {"name": "set", "type": "string", "options": ["generate", "generate-klein", "animate", "qr-art", "all"], "default": "all",
     "help": "Which model set to pull: generate (FLUX.2-dev), generate-klein (FLUX.2-klein-4B + Qwen3, for --base flux2-klein-4b), animate (LTX-2.3), qr-art (SD1.5+ControlNets), or all"}
  ]
}
```

Reads the `comfy` section of the model catalog and lands each entry (via
`hf_download.py --flat`) at `<comfy_dir>/workspace/models/<subdir>/<file>` —
ComfyUI's flat layout. Resume-safe and public, so a fresh checkout reproduces the
model set with the repo's own tooling.
