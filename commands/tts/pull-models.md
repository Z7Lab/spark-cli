# tts pull-models

```spec
{
  "name": "tts.pull_models",
  "domain": "tts",
  "subcommand": "pull-models",
  "summary": "Download the Qwen3-TTS model `spark tts` needs",
  "handler": "tts.pull_models",
  "params": []
}
```

Downloads the Qwen3-TTS model the `tts` section of the catalog lists into
`models_dir/<name>/` on the DGX, using the bundled `hf_download.py` (public repo,
resume-safe). It is a transformers repo, so the directory layout is preserved.
Run once before the first `spark tts say`.
