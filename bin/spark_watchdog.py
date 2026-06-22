#!/usr/bin/env python3
"""spark_watchdog — the generic time-box / pause / resume / state machinery.

Shared by the two in-container training wrappers — `spark_train.py` (image
style-LoRAs via ai-toolkit) and `spark_finetune.py` (LLM QLoRA via Unsloth). It
owns the two hard requirements both lifecycles share:

  • Time-boxed sessions — `max_seconds` auto-stops the run cleanly just after the
    next checkpoint once the budget elapses, so a session lasts ~N and never
    lands mid-save (a mid-save kill can corrupt that checkpoint).
  • Pause on demand — a `control/stop` file (written by the host `… pause` verb)
    is honoured the same way: stop right after the next completed checkpoint.

Resume is the underlying trainer's own (relaunching picks up from the latest
checkpoint in the output folder); this wrapper only decides, on exit, whether the
run is `complete` (target reached / clean exit) or `paused` (stopped early).

The engine-specific bits — HOW a checkpoint is detected, and WHAT command runs —
are injected by the caller (`latest_step` / `stable_latest` callables + the
launch argv), so the loop here is engine-agnostic. ai-toolkit names checkpoints
`<name>_<step>.safetensors`; HF/Unsloth writes `checkpoint-<step>/` dirs — both
collapse to the same (latest-step, stable-size) contract this loop polls.

State is a single JSON file the host-side `… status` reads. stdlib-only (deployed
to the DGX, bind-mounted into the trainer container).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable


def write_state(path: Path, **fields):
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


def run(
    launch_argv: list[str],
    *,
    output: Path,
    state: Path,
    control: Path,
    max_seconds: int,
    target_steps: int,
    latest_step: Callable[[Path], int],
    stable_latest: Callable[[Path], tuple[int, int]],
    label: str = "spark",
) -> int:
    """Run `launch_argv` as a child, enforcing the time-box / pause contract.

    `latest_step(output)` → highest completed checkpoint step (0 if none).
    `stable_latest(output)` → (step, size) of the newest checkpoint; the caller
    compares size across polls so we only act on a *finished* save.

    Returns a process exit code (0 = complete or cleanly paused, 1 = error).
    """
    output.mkdir(parents=True, exist_ok=True)
    stop_flag = control / "stop"
    stop_flag.parent.mkdir(parents=True, exist_ok=True)
    if stop_flag.exists():       # clear a stale flag from a previous session
        stop_flag.unlink()

    started = time.time()
    resumed_from = latest_step(output)
    # Clear terminal fields from any prior (failed/paused) session so a fresh start
    # or resume never shows a stale exit_code/ended_at while status is "training".
    write_state(state, status="training", pid=os.getpid(), started_at=started,
                max_seconds=max_seconds, target_steps=target_steps,
                resumed_from_step=resumed_from, stop_reason=None,
                exit_code=None, ended_at=None)

    # Launch the trainer in its own session so we can signal the whole process
    # group for a clean shutdown (SIGINT → KeyboardInterrupt; the last saved
    # checkpoint is what resume continues from).
    proc = subprocess.Popen(launch_argv, start_new_session=True)

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
            cur_step = latest_step(output)
            write_state(state, current_step=cur_step, elapsed_seconds=int(elapsed))

            if not stop_requested:
                if max_seconds and elapsed >= max_seconds:
                    stop_requested, stop_reason = True, "time-budget"
                elif stop_flag.exists():
                    stop_requested, stop_reason = True, "paused"
                if stop_requested:
                    step_at_request = cur_step
                    write_state(state, status="stopping", stop_reason=stop_reason)
            else:
                # Wait for a NEW checkpoint past the one present when we asked to
                # stop, and only act once its size is stable across two polls (i.e.
                # the save has finished) — never interrupt mid-write.
                step, size = stable_latest(output)
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
    final_step = latest_step(output)

    # Complete = the trainer reached the target. A clean rc==0 with no stop request
    # also means it ran to the configured length (steps/epochs). Anything else is a
    # resumable pause.
    if final_step >= target_steps or (rc == 0 and not stop_requested):
        status = "complete"
        # The trainer's final artifact often isn't a step-suffixed checkpoint (it
        # writes a merged/exported output instead), so _latest_step can lag the
        # real position on completion — report the target.
        final_step = max(final_step, target_steps)
    elif stop_requested or stop_reason:
        status = "paused"
    else:
        status = "error"
    write_state(state, status=status, current_step=final_step,
                stop_reason=stop_reason, exit_code=rc,
                ended_at=time.time(), elapsed_seconds=int(time.time() - started))
    print(f"[{label}] {status}: step {final_step}/{target_steps} (rc={rc}, reason={stop_reason})")
    return 0 if status in ("complete", "paused") else 1
