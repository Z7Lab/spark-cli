#!/usr/bin/env python3
"""spark_train — in-container watchdog around ai-toolkit's run.py.

Deployed to the DGX (like hf_download.py) and bind-mounted into the training
container; `spark train start/resume` launch it inside a detached screen session.
It owns the plan's two hard requirements:

  • Time-boxed sessions — `--max-seconds N` auto-stops the run cleanly just after
    the next checkpoint once the budget elapses, so a session lasts ~N and never
    lands mid-save (a mid-save kill can corrupt that checkpoint).
  • Pause on demand — a `control/stop` file (written by `spark train pause`) is
    honoured the same way: stop right after the next completed checkpoint.

Resume is ai-toolkit's own: relaunching run.py picks up from the latest checkpoint
in the output folder. This wrapper adds nothing to resume except deciding, on exit,
whether the run is `complete` (target steps reached) or `paused` (stopped early).

State is a single JSON file the host-side `spark train status` reads. stdlib-only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

# Where ai-toolkit's entrypoint lives inside the operator-provided image. Set by the
# compose env (AITOOLKIT_RUN); defaults to the common layout.
RUNPY = os.environ.get("AITOOLKIT_RUN", "/opt/ai-toolkit/run.py")
_CKPT_RE = re.compile(r"_(\d+)\.safetensors$")


def _latest_step(output: Path) -> int:
    """Highest step number among saved LoRA checkpoints (0 if none yet).

    ai-toolkit names checkpoints `<name>_<step>.safetensors`; the optimizer state
    (`*optimizer*`) is excluded so it can't be mistaken for a weight checkpoint.
    """
    best = 0
    if not output.is_dir():
        return 0
    for f in output.rglob("*.safetensors"):
        if "optimizer" in f.name.lower():
            continue
        m = _CKPT_RE.search(f.name)
        if m:
            best = max(best, int(m.group(1)))
    return best


def _stable_latest(output: Path) -> tuple[int, int]:
    """(step, size) of the newest checkpoint — used to detect a *completed* save.

    Returns the latest step and the byte size of its file; the caller compares the
    size across polls so it only acts on a checkpoint that has finished writing.
    """
    best_step, best_file = 0, None
    if output.is_dir():
        for f in output.rglob("*.safetensors"):
            if "optimizer" in f.name.lower():
                continue
            m = _CKPT_RE.search(f.name)
            if m and int(m.group(1)) >= best_step:
                best_step, best_file = int(m.group(1)), f
    size = best_file.stat().st_size if best_file and best_file.exists() else 0
    return best_step, size


def _write_state(path: Path, **fields):
    """Merge fields into the run state JSON (atomic write)."""
    state = {}
    if path.exists():
        try:
            state = json.loads(path.read_text())
        except ValueError:
            state = {}
    state.update(fields)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--output", required=True, help="ai-toolkit training_folder for this run")
    ap.add_argument("--state", required=True, help="run-state JSON the host reads")
    ap.add_argument("--control", required=True, help="control dir holding the stop flag")
    ap.add_argument("--max-seconds", type=int, default=0, help="0 = no time budget")
    ap.add_argument("--target-steps", type=int, required=True)
    args = ap.parse_args()

    output = Path(args.output)
    state = Path(args.state)
    stop_flag = Path(args.control) / "stop"
    output.mkdir(parents=True, exist_ok=True)
    stop_flag.parent.mkdir(parents=True, exist_ok=True)
    if stop_flag.exists():       # clear a stale flag from a previous session
        stop_flag.unlink()

    started = time.time()
    resumed_from = _latest_step(output)
    # Clear terminal fields from any prior (failed/paused) session so a fresh start
    # or resume never shows a stale exit_code/ended_at while status is "training".
    _write_state(state, status="training", pid=os.getpid(), started_at=started,
                 max_seconds=args.max_seconds, target_steps=args.target_steps,
                 resumed_from_step=resumed_from, stop_reason=None,
                 exit_code=None, ended_at=None)

    # Launch ai-toolkit in its own session so we can signal the whole process group
    # for a clean shutdown (SIGINT → KeyboardInterrupt; the last saved checkpoint
    # is what resume continues from).
    proc = subprocess.Popen([sys.executable, RUNPY, args.config], start_new_session=True)

    stop_requested = False
    stop_reason = None
    step_at_request = -1
    pending_size = (-1, -1)      # (step, size) seen last poll, to confirm a stable save

    try:
        while True:
            rc = proc.poll()
            if rc is not None:
                break

            elapsed = time.time() - started
            cur_step = _latest_step(output)
            _write_state(state, current_step=cur_step, elapsed_seconds=int(elapsed))

            if not stop_requested:
                if args.max_seconds and elapsed >= args.max_seconds:
                    stop_requested, stop_reason = True, "time-budget"
                elif stop_flag.exists():
                    stop_requested, stop_reason = True, "paused"
                if stop_requested:
                    step_at_request = cur_step
                    _write_state(state, status="stopping", stop_reason=stop_reason)
            else:
                # Wait for a NEW checkpoint past the one present when we asked to
                # stop, and only act once its size is stable across two polls (i.e.
                # the save has finished) — never interrupt mid-write.
                step, size = _stable_latest(output)
                if step > step_at_request and size > 0 and (step, size) == pending_size:
                    os.killpg(os.getpgid(proc.pid), signal.SIGINT)
                    try:
                        proc.wait(timeout=120)
                    except subprocess.TimeoutExpired:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    break
                pending_size = (step, size)

            time.sleep(10)
    except KeyboardInterrupt:
        # Operator Ctrl-C'd the screen session — treat as a pause-now request.
        stop_reason = stop_reason or "interrupted"
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
            proc.wait(timeout=120)
        except Exception:
            pass

    rc = proc.poll()
    if rc is None:
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    final_step = _latest_step(output)

    # Complete = the trainer reached the target. A clean rc==0 with no stop request
    # also means it ran to the configured steps. Anything else is a resumable pause.
    if final_step >= args.target_steps or (rc == 0 and not stop_requested):
        status = "complete"
        # ai-toolkit writes the final checkpoint unsuffixed (<name>.safetensors), which
        # _latest_step can't read a step from — so on completion the run is at the
        # target, not the last *suffixed* checkpoint. Report the target.
        final_step = max(final_step, args.target_steps)
    elif stop_requested or stop_reason:
        status = "paused"
    else:
        status = "error"
    _write_state(state, status=status, current_step=final_step,
                 stop_reason=stop_reason, exit_code=rc,
                 ended_at=time.time(), elapsed_seconds=int(time.time() - started))
    print(f"[spark-train] {status}: step {final_step}/{args.target_steps} (rc={rc}, reason={stop_reason})")
    return 0 if status in ("complete", "paused") else 1


if __name__ == "__main__":
    sys.exit(main())
