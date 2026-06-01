# spark

CLI for managing all services on a DGX Spark (GB10) over SSH.
The canonical interface for anything running on the Spark — agents should use these commands rather than deploying directly to the host.

## Setup

```bash
# Add to PATH (already added to ~/.bashrc)
export PATH="$HOME/dev/projects/spark/bin:$PATH"

# First-time config
spark init
```

## Commands

```
spark init                                        First-time setup — create ~/.config/spark.json
spark status                                      Show all services (LLM, ComfyUI, Whisper, RAM)
spark models                                      List downloaded models with quant and size

# LLM serving (one llama-server per model — several can run at once)
spark llm serve <model> [--quant Q] [--port N] [--ctx N] [--parallel N]
                                                  Load a model (refuses if it won't fit)
spark llm list                                    Show loaded models, ports, footprints
spark llm unload <name|--port N>                  Unload one model, free its memory
spark llm stop                                    Stop ALL LLM servers at once
spark llm logs [--port N] [--lines N]             Tail a server log
spark llm open [--port N]                         Open built-in chat UI in browser

# Image / video generation
spark comfy <start|stop|status|logs>              Manage AEON-Spark ComfyUI (port 8188)

# Audio transcription
spark transcribe <start|stop|status|logs>         Manage whisper-server (port 8081)

# Model downloads
spark download <repo> <name> <pattern>            Download a single model from HuggingFace
spark queue <repo> <name> <pattern> [...]         Queue multiple downloads sequentially
spark logs-dl                                     Tail the download queue log
```

## LLM serving

Each model runs as its own `llama-server` on its own port, so several can be
loaded at once (memory permitting). Models are addressed by **port** (always
unambiguous) or by **name**.

```bash
# List what's downloaded
spark models

# Load by name — prompts if multiple quants. Picks the next free port (from 30000).
spark llm serve model-a

# Load a specific quant directly (no prompt)
spark llm serve model-b --quant UD-Q3_K_XL

# Load a second model alongside — gets the next free port (30001, ...)
spark llm serve model-c

# See what's resident, with ports and footprints
spark llm list
#   :30000  model-a UD-Q4_K_XL   16G  pid 115536
#   :30001  model-c UD-Q5_K_M  25G  pid 123565
```

Loading never evicts another model behind your back. If a model won't fit in
free memory, `serve` **refuses** and lists what's resident so you can choose
what to free — you stay in control of what gets unloaded:

```bash
spark llm serve model-b --quant UD-Q3_K_XL
#   ✗ model-b UD-Q3_K_XL needs ~94G, but only 71G is free.
#     Resident models (unload some to make room):
#       :30000  model-a ...   spark llm unload --port 30000
#       :30001  model-c ... spark llm unload --port 30001

# Unload exactly one — by port (exact) or name (refuses if a name is ambiguous)
spark llm unload --port 30001
spark llm unload model-a

# Stop everything at once
spark llm stop
```

`unload` and `stop` free only LLM servers — whisper and ComfyUI are untouched.

Chat UI (built-in, no install): `http://gx10-<id>.local:30000` (or `spark llm open [--port N]`)

## Image and video generation (ComfyUI)

AEON-Spark — pre-built Docker image for GB10 (handles sm_121 / UMA / Blackwell correctly).

```bash
spark comfy start     # pulls and starts via docker compose
spark comfy status    # shows UI URL when ready
spark comfy stop
spark comfy logs
```

UI at: `http://gx10-<id>.local:8188`

**Generate from the CLI** — `spark comfy generate` submits a FLUX.2 text-to-image
job to the ComfyUI API and downloads the PNG to your workstation:

```bash
spark comfy generate "a red fox in a snowy forest at dawn"
spark comfy generate "neon city street" --width 1280 --height 720 --steps 25 --out city.png
```

Options: `--width --height --steps --guidance --seed --out --model --encoder --vae`.
First run loads the models into the GB10's unified memory (a few minutes); after
that, gens take ~30–60 s.

**Animate a still → video** — `spark comfy animate` runs the LTX-2.3
image-to-video pipeline (upload still → motion → MP4 downloaded locally):

