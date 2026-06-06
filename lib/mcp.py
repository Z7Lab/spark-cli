"""mcp — expose spark's manifest commands as MCP tools (stdlib-only stdio JSON-RPC).

One source of truth: the **same** command manifests, the **same** cliparse parser, the
**same** handlers, and the **same** JSON-Schema converter the CLI uses. There is no
second definition of any command — the MCP surface is generated from `commands/`.

Each `tools/call` maps the arguments object → an argv tail → `cliparse.parse` → the
exact `params` dict the CLI builds → `handler(params, cfg)`. Handler stdout is captured
(the stdio transport owns the real stdout) and returned as the tool's text content; its
structured return value is attached as `structuredContent`. `SystemExit` and exceptions
become tool errors (`isError`) rather than killing the server.

Transport: newline-delimited JSON-RPC 2.0 over stdio (what MCP stdio clients speak),
hand-rolled — no SDK, so the no-dependency property of the CLI is preserved.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys

import cliparse
import manifest
import handlers
from sparkcore import load_config

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "spark", "version": "1.0.0"}


class _MethodNotFound(Exception):
    pass


def tool_list(entries: list) -> list:
    """MCP tool descriptors for every command manifest."""
    tools = []
    for e in entries:
        sch = manifest.to_json_schema(e["spec"])
        desc = (e.get("body") or "").strip() or sch.get("description", "")
        tools.append({"name": sch["name"], "description": desc,
                      "inputSchema": sch["inputSchema"]})
    return tools


def args_to_argv(spec: dict, arguments: dict) -> list:
    """Render a tool-call arguments object into an argv tail for `cliparse.parse`.

    Reusing the CLI parser (rather than re-deriving params here) keeps argv the single
    place arguments are interpreted, so MCP gets identical defaults, typing, and enum
    validation. Positionals first (in declared order), then flags, then any rest tokens.
    """
    arguments = arguments or {}
    argv: list = []
    for p in spec.get("params", []):
        if p.get("positional") and arguments.get(p["name"]) is not None:
            v = arguments[p["name"]]
            argv += [str(x) for x in v] if (p.get("variadic") and isinstance(v, list)) else [str(v)]
    for p in spec.get("params", []):
        if p.get("positional") or p.get("rest") or arguments.get(p["name"]) is None:
            continue
        v = arguments[p["name"]]
        tok = cliparse.flag_token(p["name"])
        if p.get("type") == "bool":
            if v:
                argv.append(tok)
        else:
            argv += [tok, str(v)]
    for p in spec.get("params", []):
        if p.get("rest") and isinstance(arguments.get(p["name"]), list):
            argv += [str(x) for x in arguments[p["name"]]]
    return argv


def call_tool(by_tool: dict, cfg: dict, name: str, arguments: dict) -> dict:
    """Run one tool call: arguments → params → handler, captured and error-guarded."""
    entry = by_tool.get(name)
    if entry is None:
        return {"isError": True, "content": [{"type": "text", "text": f"Unknown tool: {name}"}]}
    spec = entry["spec"]
    try:
        params = cliparse.parse(spec.get("params", []), args_to_argv(spec, arguments))
    except cliparse.ParseError as e:
        return {"isError": True, "content": [{"type": "text", "text": f"Invalid arguments: {e}"}]}

    fn = handlers.get(spec["handler"])
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            result = fn(params, cfg)
    except SystemExit as e:
        text = buf.getvalue().strip() or f"command exited with status {e.code}"
        return {"isError": True, "content": [{"type": "text", "text": text}]}
    except Exception as e:  # never let one tool kill the server
        text = (buf.getvalue().strip() + f"\n{type(e).__name__}: {e}").strip()
        return {"isError": True, "content": [{"type": "text", "text": text}]}

    text = buf.getvalue().strip()
    out = {"content": [{"type": "text", "text": text or "(no output)"}]}
    if isinstance(result, dict):
        out["structuredContent"] = result
    return out


def handle(req: dict, ctx: dict) -> dict:
    """Dispatch one JSON-RPC request to its result (raises _MethodNotFound)."""
    method = req.get("method")
    if method == "initialize":
        return {"protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO}
    if method == "tools/list":
        return {"tools": tool_list(ctx["entries"])}
    if method == "tools/call":
        p = req.get("params") or {}
        return call_tool(ctx["by_tool"], ctx["cfg"], p.get("name"), p.get("arguments"))
    if method == "ping":
        return {}
    raise _MethodNotFound(method)


def build_context(cfg=None) -> dict:
    cfg = cfg if cfg is not None else load_config()
    entries = manifest.discover()
    return {"cfg": cfg, "entries": entries,
            "by_tool": {manifest.tool_name(e["spec"]): e for e in entries}}


def serve(stdin=None, stdout=None, ctx=None) -> None:
    """Read newline-delimited JSON-RPC from stdin, write responses to stdout."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    ctx = ctx or build_context()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = req.get("id")
        if rid is None:        # notification (e.g. notifications/initialized) — no reply
            continue
        try:
            resp = {"jsonrpc": "2.0", "id": rid, "result": handle(req, ctx)}
        except _MethodNotFound as e:
            resp = {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"Method not found: {e}"}}
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32603, "message": f"Internal error: {e}"}}
        stdout.write(json.dumps(resp) + "\n")
        stdout.flush()
