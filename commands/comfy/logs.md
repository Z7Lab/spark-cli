# comfy logs

```spec
{
  "name": "comfy.logs",
  "domain": "comfy",
  "subcommand": "logs",
  "summary": "Tail the ComfyUI container logs",
  "handler": "comfy.logs",
  "params": [
    {"name": "lines", "type": "int", "default": 50, "help": "Lines to show before following"}
  ]
}
```

Follows the ComfyUI container's `docker compose logs` (Ctrl+C to exit).
