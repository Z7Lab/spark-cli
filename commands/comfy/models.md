# comfy models

```spec
{
  "name": "comfy.models",
  "domain": "comfy",
  "subcommand": "models",
  "summary": "List downloaded ComfyUI models with sizes, flagging reclaimable orphans",
  "handler": "comfy.models",
  "params": []
}
```

Lists the ComfyUI model files on the DGX (under `{comfy_dir}/workspace/models`),
grouped by subdir and biggest-first, with sizes. Each file is flagged **orphan** when
no `spark comfy` command references it — i.e. it appears in neither the `pull-models`
catalog nor any frozen graph in `templates/`. User-trained LoRAs under `loras/` are
never flagged (they're yours, not catalog-managed).

Use it to see what's safe to reclaim, then delete with
[`spark comfy rm`](rm.md):

    spark comfy models
    spark comfy rm --orphans
