# image move-region

```spec
{
  "name": "image.move_region",
  "domain": "image",
  "subcommand": "move-region",
  "summary": "Relocate a region, background-fill the source",
  "handler": "image.move_region",
  "params": [
    {"name": "image",     "positional": true, "required": true, "help": "Path to the input image"},
    {"name": "bbox",      "type": "string", "required": true, "help": "Region to move x0,y0,x1,y1"},
    {"name": "dy",        "type": "int", "default": 0, "help": "Vertical shift (pixels; negative = up)"},
    {"name": "dx",        "type": "int", "default": 0, "help": "Horizontal shift (pixels; negative = left)"},
    {"name": "bg",        "type": "string", "default": "auto", "help": "Fill for the vacated source: auto or r,g,b"},
    {"name": "clear_pad", "type": "int", "default": 0, "help": "Extra pixels around the source to also clear"},
    {"name": "out",       "type": "string", "required": true, "help": "Output PNG path"}
  ]
}
```

Relocates a region by (dx, dy) and background-fills the area it left behind.
Pixel-exact, no model involved. Requires Pillow.
