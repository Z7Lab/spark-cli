# train start

```spec
{
  "name": "train.start",
  "domain": "train",
  "subcommand": "start",
  "summary": "Train a FLUX.2 style LoRA from a corpus on the DGX",
  "handler": "train.start",
  "params": [
    {"name": "corpus",  "positional": true, "required": true, "help": "Local directory of style-consistent images (with optional <image>.txt captions)"},
    {"name": "trigger", "required": true, "help": "Trigger word that invokes the style at inference (put it in the prompt)"},
    {"name": "name",    "type": "string", "help": "Run / LoRA name (default: derived from the corpus folder name)"},
    {"name": "max_hours",  "type": "float", "default": 3.0, "help": "Time budget for this session; auto-stops cleanly after the next checkpoint (0 = run to target)"},
    {"name": "steps",      "type": "int",   "default": 2000, "help": "Total training steps (the completion target across all sessions)"},
    {"name": "save_every", "type": "int",   "default": 250,  "help": "Checkpoint every N steps — a clean stop loses at most one interval"},
    {"name": "rank",       "type": "int",   "default": 16,   "help": "LoRA rank (network dim); higher = more capacity + larger file"},
    {"name": "resolution", "type": "int",   "default": 1024, "help": "Training resolution"},
    {"name": "auto_caption","type": "bool", "help": "Caption images missing a .txt via a served vision model (spark llm serve <vlm>) before training"},
    {"name": "free",        "type": "bool", "help": "Free the box for training first — stop ComfyUI and any llama-servers holding unified memory (opt-in)"}
  ]
}
```

Trains a **FLUX.2 style LoRA** with ai-toolkit in a dedicated container, in a
detached `screen` session on the DGX — the box does only training while this runs
(no concurrent serving). spark drives an **operator-provided, digest-pinned**
ai-toolkit image (`spark config set aitoolkit_image …`, like the ComfyUI image) and
pulls it on first run; it does not build one.

A memory pre-flight warns if ComfyUI or llama-servers are still resident in the
shared unified memory (training then contends for it); pass `--free` to stop them
first and give training the whole box (opt-in — never automatic).

The base model defaults to **FLUX.2-klein-4B** (Apache-2.0, ungated, no token).
**klein-9B** (`train_arch flux2_klein_9b`) is a gated opt-in with more capacity
(`spark config set train_base_model …` + `train_arch …`, plus an `HF_TOKEN`).
ai-toolkit downloads the base on first run; for klein-9B on a flaky link, pre-seed it
with `spark train fetch-base` and start with `SPARK_TRAIN_OFFLINE=1` so it loads offline
(no mid-run stalls). FLUX.2-dev (32B) is not a training base — too large to fine-tune on
a single Spark. See docs/training.md for the base table + license links.

The session is **time-boxed and resumable**: `--max-hours` auto-stops the run
cleanly just after the next checkpoint once the budget elapses, and
`spark train resume` continues from the latest checkpoint until `--steps` is
reached. Checkpoints land every `--save-every` steps, so a stop loses at most one
interval and never corrupts a half-written checkpoint.

The corpus is **your** responsibility — point it at images you are cleared to use.
Caption the *content* of each image in a sidecar `<image>.txt` (the trigger word
carries the style); `--auto-caption` generates those sidecars from a served vision
model for images that lack them.

On completion the LoRA is published into ComfyUI's `models/loras/` as
`<name>.safetensors` — use it with `spark comfy generate "<trigger> …" --lora <name>`.

Examples:

    spark train start ~/lora-training/my-art-style --trigger mystylexr
    spark train start ~/lora-training/my-art-style --trigger mystylexr --steps 3000 --max-hours 2 --rank 32
    spark train start ~/lora-training/my-art-style --trigger mystylexr --auto-caption
