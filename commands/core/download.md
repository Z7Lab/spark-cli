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

**Authentication — two paths:**

- **Public models (default):** no token, nothing to set up. The bundled downloader
  fetches public repos directly over HTTPS — this is the normal path for everything
  in spark's catalogs.
- **Gated or private models:** put a HuggingFace token **on the DGX** (where the
  downloader runs) — either `export HF_TOKEN=…` in its environment or `hf auth login`
  (writes `~/.cache/huggingface/token`) — and request access to the repo on
  HuggingFace first. The downloader auto-detects the token; there is **no `--token`
  flag** by design, so secrets never land in argv or the screen/download logs.

For several downloads in one background run, use `spark queue`.
