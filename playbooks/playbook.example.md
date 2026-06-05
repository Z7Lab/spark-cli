# playbook.example

A playbook is a single markdown file with two parts:

1. A fenced ` ```spec ` block of JSON — the machine-truth that `spark playbook` reads.
2. One `## <step-id>` section per step — the agent-facing guidance page.

Copy this file to `~/.config/spark/playbooks/<your-name>.md` (git-ignored, personal)
or add it to the repo's `playbooks/` dir (shipped, general). Personal playbooks
override shipped ones of the same name. Validate with `spark playbook check <name>`.

```spec
{
  "name": "playbook.example",
  "description": "Template showing the playbook format — copy and edit.",
  "requires": [
    { "what": "A playbook-wide hard prerequisite (binary / library / service)", "where": "local", "probe": "echo ok", "ready_if": "ok", "hint": "how to install it" }
  ],
  "inputs": {
    "thing": {
      "type": "string",
      "required": true,
      "description": "An input the agent must collect from the user before running"
    },
    "level": {
      "type": "enum",
      "options": ["low", "high"],
      "default": "low",
      "description": "An input with fixed options and a default"
    }
  },
  "steps": [
    {
      "id": "ensure",
      "title": "A precondition-gated step: check a dependency, offer a remedy",
      "precondition": {
        "where": "remote",
        "probe": "echo {thing}",
        "ready_if": "nonempty"
      },
      "remedy": "spark <command that installs/provides {thing}>",
      "next": "do"
    },
    {
      "id": "do",
      "title": "An action step: spark prints the command, the agent runs it",
      "command": "spark <command> --thing {thing} --level {level}",
      "next": "DONE"
    }
  ]
}
```

Notes:
- `requires` (optional) declares **playbook-wide** hard prerequisites — surfaced by
  `show` and probed at the start of `run` so an agent sees blockers before walking.
  Use it for whole-flow deps (a binary, a library, a service); use per-step
  `precondition` for things a specific step needs. Each entry: `what`, optional
  `where`/`probe`/`ready_if`/`hint`.
- `precondition.where` is `remote` (runs over SSH on the Spark) or `local` (runs on
  the workstation). `ready_if` is `nonempty` or a substring to match in the output.
- Templates may reference `{input}` names **and** config keys (`{whisper_models_dir}`,
  `{models_dir}`, `{comfy_dir}`, …). Inputs override config on name collision.
- `next` is another step id, or `DONE` to end.

## ensure

Explain what this step checks and how the remedy fixes it. `run` evaluates the
precondition; if unmet it prints the remedy and stops so the agent can resolve it,
then re-run the same step.

## do

Explain the action. `run` prints the resolved command for the agent to execute, then
points at the next step.
