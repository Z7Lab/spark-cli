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
llama-server (port, health, API/UI URLs), ComfyUI and whisper-server state, and
free RAM / disk. A down or permission-denied Docker daemon is reported as such
rather than as "not running".
