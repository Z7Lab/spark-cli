# comfy edit

```spec
{
  "name": "comfy.edit",
  "domain": "comfy",
  "subcommand": "edit",
  "summary": "Edit an image by instruction (Qwen-Image-Edit) — replace or change parts",
  "handler": "comfy.edit",
  "params": [
    {"name": "image",  "positional": true, "required": true, "help": "Image to edit (local path)"},
    {"name": "prompt", "positional": true, "required": true, "help": "Edit instruction, e.g. 'replace the sign with a clock reading 3:00'"},
    {"name": "seed",   "type": "int",    "help": "Seed (default: random)"},
    {"name": "steps",  "type": "int",    "help": "Sampling steps (default 20)"},
    {"name": "cfg",    "type": "float",  "help": "CFG scale (default 4.0)"},
    {"name": "out",    "type": "string", "help": "Local output path (default: ./<filename>.png)"}
  ]
}
```

Instruction-driven image editing with **Qwen-Image-Edit 2509** — describe the change in
plain language ("replace the cat with a dalmatian", "make the sign read OPEN", "remove
the car on the left") and it edits the image while keeping the rest consistent. The
input image is encoded into the model's conditioning (via `TextEncodeQwenImageEditPlus`),
so it edits **semantically** across the whole frame rather than repainting a fixed box.

    spark comfy pull-models --set edit                 # one-time: ~28 GB (DiT + Qwen2.5-VL + VAE)
    spark comfy edit photo.png "replace the blue sign with a clock reading 3:00"
    spark comfy edit room.png "change the sofa to dark green leather" --seed 7

First run loads ~28 GB into memory (a few min); then ~30–60 s per edit. How it differs
from neighbours: `generate --init/--denoise` is plain img2img (no semantic instruction),
`generate --inpaint` repaints a rectangular **region**, and [`comfy refine`](refine.md)
re-renders the **whole** image through a stronger base to fix text/detail. Use `edit`
when you want to change a **specific element** by description.

Model: Qwen-Image-Edit 2509
([license](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI)).
