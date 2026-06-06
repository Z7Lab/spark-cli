"""playbook handlers — list, show, run, check self-describing task flows.

A playbook is a fenced ```spec JSON block (typed inputs, steps, preconditions)
plus `## <step-id>` markdown pages. spark stays stateless — `run` evaluates one
step (and its precondition) at a time; the agent holds position and answers.

A step's `command` may be either:
  • a free-text string template — the escape hatch for arbitrary shell, e.g.
    "spark llm serve {model}" or a chained image pipeline; or
  • a structured reference to a manifest command —
    {"command": "llm.serve", "params": {"model": "{model}"}} — which `check`
    validates against the command's manifest (so a playbook can't silently rot
    when a command's flags change) and `run` renders to the same CLI string.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from sparkcore import (
    REPO_ROOT, _DEFAULTS, bold, dim, red, cyan, ok, warn, fail, ssh,
)
from cliparse import flag_token
import manifest

PLAYBOOK_REPO_DIR = REPO_ROOT / "playbooks"
PLAYBOOK_USER_DIR = Path.home() / ".config" / "spark" / "playbooks"


# ── Shared playbook plumbing ─────────────────────────────────────────────────────

def _playbook_sources() -> dict:
    """name -> (path, personal). Personal (user dir) overrides shipped by name."""
    found = {}
    for d, personal in ((PLAYBOOK_REPO_DIR, False), (PLAYBOOK_USER_DIR, True)):
        if d.is_dir():
            for f in sorted(d.glob("*.md")):
                if f.name == "playbook.example.md":
                    continue
                found[f.stem] = (f, personal)
    return found


def _resolve_template(template: str, values: dict) -> str:
    """Fill {placeholders} from values; raise naming the first missing one."""
    out = template
    for key in re.findall(r"\{([a-zA-Z0-9_]+)\}", template):
        if key not in values or values[key] in (None, ""):
            raise KeyError(key)
        out = out.replace("{" + key + "}", str(values[key]))
    return out


def _probe(cfg, pre: dict, values: dict):
    """Run a precondition probe; return (ready: bool, output: str)."""
    cmd = _resolve_template(pre["probe"], values)
    if pre.get("where", "remote") == "local":
        out = subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()
    else:
        out = ssh(cfg, cmd)
    ready_if = pre.get("ready_if", "nonempty")
    ready = bool(out) if ready_if == "nonempty" else (ready_if in out)
    return ready, out


def _merged_inputs(spec: dict, answers: dict) -> dict:
    vals = {k: v.get("default") for k, v in spec.get("inputs", {}).items()}
    vals.update({k: v for k, v in answers.items() if v is not None})
    return vals


def _command_index() -> dict:
    """canonical name (e.g. 'llm.serve') -> manifest entry, for action refs."""
    return {manifest.canonical_name(e["spec"]): e for e in manifest.discover()}


def _render_action(cmd, index: dict, values=None) -> str:
    """Render a step's command to a CLI string.

    String commands are returned as-is (resolved against `values` when given).
    Structured {command, params} refs are rendered from the command's manifest;
    when `values` is None the {placeholders} are left intact (for `show`).
    """
    if isinstance(cmd, str):
        return _resolve_template(cmd, values) if values is not None else cmd
    ref = cmd.get("command")
    entry = index.get(ref)
    if entry is None:
        return f"spark <unknown command '{ref}'>"
    spec = entry["spec"]
    parts = ["spark", spec["domain"]]
    if spec.get("subcommand"):
        parts.append(spec["subcommand"])
    ap = cmd.get("params") or {}
    params_spec = spec.get("params", [])

    def render_val(raw):
        return _resolve_template(str(raw), values) if values is not None else str(raw)

    for p in params_spec:
        if p.get("positional") and p["name"] in ap:
            parts.append(render_val(ap[p["name"]]))
    for p in params_spec:
        if not p.get("positional") and not p.get("rest") and p["name"] in ap:
            if p.get("type") == "bool":
                rv = render_val(ap[p["name"]])
                if rv.lower() not in ("", "false", "0", "no"):
                    parts.append(flag_token(p["name"]))
            else:
                parts.append(f"{flag_token(p['name'])} {render_val(ap[p['name']])}")
    return " ".join(parts)


def _load(name: str):
    """Resolve a playbook by name → (path, personal, spec, pages, steps, by_id) or exit."""
    src = _playbook_sources()
    if name not in src:
        print(red(f"No playbook '{name}'. Run {cyan('spark playbook list')}.")); sys.exit(1)
    path, personal = src[name]
    spec, pages = manifest.read_spec_and_pages(path)
    steps = spec.get("steps", [])
    by_id = {s["id"]: s for s in steps}
    return path, personal, spec, pages, steps, by_id


# ── Subcommands ──────────────────────────────────────────────────────────────────

def pb_list(params, cfg):
    """List all playbooks (shipped + personal) with descriptions."""
    src = _playbook_sources()
    if not src:
        print(dim("No playbooks found.")
              + f"  Shipped: {PLAYBOOK_REPO_DIR}  Personal: {PLAYBOOK_USER_DIR}")
        return {"action": "playbook.list", "playbooks": []}
    print(bold("Playbooks"))
    col = max(len(n) for n in src) + 2
    listed = []
    for name in sorted(src):
        path, personal = src[name]
        try:
            spec, _ = manifest.read_spec_and_pages(path)
            desc = spec.get("description", "")
        except Exception as e:
            desc = red(f"(unparseable: {e})")
        tag = dim(" [personal]") if personal else ""
        print(f"  {cyan(name):<{col + 9}} {desc}{tag}")
        listed.append({"name": name, "personal": personal})
    print(dim(f"\n  show <name> for inputs · run <name> to walk it"))
    return {"action": "playbook.list", "playbooks": listed}


def pb_show(params, cfg):
    """Show a playbook's static blueprint: required inputs + the step map."""
    name = params["name"]
    path, personal, spec, pages, steps, by_id = _load(name)
    index = _command_index()
    print(bold(spec.get("name", name)) + (dim("  [personal]") if personal else ""))
    print(f"  {spec.get('description','')}\n")
    inputs = spec.get("inputs", {})
    if inputs:
        print(bold("Required inputs:"))
        for k, v in inputs.items():
            opt = f" [{('|').join(v['options'])}]" if v.get("options") else ""
            dfl = f" (default {v['default']})" if "default" in v else ""
            req = red(" required") if v.get("required") else ""
            print(f"  {cyan(k)}  {v.get('type','string')}{opt}{dfl}{req}  {dim(v.get('description',''))}")
        print()
    reqs = spec.get("requires", [])
    if reqs:
        print(bold("Requires:"))
        for r in reqs:
            where = f" ({r['where']})" if r.get("where") else ""
            hint = dim("  " + r["hint"]) if r.get("hint") else ""
            print(f"  {cyan(r['what'])}{dim(where)}{hint}")
        print()
    print(bold("Steps:"))
    for s in steps:
        cmdt = dim("  → " + _render_action(s["command"], index)) if s.get("command") else ""
        print(f"  {cyan(s['id'])}  {s.get('title','')}{cmdt}")
    print(dim(f"\n  Walk it: spark playbook run {name} [--<input> val ...]"))
    return {"action": "playbook.show", "name": name, "personal": personal,
            "inputs": list(inputs.keys()), "steps": [s["id"] for s in steps]}


