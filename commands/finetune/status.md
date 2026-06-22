# finetune status

```spec
{
  "name": "finetune.status",
  "domain": "finetune",
  "subcommand": "status",
  "summary": "Show a fine-tune run's progress, ETA, and the published GGUF",
  "handler": "finetune.status",
  "params": [
    {"name": "name", "positional": true, "help": "Run name (default: the only / most recent run)"},
    {"name": "logs", "type": "bool", "help": "Tail the run's training log live (Ctrl+C to stop) instead of a snapshot"}
  ]
}
```

Reads the run-state the in-container watchdog writes and shows the status
(`training` / `stopping` / `paused` / `complete` / `error`), step progress, elapsed
time, a rough ETA to the target, and whether a dedicated session is live.

It also prints the **next action** for the run's state: `pause:` (the `spark finetune pause` command) while a run is actively training, `resume:` once it's
paused/stopped, and the `spark llm serve` command once it's `complete`.

The step count is the latest **saved checkpoint** (every `save_every` steps), so it
can lag the live step by up to one interval — `--logs` shows the live step. A run
still at step 0 with a live session is downloading/loading the base model (shown as
`preparing`, with the HF cache size as a progress proxy).

When a run is `complete`, it publishes the merged **GGUF** into `models_dir` as
`<name>/<name>.<quant>.gguf` (serve it with `spark llm serve <name>`) and reports
where the retained LoRA adapter lives. If the GGUF export failed (e.g. the image
lacks a llama.cpp converter) it says so — the adapter is still saved and the run can
be re-exported.

With no run name it picks the only run, or lists the runs if several exist.
