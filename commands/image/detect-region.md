# image detect-region

```spec
{
  "name": "image.detect_region",
  "domain": "image",
  "subcommand": "detect-region",
  "summary": "Print the bbox of the main object in an image",
  "handler": "image.detect_region",
  "params": [
    {"name": "image",       "positional": true, "required": true, "help": "Path to the input image"},
    {"name": "search_box",  "type": "string", "help": "Restrict the search to x0,y0,x1,y1"},
    {"name": "threshold",   "type": "int", "default": 26, "help": "Edge/contrast threshold for region detection"},
    {"name": "exclude_top", "type": "int", "default": 0,  "help": "Ignore the top N pixel rows"}
  ]
}
```

Prints the bounding box (`x0,y0,x1,y1`) of the main object, deterministically (no
model). Feed the result to `move-region` / `extract-asset`. Requires Pillow.
