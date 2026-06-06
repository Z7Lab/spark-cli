# image-to-video

```spec
{
  "name": "image-to-video",
  "description": "Animate a still image into a short video (LTX-2.3 i2v) via ComfyUI.",
  "requires": [
    { "what": "ComfyUI stack on the Spark (running container + LTX-2.3 models)", "where": "remote", "hint": "spark comfy start; then spark comfy pull-models --set animate" }
  ],
  "inputs": {
    "image": {
      "type": "string",
      "required": true,
      "description": "Path to the still image to animate (on your workstation)"
    },
    "prompt": {
      "type": "string",
      "required": true,
      "description": "Motion prompt — what should move/happen, plus audio cues"
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
      "title": "Ensure the LTX-2.3 animate models are present",
      "precondition": {
        "where": "remote",
        "probe": "find {comfy_dir}/workspace/models/checkpoints -name 'ltx-2.3-22b-dev-fp8*' 2>/dev/null",
        "ready_if": "nonempty"
      },
      "remedy": "spark comfy pull-models --set animate",
      "next": "animate"
    },
    {
      "id": "animate",
      "title": "Run the image-to-video pipeline",
      "command": "spark comfy animate {image} \"{prompt}\"",
      "next": "DONE"
    }
  ]
}
```

## ensure-comfy

ComfyUI must be up before animating. The precondition curls the server on the Spark;
if it's down, the remedy `spark comfy start` pulls and starts the container.

## ensure-models

The LTX-2.3 i2v set (FP8 checkpoint + Gemma encoder + distilled LoRA + upscaler) must
be downloaded. If missing, `spark comfy pull-models --set animate` fetches it.

## animate

Collect the image path and a motion prompt from the user. Motion varies a lot
run-to-run — if they want the best take, suggest sweeping a few seeds
(`spark comfy animate <img> "<prompt>" --seed N`). The MP4 downloads to the
workstation. Done.
