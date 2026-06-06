# image extract-asset

```spec
{
  "name": "image.extract_asset",
  "domain": "image",
  "subcommand": "extract-asset",
  "summary": "Crop a sub-image (e.g. a badge) out of an image",
  "handler": "image.extract_asset",
  "params": [
    {"name": "image", "positional": true, "required": true, "help": "Path to the input image"},
    {"name": "bbox",  "type": "string", "required": true, "help": "Crop region x0,y0,x1,y1"},
    {"name": "out",   "type": "string", "required": true, "help": "Output PNG path"}
  ]
}
```

Crops the given bounding box out of the image and saves it. Pixel-exact, no model
involved. Requires Pillow.
