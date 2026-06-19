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
    {"name": "steps",    "type": "int",    "default": 20,   "help": "Sampling steps"},
    {"name": "guidance", "type": "float",  "default": 3.5,  "help": "FLUX guidance"},
    {"name": "seed",     "type": "int",    "help": "Seed (default: random)"},
    {"name": "out",      "type": "string", "help": "Local output path (default: ./<filename>.png)"},
    {"name": "init",     "type": "string", "help": "Init image to edit (image-to-image) instead of generating from scratch"},
    {"name": "denoise",  "type": "float",  "help": "Image-to-image strength with --init: 0=keep the init, 1=ignore it (default 0.65; inpaint 1.0)"},
    {"name": "inpaint",  "type": "bool",   "help": "With --init, repaint only a region (keeps the rest of the image pixel-exact)"},
    {"name": "region",   "type": "string", "help": "Inpaint box as x,y,w,h fractions 0-1 (default 0.4,0.4,0.55,0.6 = lower-right)"},
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

Examples:

    spark comfy generate "a red fox in a snowy forest at dawn"
    spark comfy generate "neon city street" --width 1280 --height 720 --steps 25
    spark comfy generate "the same landscape under autumn foliage" --init photo.png --denoise 0.5
    spark comfy generate "a hot air balloon in the sky" --init photo.png --inpaint --region 0.3,0.1,0.4,0.4
