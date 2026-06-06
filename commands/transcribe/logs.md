# transcribe logs

```spec
{
  "name": "transcribe.logs",
  "domain": "transcribe",
  "subcommand": "logs",
  "summary": "Tail the whisper-server log",
  "handler": "transcribe.logs",
  "params": [
    {"name": "lines", "type": "int", "default": 20, "help": "Lines to show before following"}
  ]
}
```

Follows the whisper-server log on the DGX (Ctrl+C to exit).
