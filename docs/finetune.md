# Spark LLM fine-tuning (Unsloth QLoRA)

Fine-tune a small **coder** LLM on the DGX Spark so it writes in *your* conventions
and behaves the way you want, then serve the result on the existing llama.cpp path
(`spark llm serve`). This is the LLM analogue of [style-LoRA training](training.md):
same dedicated-box, time-boxed, resumable workflow — but the artifact is a chat model
instead of an image LoRA.

> **spark is the execution engine, not the data pipeline.** It consumes a standard
> dataset and trains. *Sourcing* that dataset — turning a knowledge base into
> instruction pairs, RAG export, harvesting PR reviews/docstrings, dedup,
> quality-filtering — is **upstream and your responsibility**, in your own tooling.
> Keeping spark source-agnostic is deliberate: the public tool stays decoupled from
> any private data infra, small-surfaced, and reusable with *any* dataset.

## Fine-tune vs RAG — pick the right tool first

The whole design hinges on one fork:

- **"Answer using my knowledge-base content"** → **RAG**, not fine-tuning.
  Fine-tuning teaches *style/behavior*, not *facts*. Retrieve the relevant content at
  inference time and feed it to the model. spark has no part in this path.
- **"Write code in MY style / follow MY conventions / behave a certain way"** →
  **fine-tuning**. That's what `spark finetune start` is for.

If you need both, do RAG first (cheaper, and it updates the instant your docs change);
fine-tune only for the style/behavior piece.

## 0. Prerequisites

- **Docker usable on the DGX** (rootless + CDI GPU — the same setup ComfyUI and
  `spark train` use; see the README Troubleshooting table).
- **An operator-provided, digest-pinned Unsloth image** for GB10/sm_121. spark
  **drives** it, never builds it:

  ```bash
  spark config set unsloth_image <image@sha256:…>
  ```

  Build one from [templates/finetune/Dockerfile.reference](../templates/finetune/Dockerfile.reference)
  — NVIDIA's official recipe: NGC `pytorch:25.11-py3` base (its torch/CUDA/triton/xformers
  are already GB10-tuned), `pip install transformers peft hf_transfer datasets trl`, then
  `pip install --no-deps unsloth unsloth_zoo bitsandbytes`, plus a bundled llama.cpp
  converter for the GGUF export. Trust note: prefer building from the NVIDIA base and
  digest-pinning it; if pulling a prebuilt, prefer the Unsloth org's
  `unsloth/unsloth:dgxspark-latest` over a community/individual image. The first
  `spark finetune start` pulls whatever you point `unsloth_image` at.

- **A `messages` JSONL dataset** (next section). You produce this upstream.

## 1. The dataset — `messages` JSONL (the public contract)

One JSON object per line, in OpenAI **chat** shape — the *same* shape `spark llm
serve` exposes at `/v1/chat/completions`, so the chat template applied at train time
matches inference time:

```json
{"messages": [{"role": "system", "content": "You write Python in my house style."}, {"role": "user", "content": "Write a retry decorator with backoff."}, {"role": "assistant", "content": "import time\n\ndef retry(times=3, backoff=0.5):\n    ..."}]}
{"messages": [{"role": "user", "content": "Refactor this for readability:\n..."}, {"role": "assistant", "content": "..."}]}
{"tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}], "messages": [{"role": "user", "content": "Weather in Paris?"}, {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "get_weather", "arguments": "{\"city\": \"Paris\"}"}}]}, {"role": "tool", "content": "18C", "tool_call_id": "c1"}, {"role": "assistant", "content": "It's 18°C in Paris."}]}
```

Rules spark enforces (validated **upfront**, before the run):

- Each line is a JSON object with a non-empty `messages` array.
- Each message has a `role` (`system` / `user` / `assistant` / `tool`) and non-empty
  string `content` — except an `assistant` tool-call turn, where `content` may be empty
  if it carries a `tool_calls` list (each with `function.name`).
