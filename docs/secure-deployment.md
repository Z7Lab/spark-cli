# Spark — Secure Host Deployment Runbook

Applied security posture for running the spark stack (LLM serving + AEON-Spark
ComfyUI + whisper) on the DGX Spark, hardened against a **supply-chain compromise
of the third-party ComfyUI image or its custom nodes escalating to host root**.

This is the **how**. The **why** (threat model, containment-architecture tradeoffs,
hardening rationale) lives in the general *Docker Host Security Guide* in the
internal knowledge base. This runbook is self-contained so a repo-only operator is
not blocked.

> Privileged host steps are run **manually by the operator** (sudo on the DGX). The
> spark CLI never makes privileged host changes — it just *drives* the result via
> its configurable paths and `dgx_user`. After each step the validation gate must
> pass before the next.

---

## 1. Dedicated `svc-spark` service account

A single least-privilege `svc-spark` account owns **all** spark automation
(`llama-server`, the rootless container stack, downloads). spark's `dgx_user`
points at it; the primary login stays for human/admin use. Rootless Docker is
per-user, so `svc-spark` also becomes the unprivileged owner of the rootless stack
— containment *and* separation of duties.

```bash
# Admin (one-time, privileged):
sudo useradd -m -s /bin/bash svc-spark              # locked password — key-only login
sudo loginctl enable-linger svc-spark               # user services run without a login session
grep svc-spark /etc/subuid /etc/subgid || \
  echo "svc-spark:100000:65536" | sudo tee -a /etc/subuid /etc/subgid

# SSH key install. NOTE: useradd creates svc-spark with a LOCKED password, so
# `ssh-copy-id svc-spark@host` does NOT work (nothing to authenticate with). The
# admin installs the key directly — simplest is to reuse the key the primary login
# already trusts, so the workstation key that reaches the primary admin also reaches svc-spark.
# Replace <your-username> with the actual primary admin login name on the DGX:
sudo install -d -m 700 -o svc-spark -g svc-spark /home/svc-spark/.ssh
sudo cp /home/<your-username>/.ssh/authorized_keys /home/svc-spark/.ssh/authorized_keys
sudo chown svc-spark:svc-spark /home/svc-spark/.ssh/authorized_keys
sudo chmod 600 /home/svc-spark/.ssh/authorized_keys
```

**Gate:** `ssh svc-spark@<host> whoami` → `svc-spark`. Manual poking afterwards is
`ssh svc-spark@<host>` or `su - svc-spark`.

---

## 2. Consolidate assets under `/opt/spark`

Move the whole stack under a single `svc-spark`-owned `/opt/spark` tree
(`/opt/spark/{models,comfyui,llama.cpp,whisper.cpp,bin,logs}`) — FHS-conventional,
single owner, off any human home, and it sets up rootless volume ownership cleanly.
The DGX is a single filesystem, so relocating hundreds of GB of models is an
**instant metadata-only `mv`**, not a copy.

```bash
# Admin / svc-spark (same filesystem -> instant):
sudo mkdir -p /opt/spark/logs /opt/spark/bin
sudo mv ~/models /opt/spark/models
sudo mv ~/comfyui-aeon-spark /opt/spark/comfyui
sudo mv ~/llama.cpp ~/whisper.cpp /opt/spark/
sudo chown -R svc-spark:svc-spark /opt/spark
```

Deploy spark's downloader (`spark download`/`queue` run it on the DGX) — it ships
in this repo, so copy it from your workstation:

```bash
scp bin/hf_download.py svc-spark@<host>:/opt/spark/bin/hf_download.py
```

Then re-point spark's configurable paths in `~/.config/spark.json` — **no CLI code
change is needed** (see the config table in the repo README):

```json
{
  "dgx_user":           "svc-spark",
  "models_dir":         "/opt/spark/models",
  "hf_dl":              "/opt/spark/bin/hf_download.py",
  "server_bin":         "/opt/spark/llama.cpp/build/bin/llama-server",
  "server_log":         "/opt/spark/logs/llama-server.log",
  "comfy_dir":          "/opt/spark/comfyui",
  "whisper_bin":        "/opt/spark/whisper.cpp/build/bin/whisper-server",
  "whisper_log":        "/opt/spark/logs/whisper-server.log",
  "whisper_models_dir": "/opt/spark/whisper.cpp/models",
  "download_log":       "/opt/spark/logs/download.log"
}
```