def pb_check(params, cfg):
    """Lint a playbook's spec, steps, pages, and command references."""
    name = params["name"]
    path, personal, spec, pages, steps, by_id = _load(name)
    index = _command_index()
    problems = []
    if "name" not in spec: problems.append("spec missing 'name'")
    if not steps: problems.append("spec has no steps")
    valid_keys = set(spec.get("inputs", {})) | set(_DEFAULTS)
    for r in spec.get("requires", []):
        if "what" not in r:
            problems.append("a requires entry is missing 'what'")
        for ph in re.findall(r"\{([a-zA-Z0-9_]+)\}", r.get("probe", "")):
            if ph not in valid_keys:
                problems.append(f"requires '{r.get('what','?')}' probe references unknown '{{{ph}}}'")
    ids = set(by_id)
    for s in steps:
        if "id" not in s: problems.append("a step is missing 'id'"); continue
        nxt = s.get("next")
        if nxt and nxt not in ids and nxt != "DONE":
            problems.append(f"step '{s['id']}' -> unknown next '{nxt}'")
        if s["id"] not in pages:
            problems.append(f"step '{s['id']}' has no '## {s['id']}' page")
        # Command: validate a structured ref against its manifest, or a string
        # template's placeholders.
        cmd = s.get("command")
        if isinstance(cmd, dict):
            ref = cmd.get("command")
            entry = index.get(ref)
            if entry is None:
                problems.append(f"step '{s['id']}' references unknown command '{ref}'")
            else:
                declared = {p["name"] for p in entry["spec"].get("params", [])}
                provided = cmd.get("params") or {}
                for k, v in provided.items():
                    if k not in declared:
                        problems.append(f"step '{s['id']}' command '{ref}' has no param '{k}'")
                    for ph in re.findall(r"\{([a-zA-Z0-9_]+)\}", str(v)):
                        if ph not in valid_keys:
                            problems.append(f"step '{s['id']}' command '{ref}' references unknown placeholder '{{{ph}}}'")
                for p in entry["spec"].get("params", []):
                    if p.get("required") and p["name"] not in provided:
                        problems.append(f"step '{s['id']}' command '{ref}' missing required param '{p['name']}'")
            tmpls = ((s.get("precondition") or {}).get("probe", ""), s.get("remedy", ""))
        else:
            tmpls = (cmd or "", (s.get("precondition") or {}).get("probe", ""), s.get("remedy", ""))
        for tmpl in tmpls:
            for ph in re.findall(r"\{([a-zA-Z0-9_]+)\}", tmpl):
                if ph not in valid_keys:
                    problems.append(f"step '{s['id']}' references unknown placeholder '{{{ph}}}'")
    if problems:
        print(fail(f"{name}: {len(problems)} issue(s):"))
        for p in problems: print(f"  - {p}")
        sys.exit(1)
    print(ok(f"{name}: spec, {len(steps)} steps, and pages all valid."))
    return {"action": "playbook.check", "name": name, "steps": len(steps),
            "ok": True}


