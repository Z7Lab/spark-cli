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
spark init                                  First-time setup — create ~/.config/spark.json
spark status                                Show all services (LLM, ComfyUI, Whisper, disk)

# LLM serving
spark models                                List downloaded models with quant and size
spark serve <model> [--quant Q] [--port N] [--ctx N] [--parallel N]
                                            Load a model (always passes --jinja --tools all)
spark stop                                  Unload the running model (free GPU memory)
spark logs [--lines N]                      Tail the LLM server log
spark open                                  Open built-in chat UI in browser

# Image / video generation
spark comfy <start|stop|status|logs>        Manage AEON-Spark ComfyUI (port 8188)

# Audio transcription
spark transcribe <start|stop|status|logs>   Manage whisper-server (port 8081)

# Model downloads
spark download <repo> <name> <pattern>      Download a single model from HuggingFace
spark queue <repo> <name> <pattern> [...]   Queue multiple downloads sequentially (background)
spark logs-dl                               Tail the download queue log
```

## LLM serving

```bash
# List what's available
spark models

# Load by name — prompts if multiple quants
spark serve model-b

# Load specific quant directly (no prompt)
spark serve model-b --quant UD-Q3_K_XL

# Switch models — automatically stops current server first
spark serve model-d

# Stop / unload
spark stop
```

Chat UI (built-in, no install): `http://gx10-<id>.local:30000`

## Image and video generation (ComfyUI)

AEON-Spark — pre-built Docker image for GB10 (handles sm_121 / UMA / Blackwell correctly).

```bash
spark comfy start     # pulls and starts via docker compose
spark comfy status    # shows UI URL when ready
spark comfy stop
spark comfy logs
```

UI at: `http://gx10-<id>.local:8188`

> Docker requires `sudo chmod 666 /var/run/docker.sock` on the DGX if permission denied.

## Audio transcription (Whisper)

whisper-server (whisper.cpp) — same ggml/CUDA backend as llama.cpp. OpenAI-compatible endpoint.

```bash
spark transcribe start                      # default: large-v3, port 8081
spark transcribe start --model medium       # lighter model
spark transcribe stop
spark transcribe status
spark transcribe logs
```

Endpoint: `http://gx10-<id>.local:8081/v1/audio/transcriptions`

Point any OpenAI-compatible client at it (e.g. mdkb: `audio_provider=remote`, `audio_api_base=http://gx10-<id>.local:8081`).

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

## Config

Config file: `~/.config/spark.json` (create with `spark init`)

| Key | Default | Env var override |
|-----|---------|-----------------|
| `dgx_host` | `gx10-<id>.local` | `DGX_HOST` |
| `dgx_user` | `user` | `DGX_USER` |
| `models_dir` | `~/models` | `SPARK_MODELS_DIR` |
| `server_bin` | `~/llama.cpp/build/bin/llama-server` | `SPARK_SERVER_BIN` |
| `port` | `30000` | `SPARK_PORT` |

## Models on this GB10

| Model | Quant | Size | Best for |
|-------|-------|------|----------|
| model-b | UD-Q3_K_XL | 101 GB | Best quality that fits (recommended) |
| model-b | UD-Q2_K_XL | 86 GB | Fallback if Q3 OOMs |
| model-d | UD-Q4_K_M | 84 GB | General chat, orchestration, 1M context |
| model-c | UD-Q5_K_M | 39 GB | Coding, long context |
| model-a | UD-Q4_K_XL | 17 GB | Fast tool-use, run alongside other models |
