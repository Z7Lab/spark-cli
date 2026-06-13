# engine build

```spec
{
  "name": "engine.build",
  "domain": "engine",
  "subcommand": "build",
  "summary": "Rebuild an engine from its pinned commit (or --ref/--latest)",
  "handler": "engine.build",
  "params": [
    {"name": "engine", "positional": true, "required": true, "help": "Engine to build (e.g. llama)"},
    {"name": "ref",    "type": "string", "help": "Build a specific commit/tag instead of the pin (moves the pin)"},
    {"name": "latest", "type": "bool",   "help": "Build the latest upstream commit (moves the pin)"},
    {"name": "yes",    "type": "bool",   "help": "Skip the confirmation prompt"}
  ]
}
```

Rebuilds an engine **on the DGX** from its pinned commit using the recorded cmake
flags (`templates/engines.json`), then validates the new binary actually launches
(the `LD_LIBRARY_PATH`/shared-lib case). Reproducible: same pin + flags → same build.

By default it builds the **pin**. `--ref <commit>` or `--latest` deliberately move
off the pin; after a clean, validated build the new commit is re-recorded as the pin
(so a deliberate update is explicit and re-pinned, never silent drift). Recompiling
is a CUDA/CMake build — several minutes; pass `--yes` to skip the prompt.

    spark engine build llama                 # rebuild to the pin
    spark engine build llama --latest --yes  # update to upstream HEAD, re-pin
