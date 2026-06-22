# finetune pause

```spec
{
  "name": "finetune.pause",
  "domain": "finetune",
  "subcommand": "pause",
  "summary": "Stop a fine-tune cleanly after the next checkpoint (resumable)",
  "handler": "finetune.pause",
  "params": [
    {"name": "name", "positional": true, "help": "Run name (default: the only / most recent run)"}
  ]
}
```

Signals the fine-tune watchdog to stop **right after the next checkpoint completes**
— never mid-save, so the checkpoint can't be corrupted. The run is left resumable;
pick it back up with `spark finetune resume <name>`.

Use this to end a session early; `--max-hours` on `spark finetune start` does the same
thing automatically once the time budget elapses.
