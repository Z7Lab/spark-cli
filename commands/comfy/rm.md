# comfy rm

```spec
{
  "name": "comfy.rm",
  "domain": "comfy",
  "subcommand": "rm",
  "summary": "Delete ComfyUI model file(s) from disk — a named file or all orphans",
  "handler": "comfy.rm",
  "params": [
    {"name": "file",    "positional": true, "help": "Model file to delete (basename or relative path; substring matches)"},
    {"name": "orphans", "type": "bool", "help": "Delete every orphan (unreferenced) model instead of a named file"},
    {"name": "yes",     "type": "bool", "help": "Skip the typed confirmation (for scripts) — use with care"}
  ]
}
```

Permanently deletes ComfyUI model file(s) from the DGX to free **disk** space — either a
single named file, or **every orphan** with `--orphans` (the files
[`spark comfy models`](models.md) flags as unreferenced). Shows the selection + total
size and asks for a typed confirmation unless `--yes`. `--orphans` never touches
`loras/` (user-trained weights). Re-fetch catalog models with `spark comfy pull-models`.

    spark comfy models                 # see what's there + what's orphaned
    spark comfy rm --orphans           # reclaim all unreferenced models
    spark comfy rm ltx-2.3-22b-dev.safetensors
