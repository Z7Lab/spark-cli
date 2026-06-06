# transcribe start

```spec
{
  "name": "transcribe.start",
  "domain": "transcribe",
  "subcommand": "start",
  "summary": "Start whisper-server (default: large-v3, port 8081)",
  "handler": "transcribe.start",
  "params": [
    {"name": "model", "type": "string", "default": "large-v3", "help": "Whisper model name (large-v3, medium, small, tiny)"},
    {"name": "port",  "type": "int",    "default": 8081,       "help": "Port to bind"}
  ]
}
```

Starts whisper.cpp's whisper-server and relocates its inference handler to the
OpenAI transcription path, so any OpenAI-compatible client can POST audio to
`http://<host>:8081/v1/audio/transcriptions`. Replaces a running instance if one
is up.

Examples:

    spark transcribe start
    spark transcribe start --model medium --port 8082