**Gate:** `spark status` works against the relocated paths; verify the rootless
bind-mount ownership / userns access on model and output dirs (the mapped
`svc-spark` subuid must read models and write outputs).

---

## 3. Rootless Docker + CDI (with validation gate)

Run the container stack under **rootless Docker + CDI** as `svc-spark` — the daemon
runs unprivileged, so a container escape is **not** host root. GPU access uses CDI
(NVIDIA's modern mechanism) instead of legacy `runtime: nvidia`.

```bash
# As svc-spark (admin does any privileged prereq, e.g. apt install uidmap slirp4netns):
dockerd-rootless-setuptool.sh install
systemctl --user enable --now docker          # pair with loginctl enable-linger svc-spark

# CDI spec for the GPU(s) (run once; regenerate after driver changes):
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
nvidia-ctk cdi list                           # expect: nvidia.com/gpu=all
```

Compose references the CDI device instead of `runtime: nvidia`:

```yaml
services:
  comfyui:
    devices:
      - nvidia.com/gpu=all
```

**No weaker-architecture fallback** — blockers are fixed at the toolkit/driver/
config layer, not by downgrading to userns-remap or a root daemon. The only
exception is a genuine, currently-unfixable hardware/driver limitation (e.g. NVIDIA
hasn't shipped rootless GPU for GB10): a **documented temporary state with the
blocker tracked as a fix-it item**, never a resting place.

**Gate — validation spike (do this BEFORE the full migration):** stand ComfyUI up
under rootless Docker + CDI on the GB10 and confirm the GPU is visible **and** it
can generate an image. Only then migrate `spark comfy` to drive the rootless stack
and extend the same isolation to the `ollama` sidecar.

---

## 4. Hardened compose (apply with §3)

Hardening applies **regardless of architecture** — rootless still benefits. It
shrinks what a compromised container can do before it ever reaches an escape
primitive.

```yaml
services:
  comfyui:
    image: ghcr.io/aeon-7/comfyui-aeon-spark@sha256:<pinned-digest>   # §5
    init: true                                # reap zombies / forward signals
    cap_drop: [ALL]
    cap_add: []                               # re-add ONLY what's proven needed
    security_opt: [no-new-privileges:true]
    read_only: true
    tmpfs: [/tmp]
    user: "1000:1000"                         # non-root inside the container
    devices: [nvidia.com/gpu=all]             # CDI GPU (§3)
    volumes:
      - /opt/spark/comfyui/models:/models:ro
      - /opt/spark/comfyui/output:/output:rw
    # NEVER: -v /:/host, -v /var/run/docker.sock:..., privileged: true
```

> **Gotcha:** compose hardening covers **only** compose-managed services. Anything
> launched via `docker run` (script/Makefile/spawned) keeps Docker's default caps
> (`NET_RAW`, `SETUID`, `SETGID`, `SYS_CHROOT`) unless you pass
> `--cap-drop=ALL --security-opt=no-new-privileges` explicitly. Caps are
> process-level, independent of `USER`.

---

## 5. Supply-chain pipeline

Own the supply chain — all four layers:

1. **Digest-pin** both images (`@sha256:`); resolve with
   `docker buildx imagetools inspect <image>:<tag>`. Upgrades become deliberate
   re-pins, not silent `:latest` pulls.
2. **Scan** the pinned image, gate on no critical findings:
   `trivy image --severity HIGH,CRITICAL <image>@sha256:<digest>`.
3. **Own the build** — build our own ComfyUI image from a Dockerfile we control
   (pinned base, our registry). *Research item:* is `aeon-7`'s Dockerfile public to
   fork, or only the prebuilt image published? If only the image, reproduce the
   sm_121 fixes from upstream ComfyUI ourselves. Build hygiene: BuildKit secret
   mounts (never `ARG`/`ENV` for secrets), no `curl … | sh` without hash check,
   multi-stage.
4. **Custom-node allowlist** — ComfyUI custom nodes run arbitrary Python in-process;
   allowlist them, pin to commits, review before install, no arbitrary runtime
   installs.

---

## See also

- General *Docker Host Security Guide* (internal KB) — threat model, containment
  architectures, and the full rationale behind these decisions. This document lives
  in the author's internal knowledge base and is not accessible outside that
  environment; the threat model it covers (container escape → host root via a
  compromised third-party image) is summarised in the preamble of this runbook, and
  the key decisions (rootless Docker + CDI, supply-chain pipeline, least-privilege
  service account) are each explained inline in their respective sections above.
- [README.md](../README.md) — the configurable service paths and the Troubleshooting table.
