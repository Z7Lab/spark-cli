# train sample

```spec
{
  "name": "train.sample",
  "domain": "train",
  "subcommand": "sample",
  "summary": "Render prompts from a trained LoRA (inference, no retrain)",
  "handler": "train.sample",
  "params": [
    {"name": "prompt", "positional": true, "variadic": true, "required": true, "help": "Prompt(s) to render — include the trigger word; repeat for several"},
    {"name": "name",   "type": "string", "help": "Run name (default: the only / most recent run)"},
    {"name": "seed",   "type": "int",    "help": "Seed (default: 42)"},
    {"name": "steps",  "type": "int",    "default": 20,   "help": "Sampling steps"},
    {"name": "width",  "type": "int",    "default": 1024, "help": "Image width"},
    {"name": "height", "type": "int",    "default": 1024, "help": "Image height"},
    {"name": "out",    "type": "string", "help": "Local output dir (default: ./<name>-samples)"}
  ]
}
```

Generates images from a **trained LoRA** without retraining — an alternative to
`spark comfy generate --base flux2-klein-4b --lora <name>` that renders straight from a
run without switching the comfy base (handy right after training). It runs ai-toolkit's
`generate` job in the training container: loads the run's base model + the trained LoRA
(`model.lora_path`, pure inference — the LoRA is not modified), renders your prompts,
and downloads the JPGs to your workstation.

The base model + arch are read from the run's own config, and the newest checkpoint
(the final `<name>.safetensors`) is used. Put the run's **trigger word** in each prompt.

Examples:

    spark train sample "mystylexr a busy harbor at dawn, boats, crates, gulls" --name my-art-style
    spark train sample "mystylexr a dragon over a city" "mystylexr a quiet cafe" --seed 7
