# comfy logs

```spec
{
  "name": "comfy.logs",
  "domain": "comfy",
  "subcommand": "logs",
  "summary": "Tail the ComfyUI container logs",
  "handler": "comfy.logs",
  "params": [
    {"name": "lines",  "type": "int",  "default": 50, "help": "Number of recent log lines to show"},
    {"name": "follow", "type": "bool", "help": "Stream new logs live (Ctrl+C to stop) instead of printing a snapshot and exiting"}
  ]
}
```

Shows the last `--lines` of the ComfyUI container's `docker compose logs` and
exits — handy for diagnosing a container that's up but not serving. Pass
`--follow` to stream live (Ctrl+C to exit).
