# train pause

```spec
{
  "name": "train.pause",
  "domain": "train",
  "subcommand": "pause",
  "summary": "Stop a run cleanly after the next checkpoint (resumable)",
  "handler": "train.pause",
  "params": [
    {"name": "name", "positional": true, "help": "Run name (default: the only / most recent run)"}
  ]
}
```

Signals the training watchdog to stop **right after the next checkpoint completes**
— never mid-save, so the checkpoint can't be corrupted. The run is left resumable;
pick it back up with `spark train resume <name>`.

Use this to end a session early; `--max-hours` on `start` does the same thing
automatically once the time budget elapses.
