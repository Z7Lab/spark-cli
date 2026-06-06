# llm open

```spec
{
  "name": "llm.open",
  "domain": "llm",
  "subcommand": "open",
  "summary": "Open the built-in chat UI in a browser",
  "handler": "llm.open",
  "params": [
    {"name": "port", "type": "string", "help": "Which model's UI to open (default: the only one loaded)"}
  ]
}
```

Opens a model's built-in llama-server chat UI (`http://<host>:<port>`) in your
browser. With several models loaded, pass `--port` to choose one.
