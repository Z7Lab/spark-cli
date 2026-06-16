# llm bench

```spec
{
  "name": "llm.bench",
  "domain": "llm",
  "subcommand": "bench",
  "summary": "Measure a loaded model's generation speed (tokens/sec)",
  "handler": "llm.bench",
  "params": [
    {"name": "model",      "positional": true, "help": "Loaded model to benchmark (default: the only loaded one)"},
    {"name": "port",       "type": "int",    "help": "Target a specific server port instead of by name"},
    {"name": "prompt",     "type": "string", "default": "make a python script to sort numbers", "help": "Prompt to generate from"},
    {"name": "max_tokens", "type": "int",    "default": 2048, "help": "Max tokens to generate"},
    {"name": "runs",       "type": "int",    "default": 1,    "help": "Number of runs to average"},
    {"name": "save",       "type": "bool",   "help": "Save the speed result to a per-model report under reports/"},
    {"name": "out",        "type": "string", "help": "Write the report to a specific path instead of the default reports/ location"}
  ]
}
```

Sends a prompt to a **loaded** model's OpenAI-compatible endpoint
(`http://<host>:<port>/v1/chat/completions`, no SSH) and reports generation
throughput. Uses llama-server's own `timings.predicted_per_second` (generation
only, excludes prefill/network) when present, falling back to wall-clock
tokens÷seconds. Serve a model first with `spark llm serve <model>`.

The default prompt matches the saved inference-speed baselines, so numbers are
comparable across models.

    spark llm serve Qwen3.6-35B-A3B
    spark llm bench Qwen3.6-35B-A3B
    spark llm bench --port 30000 --runs 3

Pass `--save` to record the result in a per-model report under `reports/`
(captures provenance — source repo, quant, footprint, engine build — alongside
the speed). `spark llm probe --save` updates the same file's capability section,
and `spark llm reports` renders them all to a Markdown table.

    spark llm bench Qwen3.6-35B-A3B --runs 2 --save
    spark llm bench Qwen3.6-35B-A3B --out reports/reference/qwen.json
