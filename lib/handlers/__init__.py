"""handlers — the command bodies, one plain function per leaf command.

Every handler has the signature `handler(params, cfg)`: it receives the typed
params dict (produced once by cliparse, or by an MCP tool wrapper) and the loaded
config, and may print operator-facing output and/or return a structured result.
This uniform contract is what lets a single manifest definition drive both the
CLI and a future MCP tool from the same code.

Each domain module exposes a `HANDLERS` dict mapping the manifest's `handler`
ref (e.g. "llm.serve") to its function; this package merges them into REGISTRY.
"""

from __future__ import annotations

from . import core, llm, comfy, transcribe, playbook, image, tts

REGISTRY: dict = {}
for _mod in (core, llm, comfy, transcribe, playbook, image, tts):
    REGISTRY.update(_mod.HANDLERS)


def get(name: str):
    """Resolve a manifest handler ref to its function, or raise KeyError."""
    return REGISTRY[name]
