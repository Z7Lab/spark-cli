# status

```spec
{
  "name": "status",
  "domain": "status",
  "summary": "Show all services (LLM, ComfyUI, Whisper, RAM)",
  "handler": "core.status",
  "params": []
}
```

One-glance view of the DGX: config file, SSH reachability, every loaded
llama-server (port, health, API/UI URLs), ComfyUI and whisper-server state,
free RAM / disk, GPU temperature, and whether an `HF_TOKEN` is present (on the
DGX and in spark's env — presence only, never the value; gated model/base
downloads need one). A down or permission-denied Docker daemon is reported as
such rather than as "not running".
