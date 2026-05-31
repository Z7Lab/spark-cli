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
