# playbook check

```spec
{
  "name": "playbook.check",
  "domain": "playbook",
  "subcommand": "check",
  "summary": "Lint a playbook's spec, steps, and pages",
  "handler": "playbook.check",
  "params": [
    {"name": "name", "positional": true, "required": true, "help": "Playbook name (see spark playbook list)"}
  ]
}
```

Lints a playbook: spec required fields, step `next` targets, a `## <id>` page per
step, and template placeholders against known inputs + config keys. Structured
command references (`{command, params}`) are validated against the command's
manifest — the command must exist, every param key must be declared, and required
params must be supplied — so a playbook can't silently rot when a command's flags
change.
