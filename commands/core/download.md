# download

```spec
{
  "name": "download",
  "domain": "download",
  "summary": "Download a single model from HuggingFace",
  "handler": "core.download",
  "params": [
    {"name": "repo",       "positional": true, "required": true, "help": "HuggingFace repo (e.g. <org>/<model>-GGUF)"},
    {"name": "local_name", "positional": true, "required": true, "help": "Directory name under models_dir"},
    {"name": "pattern",    "positional": true, "required": true, "help": "Glob matching the files to fetch (e.g. \"*Q4_K_XL*\")"}
  ]
}
```

Runs the bundled `hf_download.py` on the DGX to pull one model into
`models_dir/<local_name>`. Resume-safe.

Example:

    spark download <org>/<model>-GGUF <local-name> "*Q4_K_XL*"

For several downloads in one background run, use `spark queue`.
