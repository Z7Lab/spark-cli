# image-edit

```spec
{
  "name": "image-edit",
  "description": "Edit an image by instruction on the Spark (Qwen-Image-Edit) — replace or change parts of it.",
  "requires": [
    { "what": "ComfyUI stack on the Spark (running container + Qwen-Image-Edit models)", "where": "remote", "hint": "spark comfy start; then spark comfy pull-models --set edit" }
  ],
  "inputs": {
    "image": {
      "type": "string",
      "required": true,
      "description": "Path to the image to edit (on your workstation)"
    },
    "instruction": {
      "type": "string",
      "required": true,
      "description": "What to change, in plain language (e.g. 'replace the blue sign with a clock')"
    }
  },
  "steps": [
    {
      "id": "ensure-comfy",
      "title": "Ensure ComfyUI is serving on the Spark",
      "precondition": {
        "where": "remote",
        "probe": "curl -sf http://localhost:{comfy_port}/ -o /dev/null && echo up",
        "ready_if": "up"
      },
      "remedy": "spark comfy start",
      "next": "ensure-models"
    },
    {
      "id": "ensure-models",
      "title": "Ensure the Qwen-Image-Edit models are present",
      "precondition": {
        "where": "remote",
        "probe": "find {comfy_dir}/workspace/models/diffusion_models -name 'qwen_image_edit_2509*' 2>/dev/null",
        "ready_if": "nonempty"
      },
      "remedy": "spark comfy pull-models --set edit",
      "next": "edit"
    },
    {
      "id": "edit",
      "title": "Run the instruction edit",
      "command": "spark comfy edit {image} \"{instruction}\"",
      "next": "DONE"
    }
  ]
}
```

## ensure-comfy

ComfyUI must be up before editing. The precondition curls the server on the Spark; if
it's down, the remedy `spark comfy start` pulls and starts the container.

## ensure-models

The Qwen-Image-Edit set (~28 GB: the 2509 DiT + Qwen2.5-VL encoder + VAE) must be
downloaded. If missing, `spark comfy pull-models --set edit` fetches it. Disk tight?
`spark comfy models` shows what's downloaded and flags reclaimable orphans, and
`spark comfy rm --orphans` frees them.

## edit

Collect the image path and a plain-language instruction from the user. `spark comfy
edit` changes the described element while keeping the rest consistent (first run loads
~28 GB, a few min; then ~30–60 s). Pick the right tool for the job:

- **`spark comfy edit`** — change a **described element** anywhere ("replace the sign
  with a clock", "make it night").
- **`spark comfy refine <img> "<prompt>"`** — re-render the **whole** frame through a
  stronger base to fix garbled text / soft detail (img2img @ denoise 0.5).
- **`spark comfy generate --inpaint --region x,y,w,h`** — repaint a fixed **rectangle**.

The edited image downloads to the workstation. Done.