- Each example has **at least one `assistant` turn** (text *or* a tool call) — the target.
- `system` prompts and multi-turn conversations are supported (free with `messages`).
- **Tool calling** is supported as a *training format*: add an optional per-line `tools`
  array (the schema), assistant `tool_calls`, and `tool`-role results — they flow into the
  chat template (`tools=`). Whether the *served* model emits **native `tool_calls`** (vs.
  the call landing in `message.content`) depends on the base model's tool-format adherence:
  small coder models often emit JSON in content rather than the `<tool_call>` form
  llama.cpp parses into structured calls. Verify the result with `spark llm probe`.

Validation reports **line numbers** and fails in seconds, so a malformed line never
costs you a multi-minute base load. Fix what it reports and re-run.

A held-out eval set (`--eval <messages.jsonl>`, same format) logs eval loss for an
overfit signal; it's optional and off by default.

> **How you build the dataset is out of scope for spark.** A few hundred to a few
> thousand high-quality, deduped pairs beats a large noisy set. The data and its
> rights are yours.

## 2. Fine-tune (time-boxed, resumable)

```bash
# Uses the configured default base (an Unsloth bnb-4bit coder); 3 epochs.
spark finetune start ~/datasets/house-style.jsonl

# Pick a base + bigger adapter, with a 3-hour per-session budget and an eval set.
spark finetune start ~/datasets/house-style.jsonl \
    --base unsloth/Qwen2.5-Coder-14B-Instruct-bnb-4bit \
    --rank 128 --epochs 2 --max-hours 3 \
    --eval ~/datasets/house-style.eval.jsonl

# Free a resident ComfyUI / llama-server first (unified memory is shared).
spark finetune start ~/datasets/house-style.jsonl --free
```

The box does **only** fine-tuning while a session is live. A memory pre-flight warns
if ComfyUI or llama-servers are resident; `--free` stops them first (opt-in, never
automatic).

### Base model

Any **Unsloth-supported** architecture works — there is no hard enum (matching
`spark download` / `spark llm serve`, which take arbitrary HF repos). The default is
`finetune_base_model` (an Unsloth `*-bnb-4bit` coder, ideal for QLoRA); override per
run with `--base <hf-repo>`. For GB10-vetted picks (sizes, licenses, Apache-2.0
options), see the model-research notes (`dgx-spark-model-research.md`). Review each
model's license for your own use. If Unsloth rejects an arch you'll get a clear error
before the long run.

> **Size is not the binding constraint.** Unsloth QLoRA fine-tunes LLMs up to ~200B
> params on the 128 GB Spark (e.g. gpt-oss-120b 4-bit ≈ 68 GB unified memory); the
> practical sweet spot for a coding specialist is ~7–30B.

### Control surface + defaults (research-backed, overridable)

