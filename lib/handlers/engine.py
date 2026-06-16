"""engine handlers — pin-aware status/build for the on-DGX inference engines.

Engines (llama.cpp, …) are pinned in templates/engines.json to a commit + build
recipe (cmake flags). `status` flags drift from the pin; `build` rebuilds from it
(or a deliberate --ref/--latest, re-recording the pin). Mirrors how the model
catalog manages models — see sparkcore._engines_catalog / _engine_state.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys

from sparkcore import (
    bold, dim, cyan, green, yellow, red, ok, warn, fail,
    ssh, _engines_catalog, _engine_source_dir, _engine_state,
)

_SYM = {"in-sync": green("● in sync"), "drifted": yellow("▲ drifted"),
        "no-git": yellow("▲ not a git checkout"), "no-layout": yellow("▲ unknown layout")}


def status(params, cfg):
    """Show each engine's installed commit vs its pin (drift detection)."""
    catalog, path = _engines_catalog()
    engines = [k for k in catalog if not k.startswith("_")]
    want = params["engine"]
    if want and want not in engines:
        print(fail(f"Unknown engine '{want}'. Known: {', '.join(engines) or '(none)'}"))
        sys.exit(1)
    names = [want] if want else engines

    rows = []
    for name in names:
        entry = catalog[name]
        st = _engine_state(cfg, name)
        print(f"\n  {bold(name)}  {dim(entry.get('label', ''))}")
        print(f"    pinned     {cyan(st['pinned'][:12] or '(unset)')}")
        ins = st["installed"][:12] if st["installed"] else "—"
        print(f"    installed  {ins}   {_SYM.get(st['state'], st['state'])}")
        if entry.get("validated"):
            print(f"    validated  {dim(entry['validated'])}")
        if st["state"] == "drifted":
            print(f"    {dim('rebuild to the pin:')} {cyan(f'spark engine build {name}')}")
        rows.append(st)
    print(dim(f"\n  catalog: {path}"))
    return {"action": "engine.status", "engines": rows}


def build(params, cfg):
    """Rebuild an engine from its pinned commit (or --ref/--latest) and validate."""
    catalog, path = _engines_catalog()
    name = params["engine"]
    entry = catalog.get(name)
    if not isinstance(entry, dict):
        known = [k for k in catalog if not k.startswith("_")]
        print(fail(f"Unknown engine '{name}'. Known: {', '.join(known) or '(none)'}"))
        sys.exit(1)

    src = _engine_source_dir(entry, cfg)
    if not src:
        print(fail(f"Can't locate {name}'s source dir from cfg['{entry.get('path_key')}'] "
                   f"(expected it to end with '{entry.get('binary')}')."))
        sys.exit(1)

    branch = entry.get("branch", "master")
    moving = bool(params["ref"] or params["latest"])
    ref = params["ref"] or (f"origin/{branch}" if params["latest"] else entry["commit"])
    flags = " ".join(entry.get("cmake_flags", []))
    binary = entry["binary"]
    lib_dir = f"{src}/{binary.rsplit('/', 1)[0]}"

    print(bold(f"Rebuild {name}") + dim(f"  ({entry.get('label','')})"))
    print(f"  source  {cyan(src)}")
    print(f"  ref     {cyan(ref)}" + ("" if moving else dim("  (pinned)")))
    print(f"  flags   {dim(flags)}")
    if moving:
        print(warn("  --ref/--latest moves the pin; the new commit is re-recorded after a clean build."))
    print(dim("  This recompiles on the DGX (CUDA/CMake) — several minutes."))
    if not params["yes"]:
        try:
            if input("  Proceed? [y/N]: ").strip().lower() not in ("y", "yes"):
                print(dim("Aborted.")); return {"action": "engine.build", "built": False}
        except (EOFError, KeyboardInterrupt):
            print(red("\nAborted.")); return {"action": "engine.build", "built": False}

    q = shlex.quote(src)
    # Self-heal a stale CMake cache: if build/ was configured against a different
    # source path (e.g. the repo was relocated to /opt/spark), cmake refuses to
    # reconfigure. Wipe build/ only on that mismatch, so normal incremental
    # rebuilds keep their cache.
    guard = (f'if [ -f build/CMakeCache.txt ] && '
             f'! grep -qxF "CMAKE_HOME_DIRECTORY:INTERNAL={src}" build/CMakeCache.txt; then '
             f'echo "[spark] CMake cache points to a different source dir — wiping build/"; '
             f'rm -rf build; fi')
    # Non-interactive ssh doesn't load the login profile, so nvcc (under
    # /usr/local/cuda/bin) is off PATH and a fresh CMake configure fails to find
    # the CUDA compiler. Put it on PATH when present.
    cuda = '{ [ -d /usr/local/cuda/bin ] && export PATH="/usr/local/cuda/bin:$PATH" || true; }'
    build_cmd = (f"cd {q} && {cuda} && git fetch --tags origin && git checkout {shlex.quote(ref)} && "
                 f"{guard} && cmake -S . -B build {flags} && cmake --build build -j$(nproc)")
    print(dim(f"\nBuilding on {cfg['dgx_host']}…\n"))
    rc = subprocess.run(["ssh", f"{cfg['dgx_user']}@{cfg['dgx_host']}", build_cmd]).returncode
    if rc != 0:
        print(fail("Build failed — the previous binary is unchanged unless cmake overwrote it."))
        sys.exit(1)

    # Validate the freshly built server actually launches (the LD_LIBRARY_PATH case).
    ver = ssh(cfg, f"LD_LIBRARY_PATH={shlex.quote(lib_dir)} {shlex.quote(f'{src}/{binary}')} --version 2>&1 | head -1")
    if "version" not in ver.lower():
        print(fail(f"Built, but the binary won't run: {ver.strip()}"))
        sys.exit(1)
    built_commit = ssh(cfg, f"git -C {q} rev-parse HEAD 2>/dev/null || true").strip()
    print(ok(f"Built & validated {name} — {ver.strip()}"))

    if moving and built_commit:
        entry["commit"] = built_commit
        entry["validated"] = f"built via {ref} ({built_commit[:12]})"
        catalog[name] = entry
        path.write_text(json.dumps(catalog, indent=2) + "\n")
        print(ok(f"Pin moved to {built_commit[:12]} in {path.name}."))
    return {"action": "engine.build", "engine": name, "built": True,
            "commit": built_commit, "ref": ref, "moved_pin": moving}


HANDLERS = {
    "engine.status": status,
    "engine.build":  build,
}
