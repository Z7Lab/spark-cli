# logs-dl

```spec
{
  "name": "logs-dl",
  "domain": "logs-dl",
  "summary": "Tail the download queue log",
  "handler": "core.logs_dl",
  "params": []
}
```

Follows the background download queue's log on the DGX (`download_log`).
Ctrl+C to stop.
