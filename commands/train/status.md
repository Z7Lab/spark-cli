# train status

```spec
{
  "name": "train.status",
  "domain": "train",
  "subcommand": "status",
  "summary": "Show a run's progress, ETA, and the trained LoRA",
  "handler": "train.status",
  "params": [
    {"name": "name", "positional": true, "help": "Run name (default: the only / most recent run)"},
    {"name": "logs", "type": "bool", "help": "Tail the run's training log live (Ctrl+C to stop) instead of a snapshot"}
  ]
}
```

Reads the run-state the in-container watchdog writes and shows the status
(`training` / `stopping` / `paused` / `complete` / `error`), step progress, elapsed
time, a rough ETA to the target, and whether a dedicated session is live.

It also prints the **next action** for the run's state: `pause:` (the `spark train
pause` command) while a run is actively training, `resume:` once it's paused/stopped,
and the `spark comfy generate --lora` command once it's `complete`.

The step count is the latest **saved checkpoint** (every `save_every` steps), so it can
lag the live step by up to one interval — `--logs` shows the live step. A pending pause
shows as `◐ stopping after next checkpoint`.

When a run is `complete`, it publishes the latest checkpoint into ComfyUI's
`models/loras/` as `<name>.safetensors`. Pass `--logs` to follow the live training output.

With no run name it picks the only run, or lists the runs if several exist.

For a quick "is a run training right now, and how hot is the box," `spark temp` shows the
live run + uptime next to GPU temp/util; `spark train status` is the detailed progress view.
