# comfy animate

```spec
{
  "name": "comfy.animate",
  "domain": "comfy",
  "subcommand": "animate",
  "summary": "Animate a still into a video (LTX-2.3)",
  "handler": "comfy.animate",
  "params": [
    {"name": "image",  "positional": true, "required": true, "help": "Path to the still image to animate"},
    {"name": "prompt", "positional": true, "required": true, "help": "Motion prompt (quote it)"},
    {"name": "seed",   "type": "int",    "help": "Seed (default: random)"},
    {"name": "out",    "type": "string", "help": "Local output path (default: ./<filename>.mp4)"}
  ]
}
```

Uploads the still to ComfyUI, injects it + the motion prompt into the bundled
LTX-2.3 image-to-video graph (`templates/ltx2_i2v_api.json`), runs it, and
downloads the MP4. Requires the LTX models on the DGX (see
`spark comfy pull-models --set animate`). The first run loads ~44G into VRAM.

Example:

    spark comfy animate fox.png "the fox leaps and runs through the snow"
