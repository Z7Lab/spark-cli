"""manifest — load command definitions and drive routing, help, and MCP schemas.

A command is a markdown file `commands/<domain>/<verb>.md` containing a fenced
```spec JSON block (the machine truth: name, domain, subcommand, typed params,
handler ref) followed by a markdown body (the human/agent-facing help, which
doubles as the MCP tool description). This is the SAME file shape as a playbook —
a command and a playbook differ only in fields (params+handler vs inputs+steps) —
so they share one parser.

One definition per command feeds three surfaces:
  • CLI routing + argv parsing (via cliparse and the routing table here)
  • the three-level help hierarchy (auto-generated from params + body)
  • MCP-ready JSON-Schema tool definitions (to_json_schema / `spark _schema`)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from cliparse import flag_token

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMANDS_DIR = REPO_ROOT / "commands"

_SPEC_RE = re.compile(r"```(?:spec|json)\s*\n(.*?)\n```", re.S)


# ── Parsing ─────────────────────────────────────────────────────────────────────

def read_spec_and_body(path: Path):
    """Parse a command manifest → (spec dict, markdown body str)."""
    text = path.read_text()
    m = _SPEC_RE.search(text)
    if not m:
        raise ValueError(f"{path.name}: missing ```spec JSON block")
    spec = json.loads(m.group(1))
    body = text[m.end():].strip()
    return spec, body


def read_spec_and_pages(path: Path):
    """Parse a playbook manifest → (spec dict, {step_id: page_text}).

    Same fenced-block truth as a command; the body is split into `## <id>` pages.
    """
    text = path.read_text()
    m = _SPEC_RE.search(text)
    if not m:
        raise ValueError(f"{path.name}: missing ```spec JSON block")
    spec = json.loads(m.group(1))
    pages = {}
    for sm in re.finditer(r"(?ms)^##\s+([^\n]+)\n(.*?)(?=^##\s|\Z)", text[m.end():]):
        pages[sm.group(1).strip()] = sm.group(2).strip()
    return spec, pages


# ── Discovery / routing ─────────────────────────────────────────────────────────

def discover(commands_dir: Path = COMMANDS_DIR) -> list:
    """Load every command manifest under commands/<domain>/<verb>.md."""
    entries = []
    if not commands_dir.is_dir():
        return entries
    for path in sorted(commands_dir.glob("*/*.md")):
        spec, body = read_spec_and_body(path)
        entries.append({"spec": spec, "body": body, "path": path})
    return entries


def build_routing(entries: list) -> dict:
    """domain -> {subcommand_or_None: entry}. Atomic commands key on None."""
    routing: dict = {}
    for e in entries:
        spec = e["spec"]
        routing.setdefault(spec["domain"], {})[spec.get("subcommand")] = e
    return routing


def is_grouped(group: dict) -> bool:
    """True if a domain has named subcommands (vs a single atomic command)."""
    return not (len(group) == 1 and None in group)


def default_subcommand(group: dict):
    """The subcommand to run when a grouped domain is invoked bare (e.g. `spark
    config` → `show`), or None. Declared by `"default": true` in a command spec."""
    for sub, e in group.items():
        if sub is not None and e["spec"].get("default"):
            return sub
    return None


def canonical_name(spec: dict) -> str:
    """Dotted canonical name, e.g. 'llm.serve' or 'status'."""
    return f"{spec['domain']}.{spec['subcommand']}" if spec.get("subcommand") else spec["domain"]


def tool_name(spec: dict) -> str:
    """MCP tool name, e.g. 'llm_serve' or 'status'."""
    return f"{spec['domain']}_{spec['subcommand']}" if spec.get("subcommand") else spec["domain"]


# ── JSON Schema (MCP-ready) ──────────────────────────────────────────────────────

_JSON_TYPE = {"string": "string", "int": "integer", "float": "number", "bool": "boolean"}


def to_json_schema(spec: dict) -> dict:
    """Emit an MCP inputSchema (JSON Schema) for a command's params.

    This is the exact converter an MCP server would reuse to register the command
    as a tool — proving the manifest is the single source for tool schemas too.
    """
    props: dict = {}
    required: list = []
    for p in spec.get("params", []):
        name = p["name"]
        if p.get("rest"):
            props[name] = {"type": "array", "items": {"type": "string"},
                           "description": p.get("help", "")}
            continue
        base = _JSON_TYPE.get(p.get("type", "string"), "string")
        if p.get("variadic"):
            schema = {"type": "array", "items": {"type": base}}
        else:
            schema = {"type": base}
        if p.get("options"):
            schema["enum"] = list(p["options"])
        if p.get("help"):
            schema["description"] = p["help"]
        if "default" in p and p.get("default") is not None:
            schema["default"] = p["default"]
        props[name] = schema
        if p.get("required"):
            required.append(name)
    schema = {
        "name": tool_name(spec),
        "description": spec.get("summary", ""),
        "inputSchema": {"type": "object", "properties": props},
    }
    if required:
        schema["inputSchema"]["required"] = required
    return schema


# ── Help generation ──────────────────────────────────────────────────────────────

def usage_line(spec: dict) -> str:
    parts = ["spark", spec["domain"]]
    if spec.get("subcommand"):
        parts.append(spec["subcommand"])
    for p in spec.get("params", []):
        if p.get("rest"):
            parts.append("[options]")
            continue
        if not p.get("positional"):
            continue
        if p.get("variadic"):
            tok = f"<{p['name']}>..."
        else:
            tok = f"<{p['name']}>"
        if not p.get("required"):
            tok = f"[{tok}]"
        parts.append(tok)
    if any(not p.get("positional") and not p.get("rest") for p in spec.get("params", [])):
        parts.append("[options]")
    return " ".join(parts)


def _param_help_line(p: dict, colors) -> str:
    cyan, dim = colors["cyan"], colors["dim"]
    if p.get("positional") or p.get("rest"):
        label = f"<{p['name']}>"
    else:
        metavar = "" if p.get("type") == "bool" else " " + p["name"].upper()
        label = flag_token(p["name"]) + metavar
    bits = []
    if p.get("help"):
        bits.append(p["help"])
    if p.get("options"):
        bits.append(f"[{'|'.join(map(str, p['options']))}]")
    if "default" in p and p.get("default") not in (None, [], False):
        bits.append(f"(default: {p['default']})")
    if p.get("required"):
        bits.append("(required)")
    return f"  {cyan(f'{label:<16}')} {dim('  '.join(bits)) if bits else ''}".rstrip()


def command_help(entry: dict, colors) -> str:
    """Full help for one command: summary, usage, params tables, then the body."""
    spec, body = entry["spec"], entry["body"]
    bold = colors["bold"]
    lines = [f"{bold('spark ' + spec['domain'] + (' ' + spec['subcommand'] if spec.get('subcommand') else ''))} — {spec.get('summary','')}", ""]
    lines.append(f"Usage: {usage_line(spec)}")
    params = spec.get("params", [])
    pos = [p for p in params if p.get("positional") or p.get("rest")]
    opt = [p for p in params if not p.get("positional") and not p.get("rest")]
    if pos:
        lines += ["", bold("Arguments:")]
        lines += [_param_help_line(p, colors) for p in pos]
    if opt:
        lines += ["", bold("Options:")]
        lines += [_param_help_line(p, colors) for p in opt]
    if body:
        lines += ["", body]
    return "\n".join(lines)


def domain_help(domain: str, group: dict, colors) -> str:
    """Second-level help: list a domain's subcommands with summaries."""
    bold, cyan, dim = colors["bold"], colors["cyan"], colors["dim"]
    entries = {sub: e for sub, e in group.items()}
    lines = [bold(f"spark {domain} — subcommands"), ""]
    width = max((len(s or "") for s in entries), default=0) + 2
    for sub in sorted(s for s in entries if s):
        summ = entries[sub]["spec"].get("summary", "")
        lines.append(f"  {cyan(f'{sub:<{width}}')} {summ}")
    lines.append(dim(f"\n  Run 'spark {domain} <subcommand> --help' for full flag details."))
    return "\n".join(lines)
