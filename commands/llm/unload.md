# llm unload

```spec
{
  "name": "llm.unload",
  "domain": "llm",
  "subcommand": "unload",
  "summary": "Unload one model, free its memory",
  "handler": "llm.unload",
  "params": [
    {"name": "name",  "positional": true, "help": "Loaded model name to unload"},
    {"name": "port",  "type": "string", "help": "Unload the model on this port (always unambiguous)"},
    {"name": "quant", "type": "string", "help": "Disambiguate a name that has multiple quants loaded"}
  ]
}
```

Unload a single model and free its memory; other models keep running. Address it
by `--port` (always unambiguous) or by name. If a name matches more than one
loaded instance (e.g. two quants), it refuses and lists the candidates so you can
disambiguate with `--port` or `--quant`.

Examples:

    spark llm unload --port 30001
    spark llm unload <model>
    spark llm unload <model> --quant Q5_K_M
