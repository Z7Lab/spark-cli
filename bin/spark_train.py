#!/usr/bin/env python3
"""spark_train — in-container watchdog around ai-toolkit's run.py.

Deployed to the DGX (like hf_download.py) and bind-mounted into the training
container; `spark train start/resume` launch it inside a detached screen session.
It owns the plan's two hard requirements (time-boxed sessions + pause-on-demand,
both stopping cleanly just after the next checkpoint) — but the loop that enforces
them is the engine-agnostic `spark_watchdog`, shared with the LLM fine-tune
wrapper. This module only supplies the ai-toolkit-specific bits: how a checkpoint
is detected (`<name>_<step>.safetensors`) and the run.py launch command.

Resume is ai-toolkit's own: relaunching run.py picks up from the latest checkpoint
in the output folder. State is a single JSON file the host-side `spark train
status` reads. stdlib-only.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import spark_watchdog

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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--output", required=True, help="ai-toolkit training_folder for this run")
    ap.add_argument("--state", required=True, help="run-state JSON the host reads")
    ap.add_argument("--control", required=True, help="control dir holding the stop flag")
    ap.add_argument("--max-seconds", type=int, default=0, help="0 = no time budget")
    ap.add_argument("--target-steps", type=int, required=True)
    args = ap.parse_args()

    # ai-toolkit runs run.py <config>; the shared watchdog owns the time-box /
    # pause / state loop and calls back into our checkpoint detectors.
    return spark_watchdog.run(
        [sys.executable, RUNPY, args.config],
        output=Path(args.output), state=Path(args.state), control=Path(args.control),
        max_seconds=args.max_seconds, target_steps=args.target_steps,
        latest_step=_latest_step, stable_latest=_stable_latest, label="spark-train",
    )


if __name__ == "__main__":
    sys.exit(main())
