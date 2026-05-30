# spark

CLI for managing model serving on a DGX Spark (GB10) over SSH.

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
spark status                                Show server state, model, health, chat URL
spark models                                List downloaded models with quant and size
spark serve <model> [--quant Q] [--port N] [--ctx N] [--parallel N]
                                            Load a model (always passes --jinja --tools all)
spark stop                                  Unload the running model (free GPU memory)
spark logs [--lines N]                      Tail the server log
spark download <repo> <name> <pattern>      Download a single model from HuggingFace
spark queue <repo> <name> <pattern> [...]   Queue multiple downloads sequentially (background)
spark logs-dl                               Tail the download queue log
spark open                                  Open built-in chat UI in browser
```

## Serving models

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

## Downloading models

Uses `~/hf_download.py` (canonical: `utility_scripts/hf-download/hf-download.py`) — no `hf` CLI required, no token needed for public models, resume-safe.

```bash
# Single model
spark download unsloth/model-a-GGUF model-a "*UD-Q4_K_XL*"

# Multiple models queued sequentially (runs in background screen session)
# HuggingFace rate-limits parallel downloads — always queue, never download in parallel
spark queue \
  unsloth/model-a-GGUF model-a "*UD-Q4_K_XL*" \
  unsloth/model-c-GGUF model-c "*Q5_K_M*" \
  unsloth/model-b-GGUF model-b "*UD-Q3_K_XL*"

# Watch progress
spark logs-dl
```

## Chat UI

When llama-server is running, the built-in SvelteKit UI is at:

```
http://gx10-<id>.local:30000
```

No install needed — embedded in the llama-server binary. For persistent history, use Open WebUI (see `knowledge_docs/infrastructure/dgx-spark/dgx-spark-llama-cpp-guide.md`).

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
