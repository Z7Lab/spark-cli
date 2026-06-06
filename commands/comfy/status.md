# comfy status

```spec
{
  "name": "comfy.status",
  "domain": "comfy",
  "subcommand": "status",
  "summary": "Show ComfyUI state and UI URL",
  "handler": "comfy.status",
  "params": []
}
```

Shows whether the ComfyUI container is running and its UI URL
(`http://<host>:8188`). A down or permission-denied Docker daemon is reported as
"unavailable" with the fix, never as plain "not running".
