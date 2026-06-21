# comfy refine

```spec
{
  "name": "comfy.refine",
  "domain": "comfy",
  "subcommand": "refine",
  "summary": "Refine an image with a stronger model (fix text, sharpen detail)",
  "handler": "comfy.refine",
  "params": [
    {"name": "image",  "positional": true, "required": true, "help": "Image to refine (local path)"},
    {"name": "prompt", "positional": true, "help": "What the image shows — pass the original prompt (with any sign/label text in quotes) for the best text fix"},
    {"name": "denoise", "type": "float", "help": "How much the refiner may change: 0=keep the image, 1=ignore it (default 0.5 — fixes text while keeping the look)"},
    {"name": "base",   "type": "string", "options": ["flux2-dev", "flux2-klein-4b"], "default": "flux2-dev", "help": "Refiner model (default flux2-dev — the stronger text renderer)"},
    {"name": "steps",  "type": "int",    "help": "Sampling steps (default 20)"},
    {"name": "guidance", "type": "float", "help": "FLUX guidance (default 3.5)"},
    {"name": "seed",   "type": "int",    "help": "Seed (default: random)"},
    {"name": "width",  "type": "int",    "default": 1024, "help": "Sampling width hint"},
    {"name": "height", "type": "int",    "default": 1024, "help": "Sampling height hint"},
    {"name": "out",    "type": "string", "help": "Local output path (default: ./<filename>.png)"}
  ]
}
```

Run an image back through a **stronger model** to fix what a smaller base got wrong —
most often **garbled text** and soft fine detail — while keeping the composition and
most of the original style. It is full-image **img2img** at a moderate `--denoise`: the
refiner is free to repair the image but anchored to the source.

The default base is **FLUX.2-dev**, the stronger text renderer, at **denoise 0.5** — the
level where text becomes legible without drifting far from the source (lower keeps more
of the original but fixes less; higher fixes more but restyles toward dev). For the best
text fix, pass the **original prompt** and put the intended sign/label text in quotes.

Typical flow: generate your style on the **klein** base, then refine the keepers:

    spark comfy generate "mystylexr a bookshop, a sign reading \"OPEN BOOKS\"" --base flux2-klein-4b --lora my-art-style.safetensors --out shop.png
    spark comfy refine shop.png "a bookshop, a sign reading \"OPEN BOOKS\"" --denoise 0.5

The default refiner is **FLUX.2-dev**
([license](https://huggingface.co/black-forest-labs/FLUX.2-dev)); `--base
flux2-klein-4b` ([license](https://huggingface.co/black-forest-labs/FLUX.2-klein-base-4B))
keeps the same model as klein generation.

For **targeted** edits (replace a specific object/sign, not the whole image), a
purpose-built image-editing model is tracked separately — `refine` is whole-image only.
