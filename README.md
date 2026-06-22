# Spark-CLI

CLI for managing all services on a DGX Spark (GB10) over SSH.
The canonical interface for anything running on the Spark — agents should use these commands rather than deploying directly to the host.

## Prerequisites

- **SSH access** to a DGX Spark (GB10) host — `spark` manages the remote host entirely over SSH, so the DGX must be reachable and your key trusted before `spark init`.
- **Remote services** must be set up on the DGX before the corresponding `spark` commands will work: `llama-server` (for `spark llm`), a running ComfyUI container (for `spark comfy`), `whisper-server` (for `spark transcribe`), and a `qwen-tts` Python venv (for `spark tts`). **[docs/install.md](docs/install.md)** is the from-scratch setup (build the pinned engines, venv, models); **[docs/secure-deployment.md](docs/secure-deployment.md)** then hardens it.
- **Python 3** on your workstation (stdlib only — no pip installs, no venv).

## Setup

```bash
# Add this to your ~/.bashrc (or equivalent shell profile),
# adjusting the path to match your actual checkout location:
export PATH="$HOME/dev/projects/spark/bin:$PATH"

# First-time config
spark init
```

## Commands

```
spark init                                        First-time setup — create ~/.config/spark.json
spark status                                      Show all services (LLM, ComfyUI, Whisper, RAM)
spark models                                      List downloaded models with quant and size
spark disk [--prune]                              DGX disk usage by consumer; --prune reclaims Docker space

# Guided task flows (agent-friendly)
spark playbook <list|show|run|check> [name]       Discover & walk self-describing task flows

# LLM serving (one llama-server per model — several can run at once)
spark llm serve <model> [--quant Q] [--port N] [--ctx N] [--parallel N]
                                                  Load a model (refuses if it won't fit)
spark llm list                                    Show loaded models, ports, footprints
spark llm unload <name|--port N>                  Unload one model, free its memory
spark llm rm <model> [--quant Q]                  Delete a downloaded model from disk (typed-name confirm)
spark llm stop                                    Stop ALL LLM servers at once
spark llm logs [--port N] [--lines N]             Tail a server log
spark llm open [--port N]                         Open built-in chat UI in browser
spark llm bench [<model>] [--runs N]              Measure a loaded model's speed (tokens/sec)
spark llm probe [<model>] [--serve --unload]      Verify tool-calling & prompt adherence (needs: pipx install llm-probe)
spark llm reports [--out F]                        Render saved bench+probe results (reports/) as a Markdown table

# Image / video generation
spark comfy <start|stop|status|logs|queue>        Manage AEON-Spark ComfyUI (port 8188)
spark comfy generate "<prompt>" [--lora N] [--base B] [--turbo]  FLUX.2 text-to-image (+ trained LoRA; --base flux2-klein-4b for klein LoRAs; --turbo = few-step)
spark comfy refine <image> ["<prompt>"]           Re-run an image through a stronger model (fix text/detail; img2img @ denoise 0.5)
spark comfy edit <image> "<instruction>"          Instruction image edit (Qwen-Image-Edit; replace/change parts)
spark comfy animate <image> "<prompt>"            LTX-2.3 image-to-video, downloads the MP4
spark comfy qr-art <url> [--style --mode]         Scannable QR-code art (ControlNet)
spark comfy models                                List downloaded ComfyUI models + sizes, flag reclaimable orphans
spark comfy rm <file> | --orphans                 Delete ComfyUI model file(s) to free disk
spark comfy pull-models [--set generate|generate-klein|edit|animate|qr-art|all]  Download the models those commands need

# Style-LoRA training (FLUX.2, on the dedicated DGX)
spark train start <corpus> --trigger <word>       Train a FLUX.2 style LoRA from a corpus of images
            [--max-hours N --steps N --auto-caption]    time-boxed, resumable; publishes to comfy loras
spark train <status|pause|resume> [name]          Watch progress / stop after a checkpoint / continue
spark train sample "<prompt>" [--name N]          Render prompts from a trained LoRA (inference, no retrain)

# Audio transcription
spark transcribe <start|stop|status|logs>         Manage whisper-server (port 8081)
spark transcribe pull-models [--model M|--all]    Download whisper ggml model(s)

# Speech synthesis (TTS)
spark tts say "<text>" [--out F --speaker V --instruct "..." --language L]
                                                  Synthesize speech (Qwen3-TTS), downloads the .wav
spark tts pull-models                             Download the Qwen3-TTS model

# Model downloads
spark llm pull-models [<name>...|--all]           Download catalog LLM model(s) (lists sizes if no args)
spark download <repo> <name> <pattern>            Download a single model from HuggingFace
spark queue <repo> <name> <pattern> [...]         Queue multiple downloads sequentially
spark logs-dl                                     Tail the download queue log

# Inference engines (pinned builds)
spark engine status [<engine>]                    Installed commit vs pin (drift)
spark engine build <engine> [--ref X|--latest]    Rebuild from the pin (or move it)

# Local image compositing (requires Pillow)
spark image detect-region <img> [--search-box ...] Print the bbox of the main object
spark image extract-asset <img> --bbox ... --out   Crop a sub-image
spark image move-region <img> --bbox ... --dy N    Relocate a region, bg-fill the source
spark image overlay-centered <img> --assets ...    Paste assets as a centered group
```

