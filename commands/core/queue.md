# queue

```spec
{
  "name": "queue",
  "domain": "queue",
  "summary": "Queue multiple downloads to run sequentially",
  "handler": "core.queue",
  "params": [
    {"name": "specs", "positional": true, "variadic": true, "required": true,
     "help": "Download specs as repeating groups of 3: <repo> <name> <pattern>"}
  ]
}
```

Takes arguments in groups of three — repo, local name, glob pattern. Each
download starts only after the previous one finishes, in a background screen
session that survives SSH disconnects.

Example:

    spark queue \
      <org>/<model-1>-GGUF <name-1> "<glob-1>" \
      <org>/<model-2>-GGUF <name-2> "<glob-2>"

Monitor with `spark logs-dl`.

Auth works exactly as in `spark download`: public repos need no token; gated/private
repos use a HuggingFace token placed **on the DGX** (`HF_TOKEN` or `hf auth login`),
auto-detected by the downloader. See `spark download --help` for the details.
