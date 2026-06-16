# llm rm

```spec
{
  "name": "llm.rm",
  "domain": "llm",
  "subcommand": "rm",
  "summary": "Delete a downloaded model's GGUF files from disk (frees space)",
  "handler": "llm.rm",
  "params": [
    {"name": "model", "positional": true, "required": true, "help": "Model name to delete (see spark models)"},
    {"name": "quant", "type": "string", "help": "Delete only this quant (e.g. UD-Q2_K_XL); default: every quant of the model"},
    {"name": "yes",   "type": "bool",   "help": "Skip the typed-name confirmation (for scripts) — use with care"}
  ]
}
```

Permanently deletes a downloaded model's GGUF files from the models directory on
the DGX, freeing **disk** space. This is different from `spark llm unload`, which
only frees memory — `rm` removes the files, and re-downloading means a full
`spark llm pull-models` again.

Because it is destructive and irreversible, it **refuses to delete a model that
is currently loaded** (unload it first) and requires you to **type the model
name to confirm** — `--yes` skips that prompt for scripted use. Pass `--quant` to
remove just one quant and keep the others (e.g. drop a redundant `UD-Q2_K_XL`
while keeping the `UD-Q3_K_XL` you serve).

    spark llm rm OldModel                      # delete all quants (prompts for confirmation)
    spark llm rm MiniMax-M2.5 --quant UD-Q2_K_XL   # drop just one quant