## Playbooks (guided task flows)

A **playbook** is a self-describing task flow an agent can discover and walk — so it
knows what to ask you and what to run, instead of reverse-engineering `--help`.

```bash
spark playbook list                       # all flows (shipped + personal)
spark playbook show audio-transcribe      # required inputs + the step map
spark playbook run audio-transcribe --model large-v3   # walk it, one step at a time
```

`run` walks the flow a step at a time: **step 0 checks preconditions** (is the model
downloaded? is ComfyUI up?) and, if something's missing, prints the install remedy
rather than failing — then serves each step's command for the agent to run, plus the
next step. spark stays stateless; the agent carries the answers.

Playbooks are single files — a fenced ` ```spec ` JSON block (typed inputs +
precondition-gated steps) plus `## <step-id>` markdown pages. They merge two sources:

- **Shipped** (general) in [`playbooks/`](playbooks/): `audio-transcribe`,
  `image-to-video`, `llm-serve`, `qr-art`.
- **Personal** (git-ignored) in `~/.config/spark/playbooks/` — your own recipes; these
  override shipped ones by name. Copy [`playbooks/playbook.example.md`](playbooks/playbook.example.md)
  to start. Validate with `spark playbook check <name>`.

## MCP server

[`bin/spark-mcp`](bin/spark-mcp) exposes every command as an MCP tool, so any MCP client
can drive the Spark directly — not only via CLI-over-SSH. It
reuses the **same** command manifests, parser, handlers, and schema converter as the CLI
(no second definition): each tool is named `<domain>_<verb>` (e.g. `llm_serve`), its
input schema is generated from the command's `params`, and a call runs the same handler
and returns its output plus the structured `{action, …}` result. stdlib-only (hand-rolled
stdio JSON-RPC).

Point a client at it:

```json
{ "mcpServers": { "spark": { "command": "/path/to/spark/bin/spark-mcp" } } }
```

`spark _schema [name]` prints the same tool schemas for inspection.

Models for each service are listed in an editable catalog,
`templates/models.json` (sections: `comfy`, `tts`, `whisper`, `llm`). The `llm` section lists large MoE models validated for the GB10's
128 GB unified memory in Unsloth Dynamic (UD) quants — each entry carries the quant,
footprint, and rationale (run `spark llm pull-models` to see them). Edit freely.
Measured speed and capability for each on the GB10:
**[reports/reference/RESULTS.md](reports/reference/RESULTS.md)** (the rendered table) —
how it's measured and what it covers: **[docs/benchmarks.md](docs/benchmarks.md)**.

## LLM serving

Each model runs as its own `llama-server` on its own port, so several can be
loaded at once (memory permitting). Models are addressed by **port** (always
unambiguous) or by **name**.

```bash
# List what's downloaded
spark models

# Load by name — prompts if multiple quants. Picks the next free port (from 30000).
spark llm serve <model>

# Load a specific quant directly (no prompt)
spark llm serve <model> --quant Q5_K_M

# Load a second model alongside — gets the next free port (30001, ...)
spark llm serve <another-model>

# See what's resident, with ports and footprints
spark llm list
#   :30000  model-a  Q4_K_XL  16G  pid 115536
#   :30001  model-b  Q5_K_M   25G  pid 123565
```

