# llm pull-models

```spec
{
  "name": "llm.pull_models",
  "domain": "llm",
  "subcommand": "pull-models",
  "summary": "Download catalog LLM model(s)",
  "handler": "llm.pull_models",
  "params": [
    {"name": "names", "positional": true, "variadic": true, "help": "One or more catalog model names to pull"},
    {"name": "all",   "type": "bool", "help": "Pull every model in the catalog"}
  ]
}
```

Downloads LLM model(s) from the catalog (the `llm` section of
`~/.config/spark.models.json`, else the repo example) into `models_dir/<name>`.
Run with no args to list the catalog with sizes; these models are large, so it
never bulk-pulls without explicit names or `--all`.

Copy `templates/models.example.json` to `~/.config/spark.models.json` to
customize the catalog.
