# llm logs

```spec
{
  "name": "llm.logs",
  "domain": "llm",
  "subcommand": "logs",
  "summary": "Tail a server log",
  "handler": "llm.logs",
  "params": [
    {"name": "port",  "type": "string", "help": "Which server's log to tail (default: the only one loaded)"},
    {"name": "lines", "type": "int", "default": 20, "help": "Lines to show before following"}
  ]
}
```

Tails a llama-server log and follows it (Ctrl+C to exit). With a single model
loaded the port is selected automatically; with several loaded, pass `--port`.
