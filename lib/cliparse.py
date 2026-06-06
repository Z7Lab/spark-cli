"""cliparse — the one place argv is interpreted.

A command manifest declares typed `params`; this module turns a raw argv tail
into a typed `params` dict that handlers consume. The same typed dict is what an
MCP tool wrapper would build from a tool-call arguments object — so argv is
parsed in exactly one place and the `handler(params, cfg)` contract is callable
identically from the CLI and from MCP.

Param spec (one dict per parameter, in `params`):
    name        canonical key in the result dict (snake_case)
    type        "string" | "int" | "float" | "bool"   (default "string")
    positional  true → a positional argument; false/absent → a --flag
    required    true → must be supplied (positionals) / must be present (flags)
    default     value when absent
    options     list of allowed values (enum); an invalid value exits
    variadic    true (last positional only) → collects remaining positionals as a list
    rest        true → captures all remaining tokens raw (flags included), unvalidated
                (the escape hatch for dynamic-flag commands like `playbook run`)

Rules enforced (from the CLI design guide):
  - unknown --flags hard-fail
  - a non-bool flag with no value hard-fails
  - invalid enum values exit
  - CLI flags are kebab-case: param `search_box` ⇄ `--search-box`
"""

from __future__ import annotations


class ParseError(Exception):
    """A malformed invocation. The message is operator-facing."""


def flag_token(name: str) -> str:
    """`--`-prefixed kebab-case CLI token for a param name (search_box → --search-box)."""
    return "--" + name.replace("_", "-")


def _key(token: str) -> str:
    """Map a CLI flag token back to its param key (--search-box → search_box)."""
    return token[2:].replace("-", "_")


def _coerce(p: dict, raw: str):
    """Coerce a raw string to the param's type and validate enum membership."""
    name = p["name"]
    typ = p.get("type", "string")
    if typ == "int":
        try:
            val = int(raw)
        except ValueError:
            raise ParseError(f"--{name.replace('_','-')} expects an integer, got '{raw}'")
    elif typ == "float":
        try:
            val = float(raw)
        except ValueError:
            raise ParseError(f"--{name.replace('_','-')} expects a number, got '{raw}'")
    else:
        val = raw
    opts = p.get("options")
    if opts and val not in opts:
        raise ParseError(f"--{name.replace('_','-')} must be one of {', '.join(map(str, opts))} (got '{raw}')")
    return val


def parse(params_spec: list, argv: list) -> dict:
    """Parse an argv tail against a param spec into a typed dict.

    Raises ParseError on any malformed invocation.
    """
    positionals = [p for p in params_spec if p.get("positional")]
    flags = {p["name"]: p for p in params_spec if not p.get("positional") and not p.get("rest")}
    rest_param = next((p for p in params_spec if p.get("rest")), None)

    # Seed defaults.
    result = {}
    for p in params_spec:
        if p.get("rest"):
            result[p["name"]] = []
        elif p.get("positional") and p.get("variadic"):
            result[p["name"]] = list(p.get("default") or [])
        elif p.get("type") == "bool":
            result[p["name"]] = bool(p.get("default", False))
        else:
            result[p["name"]] = p.get("default")

    # Rest commands: consume the declared leading positionals from the front,
    # then capture everything else raw (no flag validation on the tail).
    if rest_param:
        i = 0
        for p in positionals:
            if i < len(argv) and not argv[i].startswith("--"):
                result[p["name"]] = _coerce(p, argv[i])
                i += 1
            elif p.get("required"):
                raise ParseError(f"missing required argument <{p['name']}>")
        result[rest_param["name"]] = list(argv[i:])
        return result

    collected = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("--"):
            key = _key(tok)
            p = flags.get(key)
            if p is None:
                raise ParseError(f"unknown flag {tok}")
            if p.get("type") == "bool":
                result[key] = True
                i += 1
            else:
                if i + 1 >= len(argv):
                    raise ParseError(f"flag {tok} needs a value")
                result[key] = _coerce(p, argv[i + 1])
                i += 2
        else:
            collected.append(tok)
            i += 1

    # Distribute collected positional tokens; the last positional may be variadic.
    for idx, p in enumerate(positionals):
        is_last = idx == len(positionals) - 1
        if p.get("variadic"):
            taken = collected[idx:]
            if p.get("required") and not taken:
                raise ParseError(f"missing required argument <{p['name']}>")
            result[p["name"]] = [_coerce(p, t) for t in taken]
            collected = collected[:idx]  # consumed the tail
            break
        if idx < len(collected):
            result[p["name"]] = _coerce(p, collected[idx])
        elif p.get("required"):
            raise ParseError(f"missing required argument <{p['name']}>")

    # Reject leftover positionals (none of the params absorbed them).
    extra = collected[len(positionals):] if not any(p.get("variadic") for p in positionals) else []
    if extra:
        raise ParseError(f"unexpected argument(s): {' '.join(extra)}")

    # Enforce required flags.
    for p in params_spec:
        if not p.get("positional") and not p.get("rest") and p.get("required"):
            if result.get(p["name"]) is None:
                raise ParseError(f"missing required flag {flag_token(p['name'])}")

    return result
