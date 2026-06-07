#!/usr/bin/env python3
"""TTS speech generator (Qwen3-TTS) — runs INSIDE the qwen-tts venv on the Spark.

Driven by `spark tts`. Two modes:
  single: --text "..." --out out.wav [--speaker Ryan] [--instruct "..."] [--language English]
  batch:  --manifest jobs.json   where jobs.json is a list of objects with the
          same keys plus "out"; the model is loaded once and reused for all jobs.

Kept dependency-light and self-contained so it can be scp'd to the box and run
with `<venv>/bin/python tts_gen.py ...` — like hf_download.py, it ships in bin/
and runs on the DGX, though the model engine (qwen_tts) is TTS-specific.
"""
from __future__ import annotations

import argparse
import json
import sys

DEFAULT_MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"


def _load(model_id, device, attn):
    import torch
    from qwen_tts import Qwen3TTSModel
    if device == "auto":
        device = "cuda:0" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
    model = Qwen3TTSModel.from_pretrained(
        model_id, device_map=device, dtype=dtype, attn_implementation=attn,
    )
    print(f"[qwen-tts] loaded {model_id} on {device} ({attn})", file=sys.stderr)
    return model


def _gen_one(model, job):
    wavs, sr = model.generate_custom_voice(
        text=job["text"],
        language=job.get("language", "English"),
        speaker=job.get("speaker", "Ryan"),
        instruct=job.get("instruct") or None,
    )
    return wavs[0], sr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--manifest", help="JSON file: list of {text,out,speaker,instruct,language}")
    ap.add_argument("--text")
    ap.add_argument("--out")
    ap.add_argument("--speaker", default="Ryan")
    ap.add_argument("--instruct", default="")
    ap.add_argument("--language", default="English")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--attn", default="sdpa", help="attn impl (sdpa avoids a flash-attn build)")
    a = ap.parse_args()

    if a.manifest:
        jobs = json.loads(open(a.manifest).read())
    elif a.text and a.out:
        jobs = [{"text": a.text, "out": a.out, "speaker": a.speaker,
                 "instruct": a.instruct, "language": a.language}]
    else:
        ap.error("provide --manifest, or both --text and --out")

    import soundfile as sf
    model = _load(a.model, a.device, a.attn)
    for i, job in enumerate(jobs):
        wav, sr = _gen_one(model, job)
        sf.write(job["out"], wav, sr)
        print(f"[{i + 1}/{len(jobs)}] wrote {job['out']} ({sr} Hz)", file=sys.stderr)
    print("OK")


if __name__ == "__main__":
    main()