Loading never evicts another model behind your back. The fit check estimates
**weights + KV cache** (the KV term is read from the model's GGUF dims and scales
with `--ctx`/`--parallel`) and requires a free **reserve** on top (`mem_reserve_gb`,
default 8). If it won't fit, `serve` **refuses** and lists what's resident so you
can choose what to free — you stay in control of what gets unloaded. The unified
memory is shared with ComfyUI, so a loaded comfy can be why a model won't fit;
`serve` surfaces that and offers `--free-comfy` to stop it first (never automatic):

```bash
spark llm serve <large-model>
#   ✗ <large-model> needs ~121G (~77G weights + ~44G KV @ ctx 8192×4) plus a 8G reserve, but only 77G is free.
#     Resident models (unload some to make room):
#       :30000  model-a ...   spark llm unload --port 30000
#       :30001  model-b ...   spark llm unload --port 30001

# Unload exactly one — by port (exact) or name (refuses if a name is ambiguous)
spark llm unload --port 30001
spark llm unload <model>

# Stop everything at once
spark llm stop
```

`unload` and `stop` free only LLM servers — whisper and ComfyUI are untouched.

Chat UI (built-in, no install): `http://gx10-<id>.local:30000` (or `spark llm open [--port N]`)

## Image and video generation (ComfyUI)

AEON-Spark — a pre-built Docker image for GB10 (handles sm_121 / UMA / Blackwell).
Manage the container, then generate/animate from the CLI — jobs hit ComfyUI's HTTP
API and the output downloads to your workstation:

```bash
spark comfy start                                    # start (docker compose) — status/stop/logs/queue too
spark comfy pull-models [--set generate|animate]     # fetch the FLUX.2 / LTX-2.3 models (once)
spark comfy generate "a red fox in a snowy forest"   # FLUX.2 text-to-image → PNG
spark comfy generate "a red fox" --turbo             # few-step distilled → seconds
spark comfy animate fox.png "the fox leaps and runs" # LTX-2.3 image-to-video → MP4
```

UI at `http://gx10-<id>.local:8188`. First run loads the models into unified memory
(a few minutes); after that image gens take ~30–60 s, i2v a few minutes. Run
`spark comfy <cmd> --help` for the full flag set.

📖 **[docs/media-workflows.md](docs/media-workflows.md)** — full guide: image gen,
animation, the **cut-out → composite → animate** recipe (fly a character onto
another scene), the model list, and **how to add a new ComfyUI workflow** as a
spark command (the `tools/flatten_comfy_workflow.py` flatten → template → CLI flow).

