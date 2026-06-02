# Spark media workflows (ComfyUI: images & video)

How to generate images, animate stills, and extend the pipeline on the DGX Spark.
Everything here runs through `spark comfy …`, which talks to the ComfyUI HTTP API
at `http://<dgx-host>:8188` and downloads results to your workstation.

> Prereq: ComfyUI must be up (`spark comfy status`; `spark comfy start` if not) and
> the relevant models present (see [Models](#models)). Host hardening/setup is in
> [secure-deployment.md](secure-deployment.md).

---

## 1. Generate an image (FLUX.2 text-to-image)

```bash
spark comfy generate "a red fox in a snowy forest at dawn"
spark comfy generate "neon city street" --width 1280 --height 720 --steps 25 --out city.png
```
Flat 11-node FLUX.2 graph built inline in `bin/spark`. First run loads the models
into the GB10's unified memory (a few min); then ~30–60 s each.
Options: `--width --height --steps --guidance --seed --out --model --encoder --vae`.

## 2. Animate a still (LTX-2.3 image-to-video)

```bash
spark comfy animate fox.png "the fox leaps and runs through the snow"
spark comfy animate portrait.jpg "slow cinematic push-in, hair drifting" --out clip.mp4
```
Uploads the image to ComfyUI, injects it + the motion prompt + seed into the frozen
graph [`templates/ltx2_i2v_api.json`](../templates/ltx2_i2v_api.json), runs the LTX-2.3 i2v pipeline (two-stage sample
+ spatial upscale + AV decode), and downloads the MP4. A few minutes per clip (22B
model). Options: `--seed --out`. The audio track is steered by the prompt — describe
"epic orchestral music" etc. to push it.

### Seed variations — getting the best take

LTX i2v **motion varies a lot run-to-run** for the same image+prompt (the seed
drives the random noise the video is denoised from). The prompt sets *intent*; the
seed decides *which* motion you actually get — one seed nails the dive, another
walks the wrong way. So the workflow for a good clip is: **fix the prompt, sweep a
few seeds, pick the best.** Without `--seed`, each run is a fresh random seed; pass
`--seed N` to make a take reproducible.

```bash
# sweep 3 seeds into separate files, same prompt:
PROMPT="…turns, walks, dives into the glowing water…"
for s in 1111 2222 3333; do
  spark comfy animate subject.jpg "$PROMPT" --seed "$s" --out "take_s$s.mp4"
done
# review the three, keep the winner. Re-run a winning seed any time to reproduce it,
# or as a base for small prompt tweaks (keep the seed, change a few words).
```

Each take is ~2 min, so a 3-seed sweep is ~6 min. Tips: change *one* thing at a time
(seed **or** a prompt phrase, not both) so you can tell what helped; once a seed
gives good motion, iterate the prompt on that fixed seed to refine details.

## 3. Fly a subject onto another scene (cut-out → composite → animate)

To make a character "land on" a different background (e.g. a character onto a distant
planet), don't use a separate model — **cut the subject out, composite it onto the
target as a start frame, then animate that composite**:

```bash
# 1. cut the subject out (rembg runs inside the comfy container):
ssh svc-spark@<host> 'export DOCKER_HOST=unix:///run/user/$(id -u)/docker.sock
  docker exec comfyui-spark python -c "
from rembg import remove; from PIL import Image
Image.open(\"/workspace/ComfyUI/input/subject.jpg\").convert(\"RGBA\")\
  .pipe(lambda im: remove(im)).save(\"/workspace/ComfyUI/input/subject_cutout.png\")"'

# 2. composite onto the background (PIL, on the workstation):
python3 - <<'PY'
from PIL import Image
bg  = Image.open("background.jpg").convert("RGBA")
sub = Image.open("subject_cutout.png").convert("RGBA")
W,H = bg.size; s = int(H*0.5)
sub = sub.resize((s, s))
bg.alpha_composite(sub, ((W-s)//2, int(H*0.04)))   # centered, near the top
bg.convert("RGB").save("composite_start.png")
PY

# 3. animate the composite:
spark comfy animate composite_start.png "the character lands triumphantly, dust and energy swirl, dramatic camera push-in, epic orchestral music" --out final.mp4
```

`rembg` is preinstalled in the AEON-Spark container; `PIL` (Pillow) on the
workstation. Tune the composite scale/position in step 2 for the look you want.

> An alternative — the bundled `05_ltx2.3_first_last_frame_to_video` workflow
> (interpolate start frame → end frame) — exists but needs the *distilled* LTX
> checkpoint and its motion is interpolative, not "subject flies in". The cut-out
> composite above gives more control and reuses `spark comfy animate`.

---

## How it works (and how to add a new workflow)

ComfyUI's bundled workflows wrap everything in a **subgraph** node, but the
`/prompt` API needs a **flat** graph. The frontend flattens subgraphs in JS at
queue time; there's no server endpoint for it. So we flatten **once**, freeze the
result as a template, and let the CLI patch it at runtime:

```
ComfyUI workflow.json ──(tools/flatten_comfy_workflow.py, once)──▶ templates/<x>.json ──(bin/spark)──▶ /prompt
```

**To add a new ComfyUI workflow as a spark command:**

1. Find the workflow JSON on the DGX (the AEON-Spark bundle ships them):
   `/opt/spark/comfyui/workspace/user/default/workflows/*.json`
2. Flatten it against the running ComfyUI (reads node schemas from `/object_info`):
   ```bash
   ssh svc-spark@<host> 'python3 -' < tools/flatten_comfy_workflow.py \
     ... # or scp the workflow back and run the tool locally against http://<host>:8188
   python3 [tools/flatten_comfy_workflow.py](../tools/flatten_comfy_workflow.py) the_workflow.json --comfy http://<host>:8188 \
     > templates/the_workflow_api.json
   ```
   The tool prints (to stderr) the `LoadImage` node ids and the prompt node id —
   the parameterisable hooks.
3. Add a `_comfy_<verb>` function in [`bin/spark`](../bin/spark) (copy `_comfy_animate`): load the
   template, patch the hooks by `class_type` (`LoadImage` → image, prompt node →
   text, `RandomNoise` → seed), POST `/prompt`, poll `/history`, download via
   `/view`.
4. Wire it into `cmd_comfy` dispatch + `_COMFY_HELP` + the README.

[`tools/flatten_comfy_workflow.py`](../tools/flatten_comfy_workflow.py) documents the non-obvious gotchas it handles
(Reroute passthroughs, the `["COMBO", …]` object_info shape, consuming
widgets_values slots for promoted widgets, the templated `ResizeImageMaskNode`,
and the `-10` subgraph-input origin).

---

## Models

| Command | Models (on the DGX, under `/opt/spark/comfyui/workspace/models/`) |
|---|---|
| `generate` (FLUX.2) | `diffusion_models/flux2_dev_fp8mixed` · `vae/flux2-vae` · `text_encoders/mistral_3_small_flux2_bf16` |
| `animate` (LTX-2.3 i2v) | `checkpoints/ltx-2.3-22b-dev-fp8` · `text_encoders/gemma_3_12B_it_fp4_mixed` · `loras/ltx-2.3-22b-distilled-lora-384` · `latent_upscale_models/ltx-2.3-spatial-upscaler-x2-1.1` |

Pull any model with our resume-safe downloader (verifies completeness):
```bash
ssh svc-spark@<host> 'python3 /opt/spark/bin/hf_download.py <repo_id> <dest_dir> "<glob>"'
```
The AEON-Spark `download_models.py` (located on the DGX, not in this repo) lists the
full model catalog with HuggingFace repo IDs, destination subdirectories under
`/opt/spark/comfyui/workspace/models/`, and the file globs for each model.
