# transcribe pull-models

```spec
{
  "name": "transcribe.pull_models",
  "domain": "transcribe",
  "subcommand": "pull-models",
  "summary": "Download whisper ggml model(s)",
  "handler": "transcribe.pull_models",
  "params": [
    {"name": "model", "type": "string", "default": "large-v3", "help": "Which model to pull"},
    {"name": "all",   "type": "bool", "help": "Pull every model in the catalog"}
  ]
}
```

Downloads whisper ggml model(s) from the catalog into the DGX's whisper models
dir using the bundled `hf_download.py` (public, resume-safe). Replaces the
dependency on whisper.cpp's own `download-ggml-model.sh`.
