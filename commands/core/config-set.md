# config set

```spec
{
  "name": "config.set",
  "domain": "config",
  "subcommand": "set",
  "summary": "Set one config key in ~/.config/spark.json",
  "handler": "core.config_set",
  "params": [
    {"name": "key",   "positional": true, "required": true, "help": "Config key to set (see `spark config show`)"},
    {"name": "value", "positional": true, "required": true, "help": "New value (coerced to the key's type)"}
  ]
}
```

Sets a single key in `~/.config/spark.json`, preserving every other key in the file
(creates the file if absent). The key must exist in the schema (`spark config show`
lists them) and the value is coerced to that key's type. Prints the old → new value.

    spark config set remote_bin /opt/spark/bin
    spark config set port 30100

For interactive first-time setup of the connection settings, use `spark init`; this
is the surgical "change one thing" counterpart.
