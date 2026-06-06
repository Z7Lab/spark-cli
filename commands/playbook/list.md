# playbook list

```spec
{
  "name": "playbook.list",
  "domain": "playbook",
  "subcommand": "list",
  "summary": "List all playbooks with descriptions",
  "handler": "playbook.list",
  "params": []
}
```

Lists every playbook — shipped (repo `playbooks/`) and personal
(`~/.config/spark/playbooks/`, which override shipped ones by name) — with each
one's description.
