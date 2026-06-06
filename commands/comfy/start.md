# comfy start

```spec
{
  "name": "comfy.start",
  "domain": "comfy",
  "subcommand": "start",
  "summary": "Start AEON-Spark ComfyUI (port 8188)",
  "handler": "comfy.start",
  "params": []
}
```

Starts AEON-Spark ComfyUI via `docker compose` on port 8188 and waits for it to
be healthy. Fails fast with the matching remedy if the Docker daemon is down,
unreachable, or permission-denied (rather than polling a dead daemon).

AEON-Spark pre-bundles FLUX 2 Dev, LTX-2.3 22B, and the correct sm_121 / UMA
launch flags for GB10.
