# config show

```spec
{
  "name": "config.show",
  "domain": "config",
  "subcommand": "show",
  "default": true,
  "summary": "Show all settings, their values, env vars, and help",
  "handler": "core.config",
  "params": []
}
```

Prints every config setting from the single schema in `lib/sparkcore.py` (`_CONFIG`):
its current value, the env var that overrides it, and a one-line description. The
defaults, env-var overrides, `spark init`, and `templates/spark.json.example` are all
derived from that one schema — add a setting there and it appears everywhere.

This is the default subcommand, so plain `spark config` runs it. Change one setting
with `spark config set <key> <value>`.

Precedence per key: **env var > `~/.config/spark.json` > default**. Copy
`templates/spark.json.example` to `~/.config/spark.json` (or run `spark init`) and edit.
