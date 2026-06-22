# finetune start

```spec
{
  "name": "finetune.start",
  "domain": "finetune",
  "subcommand": "start",
  "summary": "Fine-tune a small LLM (Unsloth QLoRA) from a messages JSONL on the DGX",
  "handler": "finetune.start",
  "params": [
    {"name": "dataset",     "positional": true, "required": true, "help": "Local messages JSONL: one {\"messages\":[{\"role\",\"content\"}, …]} object per line"},
    {"name": "name",        "type": "string", "help": "Run / model name (default: derived from the dataset filename)"},
    {"name": "base",        "type": "string", "help": "Base model to fine-tune (HF repo or local path); default: finetune_base_model (an Unsloth bnb-4bit coder)"},
    {"name": "epochs",      "type": "int",   "default": 3,    "help": "Training epochs (the completion target across all sessions)"},
    {"name": "rank",        "type": "int",   "default": 64,   "help": "LoRA rank; alpha is set to 2×rank. Higher = more capacity + larger adapter"},
    {"name": "lr",          "type": "float", "default": 0.0002, "help": "Learning rate (2e-4 fresh LoRA; ~1e-4 for incremental retrain)"},
    {"name": "max_seq_len", "type": "int",   "default": 2048, "help": "Max sequence length (raise for long-context coding samples)"},
    {"name": "save_every",  "type": "int",   "default": 50,   "help": "Checkpoint every N optimizer steps — a clean stop loses at most one interval"},
    {"name": "max_hours",   "type": "float", "default": 0.0,  "help": "Per-session time budget; auto-stops cleanly after the next checkpoint (0 = run to target epochs)"},
    {"name": "gguf_quant",  "type": "string", "default": "q4_k_m", "help": "Output GGUF quantization (the served artifact)"},
    {"name": "eval",        "type": "string", "help": "Optional eval messages JSONL — logs eval loss for an overfit signal (off by default)"},
    {"name": "no_quant",    "type": "bool",  "help": "Train a full-precision LoRA instead of QLoRA 4-bit (for small bases with memory to spare)"},
    {"name": "free",        "type": "bool",  "help": "Free the box for fine-tuning first — stop ComfyUI and any llama-servers holding unified memory (opt-in)"}
  ]
}
```

Fine-tunes a small coder LLM with **Unsloth QLoRA** in a dedicated container, in a
detached `screen` session on the DGX — the box does only fine-tuning while this
runs (no concurrent serving; the GB10's unified memory is shared). spark drives an
**operator-provided, digest-pinned** Unsloth image (`spark config set unsloth_image
…`, like the ComfyUI / ai-toolkit images) and pulls it on first run; it does not
build one. This is the LLM analogue of `spark train` (which trains image LoRAs) and
reuses the same time-box / checkpoint / resume machinery.

**Source-agnostic.** spark consumes a **standard `messages` JSONL** (chat format)
and trains — it does not know or care whether the data came from your knowledge
base, hand-written pairs, PR reviews, or a scrape. Producing that dataset (KB→pairs,
RAG export) is **upstream and out of scope** — it stays in your own tooling. This
keeps the public tool decoupled from any private data pipeline. Each line is one
example:

    {"messages": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}

The dataset is **validated upfront** (schema + line numbers) before the expensive
run, so a bad line fails in seconds rather than after the multi-minute base load.
Every example needs at least one `assistant` turn (that's the target).

The base defaults to `finetune_base_model` (an Unsloth `*-bnb-4bit` coder built for
QLoRA); override per run with `--base <hf-repo>`. Any Unsloth-supported arch works
(no hard enum, matching `spark download` / `spark llm serve`); see
`dgx-spark-model-research.md` for GB10-vetted picks. QLoRA 4-bit and LoRA `rank 64`
are the research-backed defaults — `--no-quant` trains a full-precision LoRA.

The session is **time-boxed and resumable**: `--max-hours` auto-stops the run
cleanly just after the next checkpoint once the budget elapses, and `spark finetune resume` continues from the latest checkpoint until `--epochs` is reached.
Checkpoints land every `--save-every` steps, so a stop loses at most one interval
and never corrupts a half-written checkpoint.

On completion the LoRA is **merged and exported to GGUF** (default `q4_k_m`),
published into `models_dir` as `<name>/<name>.<quant>.gguf` — serve it with `spark
llm serve <name>`. The LoRA adapter is **retained** alongside the run output for
cheap incremental retrain (and a future vLLM hot-swap path).

A memory pre-flight warns if ComfyUI or llama-servers are still resident in the
shared unified memory; pass `--free` to stop them first (opt-in — never automatic).

Examples:

    spark finetune start ~/datasets/house-style.jsonl
    spark finetune start ~/datasets/house-style.jsonl --base unsloth/Qwen2.5-Coder-14B-Instruct-bnb-4bit --rank 128
    spark finetune start ~/datasets/house-style.jsonl --epochs 2 --max-hours 3 --eval ~/datasets/house-style.eval.jsonl
