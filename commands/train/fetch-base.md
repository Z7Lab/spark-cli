# train fetch-base

```spec
{
  "name": "train.fetch_base",
  "domain": "train",
  "subcommand": "fetch-base",
  "summary": "Pre-seed the HF cache for offline training (flaky-link safe)",
  "handler": "train.fetch_base",
  "params": []
}
```

Pre-downloads the configured base's components — the DiT transformer, the Qwen
text encoder, and the VAE — into the mounted HF cache (`{train_dir}/cache/huggingface`)
**before** a run, so training can load them with `HF_HUB_OFFLINE=1` and never stall
mid-run on a flaky link.

Why this exists: ai-toolkit fetches the base from HuggingFace on first run, but a
multi-GB component over an unreliable connection can hang and block the whole run.
klein's text encoder (`Qwen/Qwen3-8B` for the 9B base, `Qwen/Qwen3-4B` for 4B) is
loaded by a **hardcoded repo id with no local-path option**, so the only reliable
fix is to have it in the cache already. This seeds all three components with the
bundled resume-safe downloader (8 retries, HTTP-Range resume) in the canonical
`refs/` + `snapshots/<sha>/` layout `from_pretrained` / `hf_hub_download` resolve
offline.

Reads `train_base_model` + `train_arch` from config (set them first to pick the
base). For the **gated** klein-9B base, export `HF_TOKEN` so the transformer
can be pulled; the ungated text encoder and VAE seed regardless. Downloads are
resume-safe — re-run to pick up where a dropped connection left off.

Then start the run **offline**:

    spark config set train_base_model black-forest-labs/FLUX.2-klein-base-9B
    spark config set train_arch flux2_klein_9b
    HF_TOKEN=hf_xxx spark train fetch-base
    SPARK_TRAIN_OFFLINE=1 spark train start ~/lora-training/my-art-style --trigger mystylexr

For the ungated default klein-4B base this is optional (its components are small
enough to fetch inline); it's most useful for the gated klein-9B base.
