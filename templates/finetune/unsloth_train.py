#!/usr/bin/env python3
"""unsloth_train — the in-container Unsloth QLoRA trainer for `spark finetune start`.

This is the LLM analogue of ai-toolkit's run.py: spark ships it (the operator's
pinned Unsloth image provides the dep tree — unsloth / torch / bitsandbytes / trl
/ llama.cpp — but NOT a training entry point, so spark supplies one). It is run as
a child of the spark_finetune.py watchdog, which owns the time-box / pause / resume
loop; this script owns the training itself:

  load base (4-bit QLoRA) → attach LoRA → SFT on a `messages` JSONL chat dataset
  → on clean completion: save the adapter, merge it, and export a GGUF (q4_k_m by
    default) the host publishes into models_dir for `spark llm serve`.

Everything is driven by a single rendered JSON job-config (see _CONFIG_KEYS); the
host (lib/handlers/llm.py) writes it and never imports unsloth, keeping spark
itself stdlib-only. Checkpoints (HF `checkpoint-<step>/`) make the run resumable:
on relaunch the trainer resumes from the latest one, so a time-boxed/paused session
picks up where it left off.

NOTE: the exact Unsloth/TRL API is what the plan's task-#1 smoke test pins against
the chosen GB10 image; this is the first cut, written to Unsloth's documented
recipe. Heavy/fragile steps (GGUF export) are guarded so a failure there never
discards a trained adapter.
"""

from __future__ import annotations

import argparse
import dataclasses
import inspect
import json
import os
import sys
from pathlib import Path

# Job-config keys the host renders (lib/handlers/llm.py:_render_job). Kept here as
# the single in-container record of the contract.
_CONFIG_KEYS = (
    "name", "base", "dataset", "eval", "output", "adapter_dir", "gguf_dir",
    "epochs", "rank", "alpha", "dropout", "lr", "max_seq_len", "save_every",
    "batch", "grad_accum", "quant", "gguf_quant",
)


def _filtered_kwargs(config_cls, kw: dict) -> dict:
    """Keep only kwargs this installed TRL's SFTConfig accepts — TRL renames config
    fields across versions (the exact API is what task #1's smoke test pins). Maps the
    known `max_seq_length`→`max_length` rename; drops anything else unknown with a note,
    so a renamed/removed field can't hard-crash the run before the trainer is pinned."""
    valid = {f.name for f in dataclasses.fields(config_cls)}
    out, dropped = {}, []
    for k, v in kw.items():
        if k in valid:
            out[k] = v
        elif k == "max_seq_length" and "max_length" in valid:
            out["max_length"] = v
        else:
            dropped.append(k)
    if dropped:
        print(f"[unsloth_train] note: SFTConfig ignored kwargs not in this TRL: {dropped}", flush=True)
    return out


