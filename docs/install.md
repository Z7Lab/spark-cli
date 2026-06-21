# Installing spark on a fresh DGX Spark

This gets a new GB10 (DGX Spark) from nothing to a working `spark` setup. It's the
basic functional install; for the hardened, service-account / rootless-Docker /
`/opt/spark` layout see **[secure-deployment.md](secure-deployment.md)**.

Paths below use the **home-directory defaults** from
[`templates/spark.json.example`](../templates/spark.json.example). Every path is a
config key, so you can relocate the whole stack later (`spark config set …`) — the
hardened runbook puts it all under `/opt/spark`.

There are two sides: your **workstation** (runs the `spark` CLI) and the **DGX**
(runs the engines, reached over SSH). `spark` itself installs nothing on the box —
it drives whatever you build here.

---

## 1. Workstation (the CLI)

stdlib Python 3 only — no pip install, no venv.

```bash
git clone <this-repo> ~/dev/projects/spark
echo 'export PATH="$HOME/dev/projects/spark/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
spark init          # writes ~/.config/spark.json (host, user, paths)
```

Ensure key-based SSH to the DGX works (`ssh <user>@gx10-<id>.local`) before going on —
every `spark` command reaches the box over SSH.

## 2. DGX — inference engines (built from source, pinned)

The engines are pinned to a commit **+ build flags** in
[`templates/engines.json`](../templates/engines.example.json). The GB10-specific bits
are `CMAKE_CUDA_ARCHITECTURES=121` (sm_121) and `BUILD_SHARED_LIBS=ON` (ggml's dynamic
backends — which is why the server needs `LD_LIBRARY_PATH`; `spark` sets it for you).

**llama.cpp** (for `spark llm`) — clone, then let spark build it to the pin:

```bash
# on the DGX:
git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp
# from your workstation — builds the pinned commit with the recorded flags, then validates:
spark engine build llama
spark engine status            # should report ● in sync
```

…or build it by hand with the same recipe:

```bash
cd ~/llama.cpp && git checkout <pinned-commit>   # see engines.json
cmake -S . -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=121 \
      -DBUILD_SHARED_LIBS=ON -DGGML_NATIVE=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
```

**whisper.cpp** (for `spark transcribe`) — same toolchain, tag `v1.8.5`:

```bash
# on the DGX:
git clone https://github.com/ggml-org/whisper.cpp ~/whisper.cpp
cd ~/whisper.cpp && git checkout v1.8.5
cmake -S . -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=121 -DBUILD_SHARED_LIBS=ON
cmake --build build -j$(nproc)
```

## 3. DGX — TTS venv (for `spark tts`)

Qwen3-TTS is a Python-package model, so it runs in its own venv (the generator is
deployed and run with this venv's interpreter):

```bash
# on the DGX:
python3 -m venv ~/venvs/qwen-tts
~/venvs/qwen-tts/bin/pip install qwen-tts torch torchaudio transformers soundfile accelerate
```

Validated set: Python 3.12, `qwen-tts` 0.1.1, `torch` 2.12, `transformers` 4.57.

## 4. DGX — ComfyUI (for `spark comfy`)

ComfyUI runs as the prebuilt **AEON-Spark** container (handles sm_121/UMA/Blackwell),
digest-pinned. Put its compose dir at `comfy_dir` and start it — needs Docker on the
box (rootless + CDI for the hardened path: see secure-deployment):

```bash
spark comfy start      # pulls the digest-pinned image and starts it
spark comfy status     # prints the UI URL when ready
```

## 4b. DGX — ai-toolkit image (optional, for `spark train`)

Style-LoRA training runs in a dedicated, **operator-provided** ai-toolkit container —
spark drives it like the ComfyUI image (pulls it, never builds it). Point spark at a
GB10/sm_121-ready image:

```bash
spark config set aitoolkit_image <image>   # build one from templates/train/Dockerfile.reference
```

`spark train start` pulls it on first run. Full guide (corpus prep, base-model choice +
licensing, resume-in-chunks): [training.md](training.md).

## 5. Models

Pull what each service needs from the catalog ([`templates/models.json`](../templates/models.example.json)):

```bash
spark llm pull-models <name>           # an LLM (run with no args to list the catalog + sizes)
spark transcribe pull-models           # whisper large-v3
spark tts pull-models                  # Qwen3-TTS
spark comfy pull-models                # FLUX.2 + LTX-2.3 image/video models
```

## 6. Verify

```bash
spark status                           # all services + free memory
spark engine status                    # engines in sync with their pins
spark llm serve <model> && spark llm bench <model>
```

For speed expectations per model, see **[benchmarks.md](benchmarks.md)**.

---

**Next:** harden it (dedicated service account, rootless Docker + CDI, `/opt/spark`
layout, supply-chain pinning) → **[secure-deployment.md](secure-deployment.md)**.
