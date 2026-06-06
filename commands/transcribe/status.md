# transcribe status

```spec
{
  "name": "transcribe.status",
  "domain": "transcribe",
  "subcommand": "status",
  "summary": "Show whisper-server state and endpoint",
  "handler": "transcribe.status",
  "params": [
    {"name": "port", "type": "int", "default": 8081, "help": "Port to probe for health"}
  ]
}
```

Shows whether whisper-server is running, the model it loaded, and the endpoint
URL (`http://<host>:<port>/v1/audio/transcriptions`).
