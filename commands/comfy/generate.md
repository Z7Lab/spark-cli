# comfy generate

```spec
{
  "name": "comfy.generate",
  "domain": "comfy",
  "subcommand": "generate",
  "summary": "Generate a FLUX.2 image, save the PNG",
  "handler": "comfy.generate",
  "params": [
    {"name": "prompt",   "positional": true, "required": true, "help": "Text-to-image prompt (quote it)"},
    {"name": "width",    "type": "int",    "default": 1024, "help": "Image width"},
    {"name": "height",   "type": "int",    "default": 1024, "help": "Image height"},
    {"name": "steps",    "type": "int",    "help": "Sampling steps (default 20; 8 with --turbo)"},
    {"name": "guidance", "type": "float",  "help": "FLUX guidance (default 3.5; 1.5 with --turbo)"},
    {"name": "seed",     "type": "int",    "help": "Seed (default: random)"},
    {"name": "out",      "type": "string", "help": "Local output path (default: ./<filename>.png)"},
    {"name": "init",     "type": "string", "help": "Init image to edit (image-to-image) instead of generating from scratch"},
    {"name": "denoise",  "type": "float",  "help": "Image-to-image strength with --init: 0=keep the init, 1=ignore it (default 0.65; inpaint 1.0)"},
    {"name": "inpaint",  "type": "bool",   "help": "With --init, repaint only a region (keeps the rest of the image pixel-exact)"},
    {"name": "region",   "type": "string", "help": "Inpaint box as x,y,w,h fractions 0-1 (default 0.4,0.4,0.55,0.6 = lower-right)"},
    {"name": "lora",          "type": "string", "help": "Style/subject LoRA in models/loras (e.g. from `spark train`); put its trigger word in the prompt"},
    {"name": "lora_strength", "type": "float",  "default": 1.0, "help": "LoRA weight: 1.0 full effect, lower for a subtler nudge"},
    {"name": "turbo",         "type": "bool",   "help": "Few-step distilled FLUX.2 LoRA for near-real-time gen (lowers steps→8, guidance→1.5; stacks with --lora). Needs: comfy pull-models --set generate"},
    {"name": "model",    "type": "string", "default": "flux2_dev_fp8mixed.safetensors", "help": "Diffusion model"},
    {"name": "encoder",  "type": "string", "default": "mistral_3_small_flux2_bf16.safetensors", "help": "Text encoder"},
    {"name": "vae",      "type": "string", "default": "flux2-vae.safetensors", "help": "VAE"}
  ]
}
```

Submits a flat FLUX.2 graph to ComfyUI's HTTP API (`http://<host>:8188`, no SSH),
polls until done, and downloads the PNG locally. The first run loads the models
into VRAM and takes a few minutes.

With `--init`, it runs **image-to-image** — the init still is uploaded, encoded,
and sampled from at `--denoise` (lower keeps more of the original; 0.65 default
follows the prompt while preserving the source composition). Use it to edit an
existing image (e.g. add or change something) while keeping its style and layout.

With `--lora <name>`, a trained FLUX.2 LoRA from `models/loras/` (e.g. produced by
`spark train`) is spliced into the graph as a `LoraLoaderModelOnly` so generation
follows that style/subject. Put the LoRA's **trigger word** in the prompt to invoke
it; `--lora-strength` (default 1.0) scales the effect. The name is validated against
ComfyUI's own LoRA list, so a typo fails fast with the available names.

`--turbo` applies a **few-step distilled** FLUX.2 LoRA (step distillation — the
near-real-time speed lever) and drops the defaults to 8 steps / 1.5 guidance, so a
gen takes seconds instead of ~a minute. It **stacks with `--lora`** (turbo for speed
+ your style), and explicit `--steps`/`--guidance` still win. Fetch the turbo LoRA
once with `spark comfy pull-models --set generate`.

Examples:

    spark comfy generate "a red fox in a snowy forest at dawn"
    spark comfy generate "neon city street" --width 1280 --height 720 --steps 25
    spark comfy generate "the same landscape under autumn foliage" --init photo.png --denoise 0.5
    spark comfy generate "a hot air balloon in the sky" --init photo.png --inpaint --region 0.3,0.1,0.4,0.4
    spark comfy generate "mystylexr a lighthouse on a cliff" --lora my-art-style.safetensors --lora-strength 0.9
    spark comfy generate "a red fox in a snowy forest" --turbo
    spark comfy generate "mystylexr a lighthouse" --turbo --lora my-art-style.safetensors
