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
    {"name": "model",    "type": "string", "default": "flux2_dev_fp8mixed.safetensors", "help": "Diffusion model"},
    {"name": "encoder",  "type": "string", "default": "mistral_3_small_flux2_bf16.safetensors", "help": "Text encoder"},
    {"name": "vae",      "type": "string", "default": "flux2-vae.safetensors", "help": "VAE"}
  ]
}
```

Submits a flat FLUX.2 text-to-image graph to ComfyUI's HTTP API
(`http://<host>:8188`, no SSH), polls until done, and downloads the PNG locally.
The first run loads the models into VRAM and takes a few minutes.

Examples:

    spark comfy generate "a red fox in a snowy forest at dawn"
    spark comfy generate "neon city street" --width 1280 --height 720 --steps 25
