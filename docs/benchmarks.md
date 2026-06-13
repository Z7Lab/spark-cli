# LLM inference benchmarks (DGX Spark / GB10)

Generation speed for the LLMs in the catalog
([`templates/models.json`](../templates/models.example.json)), measured on the
reference hardware with one consistent method. The GB10 is **memory-bandwidth
bound** for token generation, so the big lever is **active parameters per token**
(MoE models read only a fraction of their weights each token) — which is why a 35B
MoE outruns a dense-ish 120B by 3–4×.

**Hardware:** DGX Spark GB10, 128 GB unified memory
**Engine:** llama.cpp pinned at `1738129be` (build 9426) — see
[`templates/engines.json`](../templates/engines.example.json). *Numbers are only
meaningful against a known engine build; re-run after moving the pin.*
**Method:** `spark llm bench` — prompt `make a python script to sort numbers`,
`--ctx 8192`, 2 runs averaged, server-side `timings.predicted_per_second`
(generation throughput, excludes prefill).
**Date:** 2026-06-13

| Model | Quant | Params (total / active) | Footprint | **tok/s** |
|-------|-------|-------------------------|-----------|-----------|
| GLM-4.7-Flash               | UD-Q4_K_XL | compact MoE       | ~17 GB  | **67.7** |
| Qwen3.6-35B-A3B             | UD-Q5_K_M  | 35B / 3B (A3B)    | ~26 GB  | **60.7** |
| MiniMax-M2.5                | UD-Q3_K_XL | 229B / 10B        | ~101 GB | **25.9** |
| Nemotron-3-Super            | UD-Q4_K_M  | 120B / 12B        | ~82 GB  | **17.0** † |

† Nemotron is benched at `--parallel 1`. At the default `--parallel 4` it
**fit-refuses** — its estimated KV cache (~44 GB) pushes weights+KV+reserve past the
box's free memory. The single-request bench measures per-token gen speed, which
`--parallel` (concurrency slots) does not change; only the memory headroom does.

## Reading it

- **MoE active-params win:** GLM (compact MoE) and Qwen3.6-35B-A3B (3B active) are
  the snappy daily drivers. MiniMax (229B total but only 10B active) still clears
  ~26 t/s at a 101 GB footprint. Nemotron (120B/12B, hybrid Mamba-2+MoE) is the
  slowest — more active compute per token, suited to long-context work over raw TPS.
- **For agentic loops** (constant round-trips), throughput dominates → the A3B-class
  models. For one-shot hard reasoning where you'll wait, the bigger models earn it.

## Reproduce

```bash
spark engine status                       # confirm the engine matches the pin first
spark llm serve <model>                   # add --parallel 1 for the largest models
spark llm bench <model> --runs 2
```

These match prior hand-run baselines (Nemotron 16.97 vs 16.90, MiniMax 25.87 vs
25.88 from 2026-05-30), which is the cross-check that the `spark llm bench` path
reports the same numbers as launching `llama-server` by hand.