def pb_run(params, cfg):
    """Walk a playbook one step at a time (precondition-gated)."""
    name = params["name"]
    opts = params["opts"]
    path, personal, spec, pages, steps, by_id = _load(name)
    index = _command_index()

    # parse --step / --answers / --key val
    step_id, answers = None, {}
    i = 0
    while i < len(opts):
        a = opts[i]
        if a == "--step":
            step_id = opts[i + 1]; i += 2; continue
        if a == "--answers":
            answers.update(json.loads(opts[i + 1])); i += 2; continue
        if a.startswith("--"):
            k = a[2:]
            if i + 1 < len(opts) and not opts[i + 1].startswith("--"):
                answers[k] = opts[i + 1]; i += 2
            else:
                answers[k] = "true"; i += 1
            continue
        i += 1
    # config keys are available to probe/command templates ({whisper_models_dir},
    # {models_dir}, {comfy_dir}, …); playbook inputs override on name collision.
    values = {**cfg, **_merged_inputs(spec, answers)}

    if step_id is None:
        # entry: show inputs, then start at the first step
        print(bold(f"Playbook: {spec.get('name', name)}") + dim(f"  ({path.name})"))
        print(f"  {spec.get('description','')}")
        missing = [k for k, v in spec.get("inputs", {}).items()
                   if v.get("required") and not values.get(k)]
        if missing:
            print(warn(f"  Provide required inputs: {', '.join('--' + m for m in missing)}"))
        reqs = spec.get("requires", [])
        if reqs:
            print(bold("  Requirements:"))
            for r in reqs:
                ready = None
                if r.get("probe"):
                    try:
                        ready, _ = _probe(cfg, r, values)
                    except KeyError:
                        ready = None
                if ready:
                    print(f"    {ok(r['what'])}")
                elif ready is False:
                    extra = dim(f"  → {r['hint']}") if r.get("hint") else ""
                    print(f"    {fail(r['what'])}{extra}")
                else:
                    print(f"    {dim('? ' + r['what'])}")
        step_id = steps[0]["id"]

    if step_id not in by_id:
        print(red(f"No step '{step_id}' in {name}.")); sys.exit(1)
    step = by_id[step_id]
    idx = [s["id"] for s in steps].index(step_id)
    print(f"\n{bold(f'Step {idx + 1}/{len(steps)}: {step_id}')} — {step.get('title','')}")
    if step_id in pages:
        print(dim(pages[step_id]))

    pre = step.get("precondition")
    if pre:
        try:
            ready, out = _probe(cfg, pre, values)
        except KeyError as e:
            print(red(f"  precondition needs input {{{e.args[0]}}} — pass --{e.args[0]}")); sys.exit(1)
        if not ready:
            print(fail("  precondition not met"))
            remedy = step.get("remedy")
            if remedy:
                try:
                    remedy = _resolve_template(remedy, values)
                except KeyError:
                    pass
                print(f"  Remedy: {cyan(remedy)}")
            print(dim(f"  Resolve it, then re-run: spark playbook run {name} --step {step_id} "
                      + " ".join(f"--{k} {v}" for k, v in answers.items())))
            sys.exit(2)
        print(ok("  precondition met"))

    resolved = None
    cmd_t = step.get("command")
    if cmd_t:
        try:
            resolved = _render_action(cmd_t, index, values)
        except KeyError as e:
            print(red(f"  step needs input {{{e.args[0]}}} — pass --{e.args[0]}")); sys.exit(1)
        print(f"  {bold('Run:')} {cyan(resolved)}")

    nxt = step.get("next")
    if nxt and nxt != "DONE":
        print(dim(f"  Next: spark playbook run {name} --step {nxt} "
                  + " ".join(f"--{k} {v}" for k, v in answers.items())))
        return {"action": "playbook.run", "name": name, "step": step_id,
                "command": resolved, "next": nxt, "done": False}
    else:
        print(ok("  ✓ playbook complete."))
        return {"action": "playbook.run", "name": name, "step": step_id,
                "command": resolved, "next": None, "done": True}


HANDLERS = {
    "playbook.list":  pb_list,
    "playbook.show":  pb_show,
    "playbook.run":   pb_run,
    "playbook.check": pb_check,
}
