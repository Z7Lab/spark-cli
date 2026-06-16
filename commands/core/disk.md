# disk

```spec
{
  "name": "disk",
  "domain": "disk",
  "summary": "DGX disk usage and the biggest spark-managed consumers",
  "handler": "core.disk",
  "params": [
    {"name": "prune", "type": "bool", "help": "Reclaim unused Docker images + build cache (keeps volumes), then show free space"}
  ]
}
```

Reports free space on the DGX filesystem that holds the models directory, then
breaks down the largest spark-managed paths — LLM GGUFs, the ComfyUI install and
its models, Whisper models, the llama.cpp checkout, the TTS venv, and the
HuggingFace download cache — plus Docker's reclaimable usage. Use it when a
build or download fails with *"No space left on device"* to see what's safe to
free before deleting any models.

`--prune` reclaims Docker's unused images and build cache (it never removes
volumes or the running ComfyUI container). To delete a model you no longer
serve, use `spark llm rm <model>`.

    spark disk
    spark disk --prune
