# llm serve

```spec
{
  "name": "llm.serve",
  "domain": "llm",
  "subcommand": "serve",
  "summary": "Load a model on its own llama-server (fit-checked)",
  "handler": "llm.serve",
  "params": [
    {"name": "model",    "positional": true, "required": true, "help": "Model name — matches a downloaded model dir (see spark models)"},
    {"name": "quant",    "type": "string", "help": "Quant to load if several are available (e.g. UD-Q3_K_XL)"},
    {"name": "port",     "type": "int",    "help": "Bind a specific port (default: next free from 30000)"},
    {"name": "ctx",      "type": "int", "default": 8192, "help": "Context window size"},
    {"name": "parallel", "type": "int", "default": 4,    "help": "Parallel request slots"},
    {"name": "free_comfy", "type": "bool", "help": "If it won't fit, stop ComfyUI first to reclaim its memory"}
  ]
}
```

Each model runs as a separate llama-server on its own port, so several can be
loaded at once. Before loading, serve estimates the footprint (weights + KV
cache, which scales with `--ctx`/`--parallel`) and requires `mem_reserve_gb` free
on top; if it won't fit it **refuses rather than evicting** another model, and
lists the resident models so you can free space with `spark llm unload`.

The unified memory is shared with ComfyUI, so a loaded comfy (FLUX/LTX weights
resident) can be why an LLM won't fit. serve surfaces that — *"ComfyUI is holding
~X G"* — and you can either `spark comfy stop` yourself or re-run with
`--free-comfy` to have serve stop it first. It is never stopped automatically.

Picks the next free port automatically unless `--port` is given. Always passes
`--jinja --tools all` (chat-template tool calling + built-in UI tools). If
multiple quants match and `--quant` is omitted, it prompts.

Examples:

    spark llm serve <model>                 # prompts if multiple quants
    spark llm serve <model> --quant Q5_K_M  # load a specific quant directly
