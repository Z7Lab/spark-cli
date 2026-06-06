# llm stop

```spec
{
  "name": "llm.stop",
  "domain": "llm",
  "subcommand": "stop",
  "summary": "Stop ALL LLM servers at once",
  "handler": "llm.stop",
  "params": []
}
```

Stops every llama-server / vLLM instance at once and frees their memory. To stop
just one and leave the rest running, use `spark llm unload`. Does not affect
whisper or ComfyUI.
