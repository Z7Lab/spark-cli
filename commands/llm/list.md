# llm list

```spec
{
  "name": "llm.list",
  "domain": "llm",
  "subcommand": "list",
  "summary": "Show loaded models, ports, footprints",
  "handler": "llm.list",
  "params": []
}
```

One line per loaded llama-server: port, name, quant, on-disk footprint, and pid,
plus the resident total and free memory. The live processes are the source of
truth, so the list can never drift from reality.
