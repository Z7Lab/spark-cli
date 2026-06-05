# audio-transcribe

```spec
{
  "name": "audio-transcribe",
  "description": "Stand up a Whisper transcription server on the Spark.",
  "inputs": {
    "model": {
      "type": "enum",
      "options": ["large-v3", "medium", "small", "tiny"],
      "default": "large-v3",
      "description": "Whisper model size (large-v3 = best quality, tiny = fastest)"
    },
    "port": {
      "type": "number",
      "default": 8081,
      "description": "Port the whisper-server binds on the Spark"
    }
  },
  "steps": [
    {
      "id": "start",
      "title": "Start the whisper-server (pulls the model first if missing)",
      "precondition": {
        "where": "remote",
        "probe": "find {whisper_models_dir} -name 'ggml-{model}.bin' 2>/dev/null",
        "ready_if": "nonempty"
      },
      "remedy": "spark transcribe pull-models --model {model}",
      "command": "spark transcribe start --model {model} --port {port}",
      "next": "verify"
    },
    {
      "id": "verify",
      "title": "Confirm the endpoint is live",
      "command": "spark transcribe status",
      "next": "DONE"
    }
  ]
}
```

## start

Ask the user which Whisper model they want (default `large-v3`). The precondition
checks the ggml model is already on the Spark; if not, the remedy downloads it with
`spark transcribe pull-models --model <model>` (resume-safe, public). Once present,
run the shown command to launch the server.

## verify

`spark transcribe status` prints the OpenAI-compatible endpoint
(`http://<host>:<port>/v1/audio/transcriptions`). Hand that endpoint to the user's
transcription client. Done.
