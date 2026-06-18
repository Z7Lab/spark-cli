# Contributing to spark

Thanks for your interest in improving spark. This guide covers how the project is
laid out, the conventions that keep it consistent, and how to get a change merged.

spark is a CLI that runs on your **workstation** and manages a remote DGX Spark
(GB10) entirely over SSH. There is no daemon and no install step — `bin/spark` is
a thin entry point on your `PATH`.

## Development setup

```bash
git clone <your-fork-url> spark
export PATH="$PWD/spark/bin:$PATH"   # add to your shell profile
spark --help
```

- **Python 3 (stdlib only).** No `pip install`, no virtualenv — the CLI must run
  on a bare workstation. Don't add third-party imports to the core (see
  [Conventions](#conventions)).
- **A DGX is only needed for the remote commands.** Anything that serves models,
  generates media, or transcribes needs a reachable DGX (`spark init` to
  configure it). But the CLI surface itself — argument parsing, `--help`, the MCP
  schemas, manifests, playbooks — is pure-local and can be developed and tested
  without one. The CI checks below run with no DGX.

## Project layout

| Path | What it is |
|------|------------|
| `bin/spark` | Thin entry point: loads manifests, routes argv, calls a handler |
| `lib/cliparse.py` | Turns an argv tail into a typed `params` dict |
| `lib/manifest.py` | Discovers command manifests, builds routing + MCP schemas |
| `lib/sparkcore.py` | Shared helpers (SSH, config, formatting, fit-checks) |
| `lib/handlers/<domain>.py` | The actual work for each command domain |
| `commands/<domain>/<verb>.md` | One manifest per command (spec + help body) |
| `playbooks/` | Self-describing, agent-walkable task flows |
| `templates/*.example.json` | Committed seeds for the runtime catalogs |
| `docs/` | Setup, benchmarks, deployment guides |

## Adding or changing a command

Every command is defined **once**. A manifest drives CLI routing, the three-level
`--help` hierarchy, and the MCP tool schema — there is no second definition.

1. **Manifest** — add `commands/<domain>/<verb>.md` with a fenced ` ```spec `
   JSON block (`name`, `domain`, optional `subcommand`, `summary`, `handler`,
   typed `params`) followed by a markdown help body with examples.
2. **Handler** — implement `def <verb>(params, cfg)` in
   `lib/handlers/<domain>.py`. Render operator-facing output **and** return a
   structured `{"action": ..., ...}` dict (the MCP server consumes the dict).
3. **Register** — add the handler to that module's `HANDLERS` map
   (`"<domain>.<verb>": <verb>`).

That's it — routing, help, and the `spark _schema` tool schema all follow from
the manifest.

## Conventions

- **stdlib-only core.** Capabilities that need more must be **opt-in**: a missing
  third-party library or external tool is detected and the command prints a clear
  install hint instead of crashing (see the `image` verbs / Pillow and `llm probe`
  / llm-probe). Lazy-import such deps inside the handler, never at module top.
- **Update the docs your change touches.** The pre-commit hook gates this (it asks
  you to confirm via `DOCS_AUDITED=1`); actually open the affected docs — the
  command's manifest, the README command list, and any related guide or playbook.
- **Catalogs are seeded, not overwritten.** Edit `templates/*.example.json` (the
  committed seed). The runtime `templates/*.json` is gitignored — it's the
  operator's local copy.
- **No silent eviction / destructive surprises.** Destructive actions (deleting a
  model, freeing memory) confirm or refuse rather than acting implicitly; follow
  the existing pattern (`spark llm rm` requires a typed-name confirm).

## Before you open a PR

Run the same checks CI runs — all local, no DGX:

```bash
python -m compileall -q lib bin                 # byte-compile
# help + schema render for every command:
python - <<'PY'
import sys, subprocess; sys.path.insert(0, 'lib'); import manifest
bad = 0
for e in manifest.discover():
    s = e["spec"]; argv = ["./bin/spark", s["domain"]] + ([s["subcommand"]] if s.get("subcommand") else []) + ["--help"]
    if subprocess.run(argv, capture_output=True).returncode: print("HELP FAIL", argv); bad += 1
    if subprocess.run(["./bin/spark", "_schema", manifest.canonical_name(s)], capture_output=True).returncode: print("SCHEMA FAIL", s); bad += 1
print("ok" if not bad else f"{bad} failures"); sys.exit(1 if bad else 0)
PY
spark playbook check <name>                      # if you touched a playbook
```

## Commit messages

- Conventional prefix: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`,
  `perf:`.
- Subject ≤ 72 chars, lowercase after the prefix, no trailing period.
- Body states the durable **WHAT** and **WHY** — not the story of how you found
  the change, not a play-by-play of debugging.

## Pull requests

spark uses the standard fork-and-PR flow: fork the repo, push a branch to your
fork, and open a PR against `main`. Keep PRs focused on one change. CI must pass
before a merge. By contributing you agree your work is licensed under the repo's
[MIT License](LICENSE).