def _load_messages_dataset(path: str, tokenizer):
    """Load a `messages` JSONL and render each row to a single `text` field via the
    model's chat template (train-time template == serve-time template).

    Reads the JSON directly rather than via `load_dataset("json")` so heterogeneous
    tool-calling rows render correctly — assistant `tool_calls`, `tool`-role results, and
    an optional per-row `tools` schema would otherwise trip Arrow's nested-schema
    inference. `tools=` is passed through so tool definitions land in the prompt (a no-op
    for plain chat rows, where it's None)."""
    from datasets import Dataset

    rows = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            rows.append({"text": tokenizer.apply_chat_template(
                row["messages"], tools=row.get("tools"),
                tokenize=False, add_generation_prompt=False)})
    return Dataset.from_list(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = json.loads(Path(args.config).read_text())

    # Import here (not at module top) so `--help` and a config-schema check don't
    # require the heavy GPU stack to be importable.
    import torch
    from unsloth import FastLanguageModel
    from trl import SFTConfig, SFTTrainer

    output = Path(cfg["output"])
    output.mkdir(parents=True, exist_ok=True)

    print(f"[unsloth_train] base={cfg['base']}  rank={cfg['rank']}  "
          f"qlora-4bit={cfg['quant']}  epochs={cfg['epochs']}  seq={cfg['max_seq_len']}",
          flush=True)

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=cfg["base"],
        max_seq_length=cfg["max_seq_len"],
        dtype=None,                      # auto (bf16 on GB10)
        load_in_4bit=bool(cfg["quant"]),  # QLoRA 4-bit by default; --no-quant → full LoRA
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=cfg["rank"],
        lora_alpha=cfg["alpha"],
        lora_dropout=cfg["dropout"],
        # all-linear (Unsloth default targets) — the research-backed coder default.
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )

    train_ds = _load_messages_dataset(cfg["dataset"], tokenizer)
    eval_ds = _load_messages_dataset(cfg["eval"], tokenizer) if cfg.get("eval") else None
    print(f"[unsloth_train] train rows={len(train_ds)}"
          + (f"  eval rows={len(eval_ds)}" if eval_ds is not None else ""), flush=True)

    sft = SFTConfig(**_filtered_kwargs(SFTConfig, dict(
        output_dir=str(output),
        per_device_train_batch_size=cfg["batch"],
        gradient_accumulation_steps=cfg["grad_accum"],
        num_train_epochs=cfg["epochs"],
        learning_rate=cfg["lr"],
        warmup_ratio=0.03,
        lr_scheduler_type="linear",
        optim="adamw_8bit",
        logging_steps=1,
        save_strategy="steps",
        save_steps=cfg["save_every"],
        save_total_limit=None,           # keep every checkpoint → always resumable
        bf16=torch.cuda.is_bf16_supported(),
        fp16=not torch.cuda.is_bf16_supported(),
        seed=42,
        report_to="none",
        dataset_text_field="text",
        max_seq_length=cfg["max_seq_len"],
        eval_strategy="steps" if eval_ds is not None else "no",
        eval_steps=cfg["save_every"] if eval_ds is not None else None,
    )))
    # TRL ≥0.12 renamed SFTTrainer's `tokenizer` arg to `processing_class`; pass whichever
    # the installed version takes (the other arg the smoke test would otherwise trip on).
    _tok_kw = ("processing_class"
               if "processing_class" in inspect.signature(SFTTrainer.__init__).parameters
               else "tokenizer")
    trainer = SFTTrainer(
        model=model,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        args=sft,
        **{_tok_kw: tokenizer},
    )

    # Resume from the latest checkpoint if a prior (paused/time-boxed) session left
    # one — this is what makes the watchdog's clean-stop resumable.
    resume = any(output.glob("checkpoint-*"))
    print(f"[unsloth_train] {'resuming from latest checkpoint' if resume else 'fresh run'}", flush=True)
    trainer.train(resume_from_checkpoint=resume)
    # Reaching here means trainer.train() returned normally — all epochs done. A
    # watchdog stop SIGINTs the process, so we never fall through to export on a pause.

    # Retain the adapter (~tens of MB) for incremental retrain / a future vLLM
    # hot-swap path — cheap insurance even though GGUF is the primary output.
    adapter_dir = cfg["adapter_dir"]
    print(f"[unsloth_train] saving adapter → {adapter_dir}", flush=True)
    model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)

    # Primary output: merge LoRA → GGUF (default q4_k_m) for the spark-native
    # llama.cpp serve path. Guard it so a failure here (e.g. the image lacks a
    # llama.cpp converter) doesn't discard the trained adapter above — the host
    # surfaces "adapter saved, GGUF export failed" and the run can be re-exported.
    gguf_dir = Path(cfg["gguf_dir"])
    quant = cfg["gguf_quant"]
    try:
        print(f"[unsloth_train] merging + exporting GGUF ({quant}) → {gguf_dir}", flush=True)
        model.save_pretrained_gguf(str(gguf_dir), tokenizer, quantization_method=quant)
        # Unsloth writes the .gguf into a directory IT names (often "<gguf_dir>_gguf"),
        # next to the merged 16-bit safetensors it drops in gguf_dir — so search the whole
        # run output and normalize the result to gguf_dir/<name>.<quant>.gguf, which is
        # exactly where the host's publish step (_ft_publish) looks.
        import shutil
        run_out = gguf_dir.parent
        ggufs = sorted(run_out.rglob("*.gguf"))
        if ggufs:
            gguf_dir.mkdir(parents=True, exist_ok=True)
            want = gguf_dir / f"{cfg['name']}.{quant}.gguf"
            src = ggufs[0]
            if src.resolve() != want.resolve():
                src.replace(want)                       # move into the canonical location
            # Drop the multi-GB intermediates (merged 16-bit safetensors + Unsloth's
            # export scratch dir); keep only the .gguf here. The adapter lives separately.
            for p in list(gguf_dir.iterdir()):
                if p.resolve() != want.resolve():
                    shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
            scratch = src.parent
            if scratch.exists() and scratch.resolve() != gguf_dir.resolve():
                shutil.rmtree(scratch, ignore_errors=True)
            print(f"[unsloth_train] GGUF ready → {want}", flush=True)
        else:
            print("[unsloth_train] WARNING: GGUF export produced no .gguf file", flush=True)
    except Exception as e:                      # noqa: BLE001 — report, don't crash the run
        print(f"[unsloth_train] WARNING: GGUF export failed ({type(e).__name__}: {e}). "
              f"Adapter is saved at {adapter_dir}; re-export later.", flush=True)
        # Training SUCCEEDED — return 0 so the watchdog records `complete` (not `error`)
        # and the host surfaces "complete but no GGUF" with the retained-adapter hint.
        # A real training failure raises earlier and exits non-zero → still `error`.
        return 0

    print("[unsloth_train] done.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
