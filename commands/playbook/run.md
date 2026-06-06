# playbook run

```spec
{
  "name": "playbook.run",
  "domain": "playbook",
  "subcommand": "run",
  "summary": "Walk a playbook one step at a time",
  "handler": "playbook.run",
  "params": [
    {"name": "name", "positional": true, "required": true, "help": "Playbook name (see spark playbook list)"},
    {"name": "opts", "rest": true, "help": "--step <id>, --answers '{json}', and/or --<input> <value> pairs"}
  ]
}
```

Walks the flow one precondition-gated step at a time — spark stays stateless, so
the agent holds position and answers. Pass inputs as `--key val` (or
`--answers '{json}'`), and `--step <id>` to advance to a specific step. Each step
prints its guidance page, checks its precondition (offering a remedy if unmet),
prints the resolved command to run, and points at the next step.

Examples:

    spark playbook run transcribe-audio --model large-v3
    spark playbook run transcribe-audio --step start --model large-v3
