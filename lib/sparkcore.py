"""sparkcore — shared runtime library for the spark CLI.

Everything that is not a command body lives here: config loading, terminal
colors, the SSH transport, the Docker pre-flight, the model/GGUF helpers, and
the model-catalog/download plumbing. Both the thin `bin/spark` entry point and
every handler in `lib/handlers/` import from this module, so the command bodies
never restate infrastructure.

stdlib-only and self-contained — no third-party deps, no dispatcher/skillsbot at
runtime.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

CONFIG_PATH = Path.home() / ".config" / "spark.json"

_DEFAULTS = {
    "dgx_host":           "gx10-<id>.local",   # placeholder — set yours via `spark init` / config
    "dgx_user":           "your-user",  # placeholder
    "models_dir":         "~/models",
    "server_bin":         "~/llama.cpp/build/bin/llama-server",
    "server_log":         "~/llama-server.log",
    "venv":               "~/llama-cpp-venv",
    "hf_dl":              "~/hf_download.py",
    "port":               30000,
    # Memory headroom (GB) kept free when the serve fit-check decides whether a
    # model will load — covers the OS, page cache, and co-running services on top
    # of the model's weights + estimated KV cache.
    "mem_reserve_gb":     8,
    # Service asset paths — configurable so the whole stack can be relocated
    # (e.g. consolidated under a single svc-spark-owned /opt/spark tree) without
    # touching CLI code. Override per key via the env vars below or spark.json.
    "comfy_dir":          "~/comfyui-aeon-spark",
    "whisper_bin":        "~/whisper.cpp/build/bin/whisper-server",
    "whisper_log":        "~/whisper-server.log",
    "whisper_models_dir": "~/whisper.cpp/models",
    "download_log":       "~/models/download.log",
    # When the container stack runs under rootless Docker (as the svc-spark
    # service account), the daemon listens on the user's XDG runtime socket, not
    # the system socket — set true so docker commands over SSH target it.
    "docker_rootless":    False,
}

def load_config() -> dict:
    cfg = dict(_DEFAULTS)
    # Env var overrides
    env_map = {
        "DGX_HOST":                 "dgx_host",
        "DGX_USER":                 "dgx_user",
        "SPARK_MODELS_DIR":         "models_dir",
        "SPARK_SERVER_BIN":         "server_bin",
        "SPARK_SERVER_LOG":         "server_log",
        "SPARK_VENV":               "venv",
        "SPARK_HF_DL":              "hf_dl",
        "SPARK_PORT":               "port",
        "SPARK_COMFY_DIR":          "comfy_dir",
        "SPARK_WHISPER_BIN":        "whisper_bin",
        "SPARK_WHISPER_LOG":        "whisper_log",
        "SPARK_WHISPER_MODELS_DIR": "whisper_models_dir",
        "SPARK_DOWNLOAD_LOG":       "download_log",
        "SPARK_DOCKER_ROOTLESS":    "docker_rootless",
        "SPARK_MEM_RESERVE_GB":     "mem_reserve_gb",
    }
    for env, key in env_map.items():
        if os.environ.get(env):
            if key in ("port", "mem_reserve_gb"):
                cfg[key] = int(os.environ[env])
            elif key == "docker_rootless":
                cfg[key] = os.environ[env].strip().lower() in ("1", "true", "yes", "on")
            else:
                cfg[key] = os.environ[env]
    # File overrides
    if CONFIG_PATH.exists():
        try:
            file_cfg = json.loads(CONFIG_PATH.read_text())
            cfg.update(file_cfg)
        except json.JSONDecodeError as e:
            print(red(f"Config is not valid JSON ({CONFIG_PATH}): {e}"), file=sys.stderr)
            sys.exit(1)
    return cfg

# ── Colors ────────────────────────────────────────────────────────────────────

_TTY = sys.stdout.isatty() and os.environ.get("NO_COLOR") != "1"

def _c(t, code): return f"\033[{code}m{t}\033[0m" if _TTY else t
def bold(t):   return _c(t, "1")
def dim(t):    return _c(t, "2")
def red(t):    return _c(t, "31")
def green(t):  return _c(t, "32")
def yellow(t): return _c(t, "33")
def cyan(t):   return _c(t, "36")

def ok(t):   return green("✓") + " " + t
def warn(t): return yellow("⚠") + " " + t
def fail(t): return red("✗") + " " + t

# ── SSH helpers ───────────────────────────────────────────────────────────────

def ssh(cfg: dict, cmd: str, capture=True) -> str:
    full = ["ssh", f"{cfg['dgx_user']}@{cfg['dgx_host']}", cmd]
    if capture:
        r = subprocess.run(full, capture_output=True, text=True)
        return r.stdout.strip()
    subprocess.run(full)
    return ""

def ssh_screen(cfg: dict, session: str, cmd: str):
    ssh(cfg, f'screen -dmS {session} bash -c {json.dumps(cmd)}', capture=False)

# ── Docker pre-flight ───────────────────────────────────────────────────────────

def _docker_env(cfg: dict) -> str:
    """Shell prefix that points docker at the rootless socket when configured.

    Rootless dockerd listens on the invoking user's XDG runtime socket
    (`/run/user/<uid>/docker.sock`), not the system socket, so commands run over
    SSH must set DOCKER_HOST. Empty string for a normal (rootful) daemon.
    """
    if cfg.get("docker_rootless"):
        return "export DOCKER_HOST=unix:///run/user/$(id -u)/docker.sock; "
    return ""


def docker_probe(cfg: dict) -> tuple[str, str]:
    """Probe the remote Docker daemon and classify its state.

    Returns (state, raw) where state is one of:
      'ok'          — daemon reachable, commands will work
      'permission'  — socket exists but this user can't access it (not in docker group)
      'down'        — daemon installed but not running / unreachable
      'absent'      — docker binary not installed

    Used as a pre-flight so commands fail fast with the real cause + the fix,
    instead of polling a dead daemon or mislabelling it as 'not running'.
    """
    raw = ssh(cfg, _docker_env(cfg) + "docker info --format '{{.ServerVersion}}' 2>&1 || true")
    low = raw.lower()
    if "permission denied" in low:
        return "permission", raw
    if "command not found" in low or "no such file" in low:
        return "absent", raw
    if ("cannot connect to the docker daemon" in low
            or "is the docker daemon running" in low):
        return "down", raw
    # A clean `docker info` prints just the server version, no error text.
    if raw and "error" not in low and "cannot" not in low:
        return "ok", raw
    # Unknown failure — surface it as 'down' rather than silently mislabel.
    return "down", raw


def docker_remedy_lines(cfg: dict, state: str) -> list[str]:
    """Operator-ready remedy lines for a bad Docker state (point-of-failure help).

    Mirrors the spark README Troubleshooting table — keep the two in sync.
    """
    if state == "permission":
        usermod = "sudo usermod -aG docker " + cfg["dgx_user"]
        return [
            f"  {warn('Docker permission denied')} — your user is not in the docker group.",
            f"  Durable fix (on DGX, then log out/in):  {cyan(usermod)}",
            f"  Stopgap (reverts on any daemon restart): "
            f"{cyan('sudo chmod 666 /var/run/docker.sock')}",
        ]
    if state == "down":
        if cfg.get("docker_rootless"):
            return [
                f"  {fail('Rootless Docker daemon is not running.')}",
                f"  Start it (as {cfg['dgx_user']} on the DGX): "
                f"{cyan('systemctl --user start docker')}",
                f"  Check it: {cyan('systemctl --user status docker')}  "
                f"(needs `loginctl enable-linger {cfg['dgx_user']}`)",
            ]
        return [
            f"  {fail('Docker daemon is not running / unreachable.')}",
            f"  Check it:   {cyan('sudo systemctl status docker')}",
            f"  If it failed after an engine upgrade (buildkit 'invalid database'), reset and restart:",
            f"  {cyan('sudo rm -rf /var/lib/docker/buildkit && sudo systemctl daemon-reload && sudo systemctl restart docker')}",
        ]
    if state == "absent":
        return [f"  {fail('Docker is not installed on the DGX.')}  See: dgx-spark-comfyui-guide.md"]
    return []


def print_docker_remedy(cfg: dict, state: str):
    for line in docker_remedy_lines(cfg, state):
        print(line)

# ── Model / GGUF helpers ────────────────────────────────────────────────────────

def _parse_quant(filename: str) -> str:
    """Extract quant type from a GGUF filename, e.g. UD-Q4_K_XL, Q5_K_M."""
    stem = Path(filename).stem  # drop .gguf
    # Strip part suffix like -00001-of-00003
    import re
    stem = re.sub(r'-\d{5}-of-\d{5}$', '', stem)
    # Quant is typically the last dash-separated token(s) matching the pattern
    m = re.search(r'((?:UD-)?(?:IQ|Q)\d[\w_]+)$', stem, re.IGNORECASE)
    return m.group(1) if m else stem.split('-')[-1]


def _is_quant_dir(name: str) -> bool:
    """True if a directory name looks like a quant subdirectory, not a model name."""
    import re
    return bool(re.match(r'^(UD-)?(IQ|Q)\d', name, re.IGNORECASE)) or name in ('BF16', 'FP8', 'MXFP4_MOE')


def _model_name_from_path(model_path: str) -> str:
    """Derive the model name from a GGUF path, collapsing a quant subdir."""
    p = Path(model_path)
    return p.parent.parent.name if _is_quant_dir(p.parent.name) else p.parent.name


def _quant_glob(model_path: str, quant: str) -> str:
    """A remote glob covering every file of one model+quant (handles multi-part).

    Flat layout (quant in filename) → all parts sharing the quant token.
    Subdir layout (quant is a directory) → the whole quant subdir.
    """
    p = Path(model_path)
    if _is_quant_dir(p.parent.name):
        return str(p.parent)
    return f"{p.parent}/*{quant}*"


def _llm_instances(cfg: dict) -> list:
    """Live registry of running llama-server processes — the source of truth.

    Each instance is parsed straight from the process command line, so it can
    never drift from reality. Returns dicts of {pid, port, model_path, name,
    quant}, sorted by port.
    """
    # Filter on the process name (comm), not the full args — otherwise the
    # 'screen' wrapper and 'bash -c' shell (whose args also contain the binary
    # path) would each show up as a phantom instance. comm is exactly
    # 'llama-server' only for the real binary.
    raw = ssh(cfg, "ps -eo pid,comm,args | awk '$2==\"llama-server\"' || true")
    out = []
    for line in raw.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3 or "--model" not in parts[2]:
            continue
        pid, argstr = parts[0], parts[2]
        model_path = argstr.split("--model", 1)[1].strip().split()[0]
        port = argstr.split("--port", 1)[1].strip().split()[0] if "--port" in argstr else "?"
        out.append({
            "pid":        pid,
            "port":       port,
            "model_path": model_path,
            "name":       _model_name_from_path(model_path),
            "quant":      _parse_quant(Path(model_path).name),
        })
    return sorted(out, key=lambda i: i["port"])


def _du_bytes(cfg: dict, target: str) -> int:
    """Total on-disk size in bytes of a remote path/glob (a model's RAM footprint proxy)."""
    out = ssh(cfg, f"du -cb {target} 2>/dev/null | tail -1 | cut -f1")
    try:
        return int(out)
    except (ValueError, TypeError):
        return 0


def _free_bytes(cfg: dict) -> int:
    """Available system memory in bytes (unified memory shared by all models)."""
    out = ssh(cfg, "free -b | awk '/^Mem:/ {print $7}'")
    try:
        return int(out)
    except (ValueError, TypeError):
        return 0


# Minimal GGUF metadata reader — reads only the KV header (seeks past tensor data
# and large arrays) to recover the dims needed for a KV-cache estimate. Self-
# contained so the fit-check never depends on gguf-py / llama tooling being
# importable on the DGX. Prints the estimated f16 KV-cache size in bytes, or
# nothing on any problem (caller then falls open to the reserve margin alone).
_GGUF_KV_PROBE = r'''
import sys, struct
def main():
    path, ctx, par = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    SZ = {0:1,1:1,2:2,3:2,4:4,5:4,6:4,7:1,10:8,11:8,12:8}  # fixed-width scalar types
    f = open(path, 'rb')
    if f.read(4) != b'GGUF':
        return
    struct.unpack('<I', f.read(4))                      # version
    struct.unpack('<Q', f.read(8))                      # tensor_count
    kvc = struct.unpack('<Q', f.read(8))[0]             # metadata_kv_count
    def rstr():
        n = struct.unpack('<Q', f.read(8))[0]; return f.read(n)
    def skip(t):
        if t in SZ: f.seek(SZ[t], 1)
        elif t == 8: f.seek(struct.unpack('<Q', f.read(8))[0], 1)
        elif t == 9:
            et = struct.unpack('<I', f.read(4))[0]; cnt = struct.unpack('<Q', f.read(8))[0]
            if et == 8:
                for _ in range(cnt): f.seek(struct.unpack('<Q', f.read(8))[0], 1)
            else: f.seek(SZ[et]*cnt, 1)
        else: raise ValueError(t)
    want = ('block_count','attention.head_count','attention.head_count_kv',
            'attention.key_length','embedding_length')
    got = {}
    for _ in range(kvc):
        key = rstr().decode('utf-8','replace')
        t = struct.unpack('<I', f.read(4))[0]
        hit = next((w for w in want if key.endswith('.'+w)), None)
        if hit and t in (4,5,10):                       # u32/i32/u64 scalar
            v = struct.unpack({'4':'<I','5':'<i','10':'<Q'}[str(t)], f.read(SZ[t]))[0]
            got[hit] = v
        else:
            skip(t)
        if 'block_count' in got and 'embedding_length' in got and \
           'attention.head_count' in got and 'attention.head_count_kv' in got and \
           'attention.key_length' in got:
            break
    layers = got.get('block_count')
    n_head = got.get('attention.head_count')
    n_kv   = got.get('attention.head_count_kv', n_head)
    head_dim = got.get('attention.key_length')
    if head_dim is None and n_head and got.get('embedding_length'):
        head_dim = got['embedding_length'] // n_head
    if not (layers and n_kv and head_dim):
        return
    # f16 KV: 2 (K+V) * layers * n_kv * head_dim * tokens * 2 bytes
    print(2 * layers * n_kv * head_dim * ctx * par * 2)
try:
    main()
except Exception:
    pass
'''


def _kv_cache_bytes(cfg: dict, model_path: str, ctx: int, par: int) -> int:
    """Estimate the f16 KV-cache size (bytes) by reading the model's GGUF header.
    Returns 0 if it can't be determined — the reserve margin then carries the check."""
    import shlex
    cmd = (f"python3 - {shlex.quote(model_path)} {int(ctx)} {int(par)} "
           f"<<'PYEOF'\n{_GGUF_KV_PROBE}\nPYEOF")
    out = ssh(cfg, cmd)
    try:
        return int(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


def _human(n: int) -> str:
    """Format a byte count as a short human-readable size (e.g. 78G)."""
    f = float(n)
    for unit in ("B", "K", "M", "G", "T"):
        if f < 1024:
            return f"{f:.0f}{unit}"
        f /= 1024
    return f"{f:.0f}P"


def _port_log(cfg: dict, port) -> str:
    """Per-port server log path derived from the configured server_log base."""
    base = cfg["server_log"]
    if "." in Path(base).name:
        stem, ext = base.rsplit(".", 1)
        return f"{stem}-{port}.{ext}"
    return f"{base}-{port}"

# ── Model catalog / downloads ───────────────────────────────────────────────────

MODELS_USER_PATH = Path.home() / ".config" / "spark.models.json"

REPO_ROOT = Path(__file__).resolve().parent.parent


def _models_catalog():
    """Load the model catalog. Prefer the user's editable ~/.config/spark.models.json,
    else fall back to the repo example. Returns (catalog_dict, source_path)."""
    repo_example = REPO_ROOT / "templates" / "models.example.json"
    path = MODELS_USER_PATH if MODELS_USER_PATH.exists() else repo_example
    return json.loads(path.read_text()), path


def _run_pull(cfg, jobs, done_hint=""):
    """Pull a list of download jobs with the bundled hf_download.py.

    Each job: {repo, dest, glob, label, flat?}. The downloader is synced to the
    DGX first — a stale remote copy silently ignores --flat (placing files in
    nested split_files/ dirs), and keeping the engine current is cheap insurance.
    """
    if not jobs:
        print(warn("Nothing to pull (catalog section is empty)."))
        return

    local_engine = REPO_ROOT / "bin" / "hf_download.py"
    print(dim(f"Syncing downloader → {cfg['dgx_host']}:{cfg['hf_dl']}"))
    if subprocess.run(["scp", "-q", str(local_engine),
                       f"{cfg['dgx_user']}@{cfg['dgx_host']}:{cfg['hf_dl']}"]).returncode != 0:
        print(fail("Could not deploy the downloader to the DGX (scp failed)."))
        sys.exit(1)

    n = len(jobs)
    print(bold(f"Pulling {n} model(s):\n"))
    for i, j in enumerate(jobs, 1):
        print(f"  {bold(str(i))}/{n}  {cyan(j['label'])}")
        print(f"     {dim(j['repo'])} → {dim(j['dest'])}")
    print()

    chain = " && ".join(
        f"echo '[{i}/{n}] {j['label']}' && "
        f"python3 {cfg['hf_dl']} {j['repo']} {j['dest']} '{j['glob']}'" + (" --flat" if j.get("flat") else "")
        for i, j in enumerate(jobs, 1)
    )
    rc = subprocess.run(["ssh", "-t", f"{cfg['dgx_user']}@{cfg['dgx_host']}", chain]).returncode
    if rc == 0:
        print(ok(f"\nDone.{(' ' + done_hint) if done_hint else ''}"))
    else:
        print(fail(f"\nPull exited with status {rc} — re-run to resume (downloads are resume-safe)."))
        sys.exit(rc)