> **Docker permission denied?** Add your user to the `docker` group once (durable):
> `sudo usermod -aG docker user` then log out/in. `sudo chmod 666 /var/run/docker.sock`
> works too but is ephemeral — it reverts on any daemon/socket restart (e.g. an
> engine upgrade). See [Troubleshooting](#troubleshooting) for daemon-down and other failures.

> **⚠ Running a third-party GPU image.** The ComfyUI stack is a community image
> (and ComfyUI custom nodes run arbitrary code). For a hardened deployment — a
> dedicated `svc-spark` account, rootless Docker + CDI so a container escape isn't
> host root, image digest-pinning, and the `/opt/spark` layout — see
> [docs/secure-deployment.md](docs/secure-deployment.md).

## Style-LoRA training (FLUX.2)

Generate "in the style of" a corpus by training a FLUX.2 **style LoRA** on the DGX.
Training runs ai-toolkit (ostris) in a **dedicated** container, driven through a
detached `screen` session over SSH. As with the ComfyUI image, spark **drives an
operator-provided, digest-pinned** ai-toolkit image (`spark config set aitoolkit_image
…`) rather than building one — keeping ai-toolkit's brittle GPU dep tree out of this
repo. spark deploys only the orchestration ([templates/train/](templates/train/):
compose + watchdog) and pulls the image on first run.

```bash
# Point at a folder of style-consistent images you're cleared to use (~20–60 @ ~1024).
# Caption each image's CONTENT in a sidecar <image>.txt; the trigger word carries the style.
spark train start ~/lora-training/my-art-style --trigger mystylexr --max-hours 3
spark train status                                   # progress, ETA, live session
spark train pause                                    # stop cleanly after the next checkpoint
spark train resume                                   # continue from there, another time-boxed chunk
```

A run is **time-boxed and resumable**: `--max-hours` auto-stops just after the next
checkpoint, and the box does *only* training while a session is live. The corpus and
its rights are **yours** — spark provides the framework, not the content.
`--auto-caption` fills in missing captions from a served vision model
(`spark llm serve <vlm>`).

**Base model.** The default base is **FLUX.2-klein-4B**
([Apache-2.0](https://huggingface.co/black-forest-labs/FLUX.2-klein-base-4B), ungated, no
token); ai-toolkit fetches it (and its Qwen3-4B text encoder) automatically.
**FLUX.2-dev** ([license](https://huggingface.co/black-forest-labs/FLUX.2-dev)) is an
opt-in (`spark config set train_base_model black-forest-labs/FLUX.2-dev` + `train_arch
flux2`): it's gated and needs an `HF_TOKEN` on the box. Review each model's license for
your own use.

Set a GB10/sm_121-compatible ai-toolkit image with `spark config set aitoolkit_image
<image@sha256:…>`; spark pulls it on first run (build-your-own reference:
[templates/train/Dockerfile.reference](templates/train/Dockerfile.reference)).

📖 **[docs/training.md](docs/training.md)** — corpus prep, captioning, the
resume-in-chunks workflow, tuning (`--steps`/`--rank`), and troubleshooting.

## Audio transcription (Whisper)

whisper-server (whisper.cpp) — same ggml/CUDA backend as llama.cpp. Serves an OpenAI-compatible transcription endpoint.

```bash
spark transcribe start                      # default: large-v3, port 8081
spark transcribe start --model medium       # lighter model
spark transcribe stop
spark transcribe status
spark transcribe logs
```

Endpoint: `http://gx10-<id>.local:8081/v1/audio/transcriptions`

Point any OpenAI-compatible client at it (e.g. mdkb: `audio_provider=remote`, `audio_api_base=http://gx10-<id>.local:8081`).

> whisper.cpp has no native OpenAI route — `spark transcribe start` launches it with `--inference-path /v1/audio/transcriptions` so its inference handler answers there. Only that path is implemented (no `/v1/models`), and the default `json` response is OpenAI-shaped; other formats follow whisper.cpp's schema.

## Speech synthesis (TTS)

Qwen3-TTS, run in a `qwen-tts` Python venv on the DGX — entirely on-box, no cloud,
no host install. `spark tts say` syncs the bundled generator, synthesizes on the
box, and copies the `.wav` back to your workstation.

```bash
spark tts pull-models                       # one-time: fetch the model into models_dir
spark tts say "There's no place like my volcano." --out bowser.wav \
  --speaker Ryan --instruct "deep gruff gravelly menacing monster-king growl"
```

`--speaker` picks a built-in voice and `--instruct` steers tone/emotion in plain
language; `--language` sets the text language (default English). The model comes
from the catalog's `tts` section — pull it once before the first `say`.

## Downloading models

Uses [`bin/hf_download.py`](bin/hf_download.py) (ships in this repo; deployed to the
DGX under `remote_bin`) — a small urllib downloader: no `hf` CLI required, no
token needed for public models, resume-safe with completeness verification.

```bash
# Single model:  spark download <hf-repo> <local-name> "<file-glob>"
spark download <hf-repo> <local-name> "*Q4_K_XL*"

# Multiple models queued sequentially — HuggingFace rate-limits parallel downloads
spark queue \
  <hf-repo-1> <name-1> "<glob-1>" \
  <hf-repo-2> <name-2> "<glob-2>"

# Watch progress
spark logs-dl
```

**Public vs gated.** Public repos (everything in spark's catalogs) need **no token** —
that's the default path above. For a **gated or private** repo, place a HuggingFace
token **on the DGX** (where the downloader runs): `export HF_TOKEN=…` in its
environment, or `hf auth login` (writes `~/.cache/huggingface/token`), after
requesting access on HuggingFace. The downloader picks it up automatically — there's
no `--token` flag, so secrets stay out of argv and the logs.

For the concrete model names required by each `spark comfy` workflow (FLUX.2, LTX-2.3
i2v, etc.) and their HuggingFace sources, see the **Models** table in
[docs/media-workflows.md](docs/media-workflows.md#models).

## Engines (pinned builds)

The inference engines spark runs on the DGX (llama.cpp; whisper.cpp later) are
pinned in `templates/engines.json` to a **commit + build provenance** — the cmake
flags are part of the pin, because a build-config change (e.g. `BUILD_SHARED_LIBS`)
can break things just as much as a code change. This is the build-from-source analog
of the digest-pin used for the ComfyUI image.

```bash
spark engine status                 # installed commit vs pin, per engine (drift check)
spark engine build llama            # rebuild from the pin, then validate it launches
spark engine build llama --latest   # deliberately move to upstream HEAD, then re-pin
```

`status` catches an out-of-band rebuild (someone ran `git pull` + rebuilt) before it
surprises you at serve time — `spark llm serve` also prints a one-line warning if the
engine has drifted. `build` is reproducible (same pin + flags → same binary) and
re-records the pin only when you deliberately move it with `--ref`/`--latest`. It
self-heals a stale CMake cache (e.g. after the source is relocated under `/opt/spark`).

**Updating the pin is how you unlock new models.** The pin is a floor, not a ceiling:
a brand-new model architecture only loads once llama.cpp has merged support for it, so
running the latest models often means moving the pin **forward**. For example, Gemma 4
(released 2026-06-05) needs a llama.cpp from that date or later — an older engine passes
the memory fit-check but then fails to load the model as an unknown architecture. When a
new model won't serve on the current build, `spark engine build llama --latest` rebuilds
from upstream HEAD and re-pins; afterwards, re-check throughput with `spark llm bench`,
since a new build can shift tok/s and your saved reports were measured against the old pin.

## Troubleshooting

The spark CLI prints the exact remedy at the point of failure; this table is the
same set, kept in one scannable place. Commands run **on the DGX** unless noted
(`spark` itself runs on your workstation and reaches the DGX over SSH).

| Problem | Likely cause | Fix (exact command) |
|---------|--------------|---------------------|
| `permission denied` on `/var/run/docker.sock` (comfy/status) | Your user is not in the `docker` group | **Durable:** `sudo usermod -aG docker user` then log out/in. **Stopgap (ephemeral, reverts on restart):** `sudo chmod 666 /var/run/docker.sock` |
| `Cannot connect to the Docker daemon` / `spark status` shows "Docker daemon down" | Daemon not running — often a failed `docker-ce` upgrade leaving buildkit `invalid database` | `sudo rm -rf /var/lib/docker/buildkit && sudo systemctl daemon-reload && sudo systemctl restart docker` (check first: `sudo systemctl status docker`) |
| `spark comfy start` says "Still starting", UI never loads | Container up but ComfyUI still initialising, or a model/runtime error | `spark comfy logs` — watch for the real error. Confirm the daemon is healthy with `spark comfy status` |
| `spark llm serve` refuses: "needs ~XG, but only YG is free" | Model won't fit in unified memory alongside what's already loaded | `spark llm list` to see residents, then `spark llm unload --port N` (or `spark llm stop`) to free room |
| `spark llm serve` refuses: "Port N is in use" | Another model already bound that port | Pick a free one with `--port N`, or `spark llm unload --port N` first |
| Model OOMs / errors mid-load (in `spark llm logs`) | Quant or context window too large for free memory | Free memory (`spark llm list` → `unload`), or load a smaller quant, or lower `--ctx` (default 8192) |
| New model fails to load — "unknown architecture" in `spark llm logs` (fit-check passed) | The pinned llama.cpp predates that model | `spark engine build llama --latest` to update + re-pin (see [Engines](#engines-pinned-builds)), then re-`bench` |
| Build/download fails: "No space left on device" | DGX disk full | `spark disk` to see the biggest consumers; `spark disk --prune` (Docker), or `spark llm rm <model>` to drop one you don't serve |
| `spark engine build` fails: "CMakeCache.txt directory is different" or "No CMAKE_CUDA_COMPILER" | Stale build cache after a source move, or `nvcc` off PATH | Current `build` self-heals the cache and puts `/usr/local/cuda/bin` on PATH; just re-run `spark engine build llama` |
| Whisper client gets **404** on `/v1/audio/transcriptions` | whisper.cpp has no native OpenAI route | Fixed in current `spark transcribe start` (launches with `--inference-path /v1/audio/transcriptions`). Only that path exists — there is no `/v1/models`. Restart: `spark transcribe stop && spark transcribe start` |
| `spark status` → `SSH unreachable`; `gx10-*.local` won't resolve | mDNS/avahi not resolving the `.local` host, or wrong host/key | Test `ssh user@gx10-<id>.local`. Ensure `avahi-daemon` is running on the DGX, or set `dgx_host` to its IP in `~/.config/spark.json` |
| `no config file — using defaults` | `~/.config/spark.json` not created yet | `spark init` |
| Download queue stalls or errors | HuggingFace rate-limits parallel downloads; `spark queue` runs them sequentially for this reason | `spark logs-dl` to see the failure. Re-running is resume-safe; avoid launching parallel downloads |

## Architecture & design decisions

`spark` runs on the **operator's workstation** and reaches the DGX entirely over SSH. There is no daemon, no background service, and no persistent process on the workstation side.

Key design choices — each is intentional, not accidental:

- **Manifest-driven, single source of truth.** Every command is defined ONCE in a manifest under `commands/<domain>/<verb>.md` — a fenced ` ```spec ` JSON block (name, typed `params`, `handler` ref) plus a markdown help body. That one definition drives CLI routing, the three-level `--help` hierarchy, and MCP-ready tool schemas (`spark _schema`). `bin/spark` is a thin entry point: it loads manifests, routes argv, parses it against the matched spec (`lib/cliparse.py`), and calls the handler (`lib/handlers/<domain>.py`). argv is interpreted in exactly one place, and the `handler(params, cfg)` contract is callable identically from the CLI and the MCP server ([`bin/spark-mcp`](bin/spark-mcp)). Commands and playbooks share the same file shape and parser.
- **SSH-based remote management.** Every command shells out to `ssh user@host '...'`. No agent runs on the DGX; the DGX is managed like a remote host, not a peer.
- **One `llama-server` process per model.** Each loaded model gets its own port. This makes unloading precise (kill one process, free exactly that model's VRAM), avoids a multiplexing router, and keeps port numbers as stable identifiers (`--port 30000` always means that one model).
- **`screen` sessions for process persistence.** `llama-server` and `whisper-server` run in detached `screen` sessions so they outlive the SSH connection that started them.
- **Zero Python dependencies.** `bin/spark`, the `lib/` package (manifest loader, parser, handlers), `bin/hf_download.py`, and `tools/flatten_comfy_workflow.py` all use stdlib only. The few capabilities that need more are **opt-in, never forced**: the `image` verbs need Pillow, and `spark llm probe` shells out to the external [`llm-probe`](https://pypi.org/project/llm-probe/) tool (`pipx install llm-probe`) — each gated behind a clear hint if absent, so users only install what they actually use. The CLI runs on the operator's workstation, which can't assume a venv; shipping no deps means `pip install nothing` — clone and run.
- **All service paths are configurable.** Every binary, log, and model directory is a config key in `~/.config/spark.json`. Relocating the whole stack (e.g. to `/opt/spark` under a `svc-spark` service account) is a config edit, not a code change.

## Contributing

Issues and pull requests welcome. See **[CONTRIBUTING.md](CONTRIBUTING.md)** for the
project layout, how to add a command (manifest + handler), the stdlib-only and
docs conventions, and the local checks CI runs (all without a DGX).

## Config

Config file: `~/.config/spark.json` (create with `spark init`, or copy
[`templates/spark.json.example`](templates/spark.json.example) and edit).

Run **`spark config`** (alias `spark config show`) to see every setting, its current
value, its env-var override, and a one-line description, and **`spark config set
<key> <value>`** to change one (validated, preserves the rest of the file). There's
no hand-maintained list here on purpose — the defaults, env vars, `spark init`
prompts, the example file, and `spark config` all **derive from one schema** (`_CONFIG`
in `lib/sparkcore.py`), so they can't drift. Precedence per key: **env var >
`~/.config/spark.json` > default**.

Every service path is configurable, so the whole stack can be relocated (e.g.
consolidated under a single service-account-owned `/opt/spark` tree) by editing
`~/.config/spark.json` — no code changes. Set the keys to the new locations,
e.g. `spark config set models_dir /opt/spark/models`. The bundled on-box scripts
(`hf_download.py`, `tts_gen.py`) live under one `remote_bin` dir — set it once
(`spark config set remote_bin /opt/spark/bin`) and each script's path derives from it.

Run `spark models` to list what's downloaded on your DGX, with quant and size.