| Flag | Default | Notes |
|------|---------|-------|
| `--epochs` | `3` | Primary length unit (SFT's natural unit); the completion target across sessions. |
| `--max-hours` | `0` (off) | Per-session wall-clock budget; auto-stops cleanly after the next checkpoint. |
| `--rank` | `64` | LoRA rank; alpha is set to **2×rank**. |
| `--lr` | `2e-4` | 2e-4 for a fresh LoRA; ~1e-4 for incremental retrain. |
| `--max-seq-len` | `2048` | Raise for long-context coding samples. |
| `--save-every` | `50` | Checkpoint every N optimizer steps — a clean stop loses ≤ one interval. |
| `--gguf-quant` | `q4_k_m` | The exported/served GGUF's quantization. |
| `--no-quant` | off | Train a full-precision LoRA instead of QLoRA 4-bit (small bases). |
| `--eval` | off | Optional held-out `messages.jsonl` → eval loss. |

QLoRA 4-bit, all-linear LoRA targets, dropout 0.05, and an 8-bit AdamW optimizer are
the built-in defaults.

## 3. Watch, pause, resume

```bash
spark finetune status                 # status / step progress / ETA / live session
spark finetune status <name> --logs   # follow the live trainer output
spark finetune pause                  # stop cleanly after the next checkpoint (resumable)
spark finetune resume [--max-hours N] # continue from the latest checkpoint
```

The step count is the latest **saved checkpoint** (every `--save-every` steps), so it
can lag the live step by up to one interval — `--logs` shows the live step. A run
still at step 0 with a live session is **downloading/loading the base model** (shown
as `preparing`, with the HF cache size as a progress proxy).

Resume as many time-boxed sessions as it takes; each trains another `--max-hours`
chunk and stops cleanly after a checkpoint, until the run reaches `--epochs`. Resume
picks up from the latest checkpoint automatically.

## 4. Serve the fine-tuned model

On completion the LoRA is **merged** and **exported to GGUF** (default `q4_k_m`),
published into `models_dir` as `<name>/<name>.<quant>.gguf` — an ordinary GGUF that
slots straight into the existing serve path with **zero new infra**:

```bash
spark llm serve <name>        # load the merged GGUF on llama.cpp
spark llm bench <name>        # speed (tokens/sec)
spark llm probe <name>        # tool-calling / prompt adherence
```

The LoRA **adapter** is also retained alongside the run output (`output/<name>/adapter/`)
— cheap insurance for **incremental retrain** (point a new run at the same base and
reuse it, with a lower `--lr`) and a possible future vLLM multi-adapter hot-swap path.
spark serves GGUF via llama.cpp today; vLLM adapter serving is a separate stack, out of
scope.

## How it fits together

```
your dataset (messages JSONL)        ← you build this, upstream (KB→pairs / RAG export / scrape)
        │  staged to {finetune_dir}/datasets/<name>.jsonl
        ▼
spark finetune start  ──drives──▶  Unsloth image (operator-provided, digest-pinned)
   host orchestration               in-container:
   - validate JSONL upfront           spark_finetune.py  (watchdog: time-box / pause / resume)
   - render job-config JSON            └─ unsloth_train.py (QLoRA SFT → merge → GGUF export)
   - screen session over SSH
        │
        ▼
   output/<name>/{checkpoint-*/, adapter/, gguf/}
        │  on completion: publish GGUF → models_dir/<name>/<name>.<quant>.gguf
        ▼
spark llm serve <name>           ← merged GGUF on the existing llama.cpp path
```

The time-box / checkpoint / resume machinery is the **shared** `bin/spark_watchdog.py`
— the same loop `spark train` uses for image LoRAs; this verb supplies only the
Unsloth-specific checkpoint detection and trainer launch.

## Troubleshooting

- **"No Unsloth image configured."** Set `unsloth_image` (see Prerequisites). spark
  drives an operator-provided image; it does not build one.
- **"complete but no GGUF found to publish."** Training finished and the adapter is
  saved (`output/<name>/adapter/`), but the merge→GGUF export failed — usually the
  image lacks a llama.cpp converter. Check `spark finetune status <name> --logs`;
  the run isn't lost, the adapter can be re-exported.
- **OOM / very slow.** Free the box (`--free`, or `spark comfy stop` / `spark llm
  stop`) — unified memory is shared. Lower `--max-seq-len`, use a smaller base, or keep
  QLoRA 4-bit (the default) rather than `--no-quant`.
- **Base download stalls on a flaky link.** The in-container fetch uses
  huggingface_hub's resume-safe single-stream downloader; a gated base repo needs an
  `HF_TOKEN` in spark's environment (passed into the container `.env`).
- **Different image/version.** The trainer is written to NVIDIA's documented recipe and
  validated on the reference image (`trl==0.26.1`). It adapts to TRL drift automatically
  (`processing_class` vs `tokenizer`, SFTConfig field renames); if you use a very different
  image and a step fails, `templates/finetune/unsloth_train.py` is the place to adjust.
