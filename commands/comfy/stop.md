# comfy stop

```spec
{
  "name": "comfy.stop",
  "domain": "comfy",
  "subcommand": "stop",
  "summary": "Stop the running ComfyUI container",
  "handler": "comfy.stop",
  "params": []
}
```

Stops the ComfyUI container via `docker compose down`. Surfaces the Docker remedy
if the daemon is unusable.
