# engine status

```spec
{
  "name": "engine.status",
  "domain": "engine",
  "subcommand": "status",
  "summary": "Show each engine's installed commit vs its pin (drift)",
  "handler": "engine.status",
  "params": [
    {"name": "engine", "positional": true, "help": "One engine (e.g. llama); default: all"}
  ]
}
```

Compares each inference engine's installed source checkout (the git commit under
its configured path) against the pin recorded in `templates/engines.json`, and
reports **in sync** or **drifted**. This is how you catch an out-of-band rebuild
(e.g. someone ran `git pull` + rebuilt) before it surprises you at serve time.

The pin is a commit **plus build provenance** (cmake flags) — see the catalog.
Rebuild back to the pin with `spark engine build <name>`.

    spark engine status
    spark engine status llama
