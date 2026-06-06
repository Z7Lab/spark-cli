# comfy queue

```spec
{
  "name": "comfy.queue",
  "domain": "comfy",
  "subcommand": "queue",
  "summary": "Show ComfyUI's render queue (running + pending prompts)",
  "handler": "comfy.queue",
  "params": []
}
```

Queries ComfyUI's live `/queue` endpoint and lists the prompt IDs that are
currently **running** and **pending**. This is the authoritative answer to
"is something rendering right now?" — unlike `comfy status`, which only reports
whether the container is up and the UI reachable.

Use it to watch a batch (e.g. `spark comfy animate` or a multi-clip job) without
guessing from OS process state. A prompt that disappears from both lists has
finished; its result is then retrievable from ComfyUI's history.

If ComfyUI is down or unreachable the queue cannot be read, and the command says
so and points you at `spark comfy status`.
