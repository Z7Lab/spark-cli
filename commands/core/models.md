# models

```spec
{
  "name": "models",
  "domain": "models",
  "summary": "List downloaded models with quant and size",
  "handler": "core.models",
  "params": []
}
```

Lists every GGUF on the DGX grouped by model name (collapsing quant subdirs and
multi-part files), with the available quant(s), on-disk size, and the
`spark llm serve <name>` line to load it.
