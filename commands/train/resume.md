# train resume

```spec
{
  "name": "train.resume",
  "domain": "train",
  "subcommand": "resume",
  "summary": "Resume a paused run from its latest checkpoint",
  "handler": "train.resume",
  "params": [
    {"name": "name",      "positional": true, "help": "Run name (default: the only / most recent run)"},
    {"name": "max_hours", "type": "float", "help": "Time budget for this session (default: reuse the run's previous budget)"}
  ]
}
```

Relaunches the run in the dedicated `screen` session; ai-toolkit continues from the
latest checkpoint in the run's output folder until the original `--steps` target is
reached. Pass `--max-hours` to set a fresh time budget for this session, or omit it
to reuse the run's previous budget.

Resume as many time-boxed sessions as it takes — each one trains another
`--max-hours` chunk and stops cleanly after a checkpoint, until the run is complete.
