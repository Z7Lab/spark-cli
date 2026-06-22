#!/usr/bin/env python3
"""spark_finetune — in-container watchdog around the Unsloth QLoRA trainer.

The LLM analogue of spark_train.py: deployed to the DGX and bind-mounted into the
operator-provided Unsloth image; `spark finetune start` / `… resume` launch
it inside a detached screen session. It reuses the engine-agnostic `spark_watchdog`
loop (time-box / pause / resume / state) and supplies only the Unsloth-specific
bits — how an HF/Trainer checkpoint is detected, and the trainer launch command.

The actual training (load base → QLoRA SFT → merge → export GGUF) lives in the
sibling `unsloth_train.py`, which this wrapper runs as a child so the watchdog can
signal it for a clean, checkpoint-aligned stop. HF/Unsloth writes checkpoints as
`checkpoint-<step>/` directories (not the `<name>_<step>.safetensors` files
ai-toolkit uses), so the two detectors below are all that differs from the image
trainer. stdlib-only.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import spark_watchdog

# HF Trainer / Unsloth checkpoint dirs, e.g. output/checkpoint-50/. The optimizer
# state lives *inside* the dir, so (unlike ai-toolkit's flat files) there is no
# sibling file to mistake for a weight checkpoint.
_CKPT_RE = re.compile(r"^checkpoint-(\d+)$")


def _latest_step(output: Path) -> int:
    """Highest step among saved HF checkpoints (0 if none yet)."""
    best = 0
    if not output.is_dir():
        return 0
    for d in output.glob("checkpoint-*"):
        m = _CKPT_RE.match(d.name)
        if m and d.is_dir():
            best = max(best, int(m.group(1)))
    return best


def _stable_latest(output: Path) -> tuple[int, int]:
    """(step, size) of the newest checkpoint — used to detect a *completed* save.

    Size is the total bytes of the checkpoint's weight shards (`*.safetensors`,
    e.g. PEFT's adapter_model.safetensors); the watchdog compares it across polls
    so it only acts once the save has finished writing, never mid-write.
    """
    best_step, best_dir = 0, None
    if output.is_dir():
        for d in output.glob("checkpoint-*"):
            m = _CKPT_RE.match(d.name)
            if m and d.is_dir() and int(m.group(1)) >= best_step:
                best_step, best_dir = int(m.group(1)), d
    size = 0
    if best_dir:
        for f in best_dir.glob("*.safetensors"):
            try:
                size += f.stat().st_size
            except OSError:
                pass
    return best_step, size


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="rendered Unsloth job-config JSON")
    ap.add_argument("--output", required=True, help="HF training output dir for this run")
    ap.add_argument("--state", required=True, help="run-state JSON the host reads")
    ap.add_argument("--control", required=True, help="control dir holding the stop flag")
    ap.add_argument("--max-seconds", type=int, default=0, help="0 = no time budget")
    ap.add_argument("--target-steps", type=int, required=True,
                    help="optimizer steps the run targets (epochs × steps/epoch) — for progress + completion")
    args = ap.parse_args()

    # The trainer lives beside this wrapper (both bind-mounted at /workspace/bin).
    trainer = str(Path(__file__).resolve().parent / "unsloth_train.py")
    return spark_watchdog.run(
        [sys.executable, trainer, "--config", args.config],
        output=Path(args.output), state=Path(args.state), control=Path(args.control),
        max_seconds=args.max_seconds, target_steps=args.target_steps,
        latest_step=_latest_step, stable_latest=_stable_latest, label="spark-finetune",
    )


if __name__ == "__main__":
    sys.exit(main())
