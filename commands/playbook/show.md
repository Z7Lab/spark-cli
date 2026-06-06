# playbook show

```spec
{
  "name": "playbook.show",
  "domain": "playbook",
  "subcommand": "show",
  "summary": "Show a playbook's inputs and step map",
  "handler": "playbook.show",
  "params": [
    {"name": "name", "positional": true, "required": true, "help": "Playbook name (see spark playbook list)"}
  ]
}
```

Prints the static blueprint of a playbook: its required inputs, whole-flow
requirements, and the ordered step map (with each step's command, structured
refs rendered to their CLI form).
