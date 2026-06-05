# llm-serve

```spec
{
  "name": "llm-serve",
  "description": "Load an LLM on the Spark as its own llama-server (fit-checked).",
  "inputs": {
    "model": {
      "type": "string",
      "required": true,
      "description": "Model name to serve — matches a downloaded model dir (see spark models)"
    }
  },
  "steps": [
    {
      "id": "ensure-model",
      "title": "Ensure the model is downloaded on the Spark",
      "precondition": {
        "where": "remote",
        "probe": "find {models_dir} -iname '*{model}*.gguf' 2>/dev/null | head -1",
        "ready_if": "nonempty"
      },
      "remedy": "spark llm pull-models   (or: spark download <org>/<repo>-GGUF {model} '<glob>')",
      "next": "serve"
    },
    {
      "id": "serve",
      "title": "Load the model (refuses if it won't fit)",
      "command": "spark llm serve {model}",
      "next": "verify"
    },
    {
      "id": "verify",
      "title": "Confirm it's resident",
      "command": "spark llm list",
      "next": "DONE"
    }
  ]
}
```

## ensure-model

Run `spark models` to see what's downloaded. If the requested model isn't there, the
remedy pulls it — `spark llm pull-models` lists the catalog with sizes, or use
`spark download` for an arbitrary HuggingFace GGUF.

## serve

`spark llm serve <model>` runs the fit-check first (weights + KV cache + reserve vs
free memory) and **refuses rather than evicting** another model if it won't fit —
listing what to unload. If it refuses, ask the user which resident model to free
(`spark llm unload --port N`) or lower `--ctx`/`--parallel`, then re-run.

## verify

`spark llm list` shows the loaded model, its port, and footprint. Hand the user the
API/chat URL (`http://<host>:<port>`). Done.