```bash
spark comfy animate fox.png "the fox leaps and runs through the snow"
spark comfy animate portrait.jpg "slow cinematic push-in, hair drifting" --out clip.mp4
```

Options: `--seed --out`. Requires the LTX-2.3 models on the DGX (FP8 checkpoint +
Gemma encoder + distilled LoRA + upscaler). The i2v run takes a few minutes (22B
model, two-stage sample + upscale + decode).

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

## Downloading models

Uses `~/hf_download.py` (canonical: `utility_scripts/hf-download/hf-download.py`) — no `hf` CLI required, no token needed for public models, resume-safe.

```bash
# Single model
spark download unsloth/model-a-GGUF model-a "*UD-Q4_K_XL*"

# Multiple models queued sequentially — HuggingFace rate-limits parallel downloads
spark queue \
  unsloth/model-a-GGUF model-a "*UD-Q4_K_XL*" \
  unsloth/model-c-GGUF model-c "*Q5_K_M*" \
  unsloth/model-b-GGUF model-b "*UD-Q3_K_XL*"

# Watch progress
spark logs-dl
```

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
| Whisper client gets **404** on `/v1/audio/transcriptions` | whisper.cpp has no native OpenAI route | Fixed in current `spark transcribe start` (launches with `--inference-path /v1/audio/transcriptions`). Only that path exists — there is no `/v1/models`. Restart: `spark transcribe stop && spark transcribe start` |
| `spark status` → `SSH unreachable`; `gx10-*.local` won't resolve | mDNS/avahi not resolving the `.local` host, or wrong host/key | Test `ssh user@gx10-<id>.local`. Ensure `avahi-daemon` is running on the DGX, or set `dgx_host` to its IP in `~/.config/spark.json` |
| `no config file — using defaults` | `~/.config/spark.json` not created yet | `spark init` |
| Download queue stalls or errors | HuggingFace rate-limits parallel downloads; `spark queue` runs them sequentially for this reason | `spark logs-dl` to see the failure. Re-running is resume-safe; avoid launching parallel downloads |

## Config

Config file: `~/.config/spark.json` (create with `spark init`)

| Key | Default | Env var override |
|-----|---------|-----------------|
| `dgx_host` | `gx10-<id>.local` | `DGX_HOST` |
| `dgx_user` | `user` | `DGX_USER` |
| `models_dir` | `~/models` | `SPARK_MODELS_DIR` |
| `server_bin` | `~/llama.cpp/build/bin/llama-server` | `SPARK_SERVER_BIN` |
| `server_log` | `~/llama-server.log` | `SPARK_SERVER_LOG` |
| `port` | `30000` | `SPARK_PORT` |
| `comfy_dir` | `~/comfyui-aeon-spark` | `SPARK_COMFY_DIR` |
| `whisper_bin` | `~/whisper.cpp/build/bin/whisper-server` | `SPARK_WHISPER_BIN` |
| `whisper_log` | `~/whisper-server.log` | `SPARK_WHISPER_LOG` |
| `whisper_models_dir` | `~/whisper.cpp/models` | `SPARK_WHISPER_MODELS_DIR` |
| `download_log` | `~/models/download.log` | `SPARK_DOWNLOAD_LOG` |

Every service path is configurable, so the whole stack can be relocated (e.g.
consolidated under a single service-account-owned `/opt/spark` tree) by editing
`~/.config/spark.json` — no code changes. Set the keys to the new locations,
e.g. `"comfy_dir": "/opt/spark/comfyui"`, `"models_dir": "/opt/spark/models"`.

## Models on this GB10

| Model | Quant | Size | Best for |
|-------|-------|------|----------|
| model-b | UD-Q3_K_XL | 101 GB | Best quality that fits (recommended) |
| model-b | UD-Q2_K_XL | 86 GB | Fallback if Q3 OOMs |
| model-d | UD-Q4_K_M | 84 GB | General chat, orchestration, 1M context |
| model-c | UD-Q5_K_M | 39 GB | Coding, long context |
| model-a | UD-Q4_K_XL | 17 GB | Fast tool-use, run alongside other models |
