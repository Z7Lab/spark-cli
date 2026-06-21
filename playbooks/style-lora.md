# style-lora

```spec
{
  "name": "style-lora",
  "description": "Train a FLUX.2 style LoRA from a corpus on the Spark, then use it.",
  "requires": [
    { "what": "Docker usable on the DGX", "where": "remote", "probe": "docker info --format '{{.ServerVersion}}' 2>/dev/null", "ready_if": "nonempty", "hint": "see the README Troubleshooting table (rootless/CDI)" },
    { "what": "ai-toolkit training image on the DGX", "where": "remote", "probe": "docker images -q {aitoolkit_image} 2>/dev/null", "ready_if": "nonempty", "hint": "spark config set aitoolkit_image <image>; build one from templates/train/Dockerfile.reference (GB10/sm_121)" },
    { "what": "A corpus of style-consistent images (local), captioned", "where": "local", "hint": "~20-60 images at ~1024; each <image>.txt captions the CONTENT (the trigger word carries the style)" }
  ],
  "inputs": {
    "corpus":    { "type": "string", "required": true, "description": "Local directory of style-consistent images (with <image>.txt captions)" },
    "trigger":   { "type": "string", "required": true, "description": "Trigger word that invokes the style in prompts" },
    "steps":     { "type": "int",   "default": 2000, "description": "Total training steps (style imprints ~1500-3000)" },
    "rank":      { "type": "int",   "default": 32,   "description": "LoRA rank — higher captures more detail, bigger file" },
    "max_hours": { "type": "float", "default": 0,    "description": "Per-session time budget; 0 = run to target. Auto-stops cleanly after a checkpoint" }
  },
  "steps": [
    {
      "id": "choose-base",
      "title": "Choose the base model",
      "next": "ensure-image"
    },
    {
      "id": "ensure-image",
      "title": "Ensure the ai-toolkit image is on the DGX",
      "precondition": { "where": "remote", "probe": "docker images -q {aitoolkit_image} 2>/dev/null", "ready_if": "nonempty" },
      "remedy": "spark config set aitoolkit_image <image>   # build from templates/train/Dockerfile.reference if needed",
      "next": "prepare-corpus"
    },
    {
      "id": "prepare-corpus",
      "title": "Prepare the corpus (images + content captions)",
      "next": "train"
    },
    {
      "id": "train",
      "title": "Start training (time-boxed, resumable)",
      "command": "spark train start {corpus} --trigger {trigger} --steps {steps} --rank {rank} --max-hours {max_hours}",
      "next": "monitor"
    },
    {
      "id": "monitor",
      "title": "Watch progress; pause/resume as needed",
      "next": "use"
    },
    {
      "id": "use",
      "title": "Use the trained LoRA",
      "next": "DONE"
    }
  ]
}
```

## choose-base

The base model decides both quality **and licensing**, so choose deliberately:

  your own works and **sell** the results. ai-toolkit fetches it automatically. Nothing to set.
  `spark config set train_base_model black-forest-labs/FLUX.2-dev` +
  `spark config set train_arch flux2`, and an `HF_TOKEN` on the box.

If the user intends to sell, keep the default (klein-4B).

## ensure-image

spark **drives** an operator-provided, digest-pinned ai-toolkit image (like the ComfyUI
image); it does not build one. Point spark at a GB10/sm_121-ready image with
`spark config set aitoolkit_image <image>`. To build your own, use
`templates/train/Dockerfile.reference` (it bakes in the GB10 fixes: NGC 25.10 base,
torchcodec/torchaudio handling, libGL). The first `spark train start` pulls the image.

## prepare-corpus

Collect the **corpus** path and a **trigger** word. Recipe for a good style LoRA:

- ~20-60 **style-consistent** images at roughly 1024px (a handful underfits — the style
  won't imprint; that's the difference between a smoke test and a real run).
- Caption each image's **content** in a sidecar `<image>.txt` (e.g. `a lighthouse by the
  sea`) — *not* the style. The trigger word carries the style. Missing captions: pass
  `--auto-caption` to generate them from a served vision model (`spark llm serve <vlm>`).
- The corpus and its rights are the user's responsibility.

## train

Run `spark train start {corpus} --trigger {trigger} --steps {steps} --rank {rank}
--max-hours {max_hours}`. The box does **only** training while a session is live. It
checkpoints every ~250 steps and (with `--max-hours N`) auto-stops cleanly just after a
checkpoint once the budget elapses. ai-toolkit writes sample images under
`{train_dir}/output/<name>/samples/` each checkpoint — the live read on whether the
style is taking hold.

## monitor

- `spark train status <name>` — status / step progress / ETA / live session.
- `spark train status <name> --logs` — follow the ai-toolkit output.
- `spark train pause <name>` — stop cleanly after the next checkpoint (resumable).
- `spark train resume <name> [--max-hours N]` — continue from the latest checkpoint.

Repeat resume across as many time-boxed sessions as it takes; the run is **complete** at
`--steps`. On completion the LoRA publishes into ComfyUI's `models/loras/` as
`<name>.safetensors`.

## use

Generate with the trigger word in the prompt:

    spark comfy generate "<trigger> a lighthouse on a cliff" --lora <name>.safetensors

**Caveat:** `comfy generate` currently serves **FLUX.2-dev (fp8)**, so a **dev**-trained
inference, which isn't wired into `comfy generate` yet — for now, judge a klein run by
its ai-toolkit **sample images** (`{train_dir}/output/<name>/samples/`).
