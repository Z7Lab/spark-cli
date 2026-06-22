# finetune

```spec
{
  "name": "finetune",
  "description": "Fine-tune a small coder LLM (Unsloth QLoRA) from a messages JSONL on the Spark, then serve it.",
  "requires": [
    { "what": "Docker usable on the DGX", "where": "remote", "probe": "docker info --format '{{.ServerVersion}}' 2>/dev/null", "ready_if": "nonempty", "hint": "see the README Troubleshooting table (rootless/CDI)" },
    { "what": "Unsloth fine-tune image on the DGX", "where": "remote", "probe": "docker images -q {unsloth_image} 2>/dev/null", "ready_if": "nonempty", "hint": "spark config set unsloth_image <image>; build one from templates/finetune/Dockerfile.reference (GB10/sm_121), or use a published GB10 image" },
    { "what": "A messages JSONL dataset (local)", "where": "local", "hint": "one {\"messages\":[{\"role\",\"content\"}, …]} object per line; each example needs an assistant turn. Sourcing (KB→pairs, RAG export) is YOUR job, upstream of spark" }
  ],
  "inputs": {
    "dataset":   { "type": "string", "required": true, "description": "Local messages JSONL (chat format)" },
    "base":      { "type": "string", "default": "", "description": "Base model (HF repo or local path); empty = the configured finetune_base_model (an Unsloth bnb-4bit coder)" },
    "epochs":    { "type": "int",   "default": 3,    "description": "Training epochs (style/behavior imprints in a few)" },
    "rank":      { "type": "int",   "default": 64,   "description": "LoRA rank — alpha = 2×rank; higher = more capacity, bigger adapter" },
    "max_hours": { "type": "float", "default": 0,    "description": "Per-session time budget; 0 = run to target epochs. Auto-stops cleanly after a checkpoint" }
  },
  "steps": [
    {
      "id": "scope",
      "title": "Decide: do you actually need fine-tuning (vs RAG)?",
      "next": "ensure-image"
    },
    {
      "id": "ensure-image",
      "title": "Ensure the Unsloth image is on the DGX",
      "precondition": { "where": "remote", "probe": "docker images -q {unsloth_image} 2>/dev/null", "ready_if": "nonempty" },
      "remedy": "spark config set unsloth_image <image>   # build from templates/finetune/Dockerfile.reference if needed",
      "next": "prepare-dataset"
    },
    {
      "id": "prepare-dataset",
      "title": "Prepare the dataset (messages JSONL)",
      "next": "finetune"
    },
    {
      "id": "finetune",
      "title": "Start fine-tuning (time-boxed, resumable)",
      "command": "spark finetune start {dataset} --base {base} --epochs {epochs} --rank {rank} --max-hours {max_hours}",
      "next": "monitor"
    },
    {
      "id": "monitor",
      "title": "Watch progress; pause/resume as needed",
      "next": "serve"
    },
    {
      "id": "serve",
      "title": "Serve the fine-tuned model",
      "next": "DONE"
    }
  ]
}
```

## scope

Fine-tuning and RAG solve **different** problems — resolve this before building:

- **"Answer using my knowledge-base content"** → that's **RAG**, not fine-tuning.
  Fine-tuning teaches *style/behavior*, not facts. Ground answers at inference time
  with your retrieval tooling; don't fine-tune for it. spark has no part here.
- **"Write code in MY conventions / behave a certain way"** → that's **fine-tuning**.
  This is what `spark finetune start` does: QLoRA on a small coder, served as a GGUF.

spark is the **execution engine** only: standard dataset in → trained, served model
out. **Sourcing the dataset** (turning a KB into instruction pairs, harvesting PR
reviews / docstrings, RAG export, quality-filtering, dedup) is **upstream and your
job** — it stays in your own tooling, never in spark. Keep spark source-agnostic.

## ensure-image

spark **drives** an operator-provided, digest-pinned Unsloth image (like the ComfyUI
/ ai-toolkit images); it does not build one. Point spark at a GB10/sm_121-ready image
with `spark config set unsloth_image <image>`. To build your own, use
`templates/finetune/Dockerfile.reference` (NGC 25.09 base, pinned
bitsandbytes/transformers/trl, triton+xformers from source for sm_121, a bundled
llama.cpp converter for the GGUF export). A published alternative:
`gogamza/unsloth-vllm-gb10:latest`. The first `spark finetune start` pulls the image.

## prepare-dataset

Emit a **`messages` JSONL** — one JSON object per line, OpenAI chat shape (the same
shape `spark llm serve` exposes, so the train-time chat template matches serve time):

```json
{"messages": [{"role": "system", "content": "You write Python in my house style."},
              {"role": "user", "content": "Write a retry decorator."},
              {"role": "assistant", "content": "def retry(...):\n    ..."}]}
```

- Every example needs at least one **assistant** turn — that's the target the model
  learns from. `system` and multi-turn `user`/`assistant` are supported.
- spark **validates the whole file upfront** (schema + line numbers); a bad line
  fails in seconds, before the multi-minute base load. Fix what it reports and re-run.
- A few hundred to a few thousand high-quality, deduped pairs beats a large noisy set.
- The dataset and its rights are **your** responsibility. How you produce it is
  upstream of spark (see **scope**).
- Optional: a held-out `--eval <messages.jsonl>` logs eval loss for an overfit signal.

## finetune

Run `spark finetune start {dataset} --base {base} --epochs {epochs} --rank {rank}
--max-hours {max_hours}` (omit `--base` to use the configured default coder). The box
does **only** fine-tuning while a session is live (unified memory is shared — pass
`--free` to stop a resident ComfyUI / llama-server first).

Defaults are research-backed and overridable: **QLoRA 4-bit** (`--no-quant` for a
full-precision LoRA), **LoRA rank 64** (alpha 2×rank), **lr 2e-4**, **max-seq-len
2048**. It checkpoints every `--save-every` steps and (with `--max-hours N`)
auto-stops cleanly just after a checkpoint once the budget elapses.

## monitor

- `spark finetune status <name>` — status / step progress / ETA / live session.
- `spark finetune status <name> --logs` — follow the trainer output.
- `spark finetune pause <name>` — stop cleanly after the next checkpoint (resumable).
- `spark finetune resume <name> [--max-hours N]` — continue from the latest checkpoint.

Repeat resume across as many time-boxed sessions as it takes; the run is **complete**
at `--epochs`. On completion the LoRA is merged and exported to **GGUF** (q4_k_m),
published into `models_dir` as `<name>/<name>.<quant>.gguf`. The LoRA adapter is
retained for cheap incremental retrain.

## serve

Serve the merged GGUF on the existing llama.cpp path — no new infra:

    spark llm serve <name>
    spark llm bench <name>        # speed
    spark llm probe <name>        # tool-calling / prompt adherence

To **incrementally retrain** later, point a new run's data at the same base and reuse
the retained adapter (lower `--lr`, e.g. 1e-4). Multi-adapter vLLM hot-swap is a
separate stack, not spark's today.
