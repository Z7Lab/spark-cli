# image overlay-centered

```spec
{
  "name": "image.overlay_centered",
  "domain": "image",
  "subcommand": "overlay-centered",
  "summary": "Paste assets as a centered group",
  "handler": "image.overlay_centered",
  "params": [
    {"name": "image",    "positional": true, "required": true, "help": "Path to the base image"},
    {"name": "assets",   "type": "string", "required": true, "help": "Comma-separated asset paths (a.png,b.png)"},
    {"name": "y",        "type": "int", "default": 0, "help": "Vertical position of the group"},
    {"name": "scale",    "type": "float", "default": 1.0, "help": "Scale factor applied to the assets"},
    {"name": "gap",      "type": "int", "default": 24, "help": "Gap between assets (pixels)"},
    {"name": "anchor_x", "type": "int", "help": "Center X of the group (default: image center)"},
    {"name": "out",      "type": "string", "required": true, "help": "Output PNG path"}
  ]
}
```

Pastes one or more assets as a horizontally-centered group at the given y.
Pixel-exact, no model involved. Requires Pillow.
