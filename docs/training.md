# Spark style-LoRA training (FLUX.2)

Train a FLUX.2 **style LoRA** from a corpus of images on the DGX Spark. spark
provides the **framework** — the trainer, the time-boxed/resumable session control,
and the inference wiring; the corpus and its rights are **yours**.

> **Fine-tuning an LLM instead?** This page is image style-LoRAs. To fine-tune a
> small **coder LLM** (Unsloth QLoRA → GGUF, served via `spark llm serve`), see
> **[docs/finetune.md](finetune.md)** — same dedicated-box, time-boxed,
> resumable workflow, sharing the [bin/spark_watchdog.py](../bin/spark_watchdog.py)
> machinery.

> **Base model.** The default is **FLUX.2-klein-4B**
> ([Apache-2.0](https://huggingface.co/black-forest-labs/FLUX.2-klein-base-4B), ungated,
> no token). **FLUX.2-klein-9B**
> ([license](https://huggingface.co/black-forest-labs/FLUX.2-klein-base-9B)) is a gated
> opt-in with more capacity. Review each model's license for your own use. See
> [Base model](#base-model) below.

Training runs [ai-toolkit (ostris)](https://github.com/ostris/ai-toolkit) in a
**dedicated** container, driven through a detached `screen` session over SSH, exactly
like `spark llm serve`. As with the ComfyUI image, spark **drives an operator-
provided, digest-pinned** ai-toolkit image — it does not build or vendor one — so
ai-toolkit's brittle GPU dep tree (torch / flash-attn / torchcodec on sm_121) stays
out of this repo. spark deploys only the orchestration (compose + watchdog).

> While a training session is live the box does **only** training — no concurrent
> serving. That's deliberate: the GB10's unified memory is shared, so training gets
> full horsepower and there's no OOM-while-serving contention. `spark train start`
> runs a memory pre-flight and warns if ComfyUI or llama-servers are still resident;
> pass `--free` to stop them first (opt-in).

---

## 0. Prerequisites

- Docker usable on the DGX (`spark comfy status` confirms the daemon; same engine).
- The **base model** is fetched by ai-toolkit on first run into the container's HF
  cache — nothing to pre-download. The default **klein-4B is Apache/ungated (no
  token)**; see [Base model](#base-model) to switch bases.
- An **ai-toolkit container image** for the GB10/sm_121 — operator-provided and
  digest-pinned, exactly like the ComfyUI image (spark drives it, never builds it):

  ```bash
  spark config set aitoolkit_image <image@sha256:…>   # a GB10/sm_121-ready image
  ```

  `spark train start` pulls it on first run. To build your own, see
  [../templates/train/Dockerfile.reference](../templates/train/Dockerfile.reference);
  the image contract is just: `python3` on PATH and ai-toolkit's `run.py` present
  (default `/opt/ai-toolkit/run.py`; override with `AITOOLKIT_RUN` in `{train_dir}/.env`).

## 1. Prepare a corpus

A directory of **style-consistent** images you are cleared to use. Recipe:

- ~20–60 images at roughly 1024px on the long edge.
- Caption each image's **content** in a sidecar `<image>.txt` (same basename):
  describe the subject/composition/setting, *not* the style. The **trigger word**
  carries the style, so the model learns "style = the trigger" rather than baking the
  style into content words.

  ```
  corpus/
    01.png   01.txt   →  "a woman standing by a window, half-light"
    02.png   02.txt   →  "a harbour at dawn, boats at anchor"
  ```

- No captions yet? Pass `--auto-caption` to generate the sidecars from a served
  vision model (`spark llm serve <vlm>` first); the trigger word is prepended for you.

**Where to put it.** Any directory works — just put the images (and captions) in one
folder named for the style. A tidy convention on your workstation:

```
~/lora-training/
  my-art-style/    # <- point `spark train start` at this folder
    01.jpg  01.txt
    02.jpg  02.txt
    ...
```

(That folder is the "corpus" the commands refer to — it just means *your set of
training images*.)

### Naming — the two names you make up

You choose two names. **Both are yours to invent** — they are not spark internals, repos,
or anything the tools already know:

- **`--name <name>`** — a plain label for this run, like `my-art-style`. It becomes the
  **LoRA filename** (`my-art-style.safetensors`) and the on-box run folder, and it
  **defaults to the corpus folder name**. Use a **distinct name per run**, or a fresh
  run will resume/overwrite an earlier one of the same name.
- **`--trigger <word>`** — the word you'll **type in prompts** to switch the style on
  (`"a lighthouse mystylexr"`). Make it **one made-up token the model has never seen**,
  so your style binds cleanly to it. Avoid real words like `watercolor` or `noir` — the
  model already has its *own* idea of those, which muddies yours. Good triggers are
  coined/unique: `mystylexr`, `artzbk`, or a readable name with an odd tag like
  `myartstyle7`.

So a run might be `--name my-art-style --trigger mystylexr` — a readable file label, and
a unique nonsense trigger. If you omit `--name`, the corpus folder name is used.

## 2. Train (time-boxed, resumable)

```bash
spark train start ~/lora-training/my-art-style --trigger mystylexr --max-hours 3
```

What happens: the corpus is staged to the DGX, an ai-toolkit config is rendered from
[../templates/train/aitoolkit_config.yaml](../templates/train/aitoolkit_config.yaml),
and the watchdog launches the run in the `spark-train` screen session. It checkpoints
every `--save-every` steps (default 250) and, once `--max-hours` elapses, **stops
cleanly just after the next checkpoint** — never mid-save — so a session lasts about
the budget and loses at most one interval.

Useful flags (`spark train start --help` for all):

| Flag | Default | Meaning |
|------|---------|---------|
| `--trigger` | (required) | Word that invokes the style; put it in the prompt at inference |
| `--name` | corpus folder name | Run / output LoRA name |
| `--max-hours` | `3` | Per-session time budget (`0` = run straight to the target) |
| `--steps` | `2000` | Total training steps — the completion target across all sessions |
| `--save-every` | `250` | Checkpoint cadence (a clean stop loses ≤ one interval) |
| `--rank` | `16` | LoRA rank/dim — higher = more capacity + bigger file |
| `--auto-caption` | off | Caption missing images via a served vision model first |
| `--free` | off | Stop resident ComfyUI / llama-servers first, so training owns the box |

## 3. Watch, pause, resume

```bash
spark train status                 # status, step N/target, ETA, whether a session is live
spark train status --logs          # follow the live ai-toolkit output
spark train pause                  # stop cleanly after the next checkpoint (resumable)
spark train resume [--max-hours N] # continue from the latest checkpoint, another chunk
```

Resume picks up from the latest checkpoint and trains another `--max-hours` chunk.
Repeat `resume` across as many sessions as it takes; the run is **complete** when it
reaches `--steps`. On completion the latest checkpoint is published into ComfyUI's
`models/loras/` as `<name>.safetensors`.

## 4. Generate with the LoRA

```bash
# klein-trained LoRA (the default training base) — switch comfy to the klein base:
spark comfy pull-models --set generate-klein            # once
spark comfy generate "mystylexr a busy harbor at dawn" --base flux2-klein-4b --lora my-art-style.safetensors
```

The LoRA loads as a single `LoraLoaderModelOnly` spliced between the FLUX.2 UNET
loader and `ModelSamplingFlux` (FLUX.2 style LoRAs are model-only). Put the **trigger
word** in the prompt; `--lora-strength` (default 1.0) scales the effect. The name is
validated against ComfyUI's own LoRA list, so a typo fails fast with the choices.

> **Match the base to the LoRA's training base.** spark trains klein LoRAs, so render
> with `--base flux2-klein-4b` (after `comfy pull-models --set generate-klein`). A LoRA
> trained elsewhere on FLUX.2-dev loads on comfy's default base. `--turbo` is
> FLUX.2-dev only.

You can also drop any FLUX.2 `.safetensors` LoRA into
`{comfy_dir}/workspace/models/loras/` and use it the same way.

klein nails style but is weak on **text/fine detail** — refine the keepers through a
stronger model with [`spark comfy refine`](../commands/comfy/refine.md) (img2img @
denoise 0.5, FLUX.2-dev).

### Alternative — `spark train sample` (render via the trainer)

Also works for **any** base, straight from a run without switching the comfy base — it
runs ai-toolkit in the training container, loading the run's base + the trained LoRA
(pure inference, the LoRA is not modified), and downloads the images. Handy right after
training; `comfy generate --base` is faster for repeated/warm iteration.

```bash
spark train sample "mystylexr a busy harbor at dawn, boats, crates, gulls" --name my-art-style
spark train sample "mystylexr a dragon over a city" "mystylexr a quiet cafe" --seed 7 --steps 25
```

Put the trigger word in each prompt; pass several prompts to render them all. Images
land in `./<name>-samples/` (or `--out <dir>`). The base/arch are taken from the run's
own config and the final `<name>.safetensors` is used. First run loads the base into
VRAM (a few min).

---

## Base model

ai-toolkit fetches the base from HuggingFace on first run (into the mounted HF cache);
spark just sets `name_or_path` + `arch` in the rendered config.

Review each model's license (linked) for your own use.

| Base | `train_base_model` / `train_arch` | License | Token? |
|------|-----------------------------------|---------|--------|
| **klein-4B** (default) | `black-forest-labs/FLUX.2-klein-base-4B` / `flux2_klein_4b` | [Apache-2.0](https://huggingface.co/black-forest-labs/FLUX.2-klein-base-4B) | no |
| klein-9B | `black-forest-labs/FLUX.2-klein-base-9B` / `flux2_klein_9b` | [license](https://huggingface.co/black-forest-labs/FLUX.2-klein-base-9B) (gated) | yes |

> **Not FLUX.2-dev.** dev (32B) is too large to fine-tune on a single 128 GB Spark —
> ai-toolkit [#531](https://github.com/ostris/ai-toolkit/issues/531) shows a ~256 GB box
> OOMing with every optimization on (closed *not planned*), and NVIDIA's own Spark image
> fine-tuning targets the 12B FLUX.1-dev. Use klein (4B/9B); dev stays a generation /
> [`refine`](../commands/comfy/refine.md) model only.

Switch base:

```bash
# default — ungated, no token, nothing to set
spark train start ~/lora-training/my-art-style --trigger mystylexr

# klein-9B (gated, more capacity): accept its license on HF, then seed + train offline:
spark config set train_base_model black-forest-labs/FLUX.2-klein-base-9B
spark config set train_arch flux2_klein_9b
HF_TOKEN=hf_xxx spark train fetch-base
SPARK_TRAIN_OFFLINE=1 spark train start ~/lora-training/my-art-style --trigger mystylexr
```

For a gated base, `HF_TOKEN` (from spark's environment) is written into
`{train_dir}/.env` and passed to the container — the same "gated → token on the box"
path documented under `spark download`. The default klein-4B base needs none of this.

### Large bases on a flaky link — prefetch + offline

ai-toolkit downloads the base on first run, but a large base (klein-9B, dev) pulls
multi-GB components — the DiT transformer, the **Qwen text encoder** (`Qwen/Qwen3-8B`
for 9B, `Qwen/Qwen3-4B` for 4B), and the VAE. On an unreliable connection any one of
these can stall mid-stream and hang the run, and ai-toolkit loads the text encoder by
a **hardcoded repo id with no local-path option** — so it must be in the cache to
train offline.

`spark train fetch-base` pre-seeds the mounted HF cache (`{train_dir}/cache/huggingface`)
with all three components using the bundled resume-safe downloader (8 retries,
HTTP-Range resume), in the layout `from_pretrained` / `hf_hub_download` resolve with
`HF_HUB_OFFLINE=1`. Then start the run offline so it never touches the network:

```bash
spark config set train_base_model black-forest-labs/FLUX.2-klein-base-9B
spark config set train_arch flux2_klein_9b
HF_TOKEN=hf_xxx spark train fetch-base            # resume-safe — re-run if the link drops
SPARK_TRAIN_OFFLINE=1 spark train start ~/lora-training/my-art-style --trigger mystylexr
```

`SPARK_TRAIN_OFFLINE=1` sets `HF_HUB_OFFLINE=1` in the container, so every component
loads from the seeded cache (no network). The token is only needed at `fetch-base`
time for a gated base; the ungated text encoder and VAE seed regardless. For the
default klein-4B base this is optional (its components are small enough to fetch
inline).

## How it fits together

| Piece | Where |
|-------|-------|
| Host-side verbs (deploy, stage, launch, control, publish) | [../lib/handlers/train.py](../lib/handlers/train.py) |
| In-container watchdog (time-budget safe-stop, checkpoint detection) | [../bin/spark_train.py](../bin/spark_train.py) |
| Orchestration deployed to the box (compose + ai-toolkit config template) | [../templates/train/](../templates/train/) |
| Build-your-own image reference (NOT built by spark) | [../templates/train/Dockerfile.reference](../templates/train/Dockerfile.reference) |
| Inference wiring (`--lora`) | `generate()` in [../lib/handlers/comfy.py](../lib/handlers/comfy.py) |
| Config keys (`train_dir`, `train_base_model`, `train_arch`, `aitoolkit_image`) | [../lib/sparkcore.py](../lib/sparkcore.py) |

Remote layout under `{train_dir}` (default `~/spark-train`):

```
compose.yaml  bin/spark_train.py               # deployed orchestration (no Dockerfile)
datasets/<name>/   configs/<name>.yaml         # staged corpus + rendered config
output/<name>/     state/<name>.json           # checkpoints + run state
output/<name>/samples/                         # ai-toolkit sample images per checkpoint
control/<name>/stop   logs/<name>.log          # pause flag + session log
.env                                           # AITOOLKIT_IMAGE (+ HF_TOKEN only for a gated base)
cache/huggingface/                             # base model + text encoder, fetched by ai-toolkit
```

> The base model is **not** stored in the repo or pre-staged — ai-toolkit downloads it
> into the mounted HF cache on first run. The default klein-4B base is ungated (no
> token); only a gated base (klein-9B / dev) needs `HF_TOKEN`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `Couldn't obtain the ai-toolkit image` | `aitoolkit_image` unset/unreachable | `spark config set aitoolkit_image <image@sha256:…>` (GB10/sm_121-ready), or build one from `Dockerfile.reference` and point spark at it |
| Image runs but training errors immediately | image lacks ai-toolkit at the expected path | set `AITOOLKIT_RUN=<path to run.py>` in `{train_dir}/.env` |
| `Cannot train — Docker is not usable` | daemon down / permission | same remedy as `spark comfy` — see the README Troubleshooting table |
| Base download 401/403 | gated base (dev / klein-9B) without a token | accept the license on HF and re-run with `HF_TOKEN=…`, or use the default klein-4B (ungated) |
| Run hangs at step 0 on a big base (download stalls mid-stream) | flaky link to HF on a multi-GB component (e.g. `Qwen/Qwen3-8B`) | pre-seed the cache with `spark train fetch-base` (resume-safe), then `SPARK_TRAIN_OFFLINE=1 spark train start …` — see "Large bases on a flaky link" above |
| `state_dict` unexpected `*.weight_scale` keys | base is an fp8 checkpoint (e.g. comfy's `flux2_dev_fp8mixed`) | use a bf16 base via `train_base_model` (a HF repo), not an fp8 file — ai-toolkit fine-tunes full-precision weights |
| `LoRA '<name>' is not in models/loras` at generate | not published / typo | `spark train status <name>` (publishes on complete), or check the loras dir |
| Run won't resume | no checkpoint saved yet | needs ≥ one checkpoint (`--save-every` steps); check `spark train status --logs` |

> The ai-toolkit `model:` block is rendered from `train_base_model` + `train_arch`
> (default klein-4B). If a future ai-toolkit changes its arch names or config schema,
> adjust [../templates/train/aitoolkit_config.yaml](../templates/train/aitoolkit_config.yaml)
> — it's a plain template the handler only does token substitution on.
