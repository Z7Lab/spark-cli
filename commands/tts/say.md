# tts say

```spec
{
  "name": "tts.say",
  "domain": "tts",
  "subcommand": "say",
  "summary": "Synthesize speech from text on the Spark (Qwen3-TTS)",
  "handler": "tts.say",
  "params": [
    {"name": "text", "positional": true, "required": true, "help": "Text to speak"},
    {"name": "out", "type": "string", "default": "tts.wav", "help": "Local output .wav path"},
    {"name": "speaker", "type": "string", "default": "Ryan", "help": "Built-in voice (e.g. Ryan, Aiden); see the model card for the full list"},
    {"name": "instruct", "type": "string", "default": "", "help": "Natural-language tone/style direction, e.g. \"deep gruff menacing growl\""},
    {"name": "language", "type": "string", "default": "English", "help": "Language of the text (or Auto)"}
  ]
}
```

Generates speech on the DGX with Qwen3-TTS, run in the qwen-tts venv against the
catalog model — entirely on-box (no cloud, no host install). The resulting `.wav`
is copied back to `--out` on your workstation.

    spark tts say "There's no place like my volcano." --out bowser.wav \
      --speaker Ryan --instruct "deep gruff gravelly menacing monster-king growl"

`--speaker` picks a built-in voice and `--instruct` steers tone/emotion in plain
language. The model comes from the catalog's `tts` section; if it's not on the
Spark yet, run `spark tts pull-models` first.
