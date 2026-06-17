# LLM inference benchmarks (DGX Spark / GB10)

How spark measures the catalog models, what the numbers mean, and where the
results live. The GB10 is **memory-bandwidth bound** for token generation, so the
dominant lever is **active parameters per token** — MoE models read only a
fraction of their weights each step, which is why a 35B MoE can outrun a
dense-ish 120B by several times.

## What's measured

**Speed** — `spark llm bench` sends a fixed prompt to a loaded model's
OpenAI-compatible endpoint and reports generation throughput from llama-server's
own `timings.predicted_per_second` (generation only, excludes prefill/network),
falling back to wall-clock tokens÷seconds when that's absent.

**Capability** — `spark llm probe` checks whether a model actually works in an
agent loop, via the optional [`llm-probe`](https://pypi.org/project/llm-probe/)
(`pipx install llm-probe`). It runs **8 checks** — valid tool call, enum/argument
constraints, right-tool-among-many, large tool list, system-prompt adherence, no
think-block leakage, no degenerate output, and response time — so a model scores
`N/8`. See [llm-probe's TESTING.md](https://github.com/Z7Lab/LLM-probe/blob/main/TESTING.md)
for exactly what each one checks. Speed without capability is a trap; a model can
clear 60 t/s and still fail to emit a usable tool call.

**Method:** prompt `make a python script to sort numbers`, `--ctx 8192`, 2 runs
averaged, against a **pinned** engine — throughput is only comparable against a
known build, so confirm the pin with `spark engine status` before trusting a
comparison. The bench path is spot-checked against hand-run `llama-server`
baselines: `spark llm bench` reports the same numbers as launching the server by
hand.

**Reference hardware:** DGX Spark GB10, 128 GB unified memory · engine llama.cpp
pinned per [`templates/engines.example.json`](../templates/engines.example.json).

## Where the results live

The actual numbers are **not** in this doc — they live as per-model JSON reports
under [`reports/reference/`](../reports/reference/) (the committed reference set;
your own `--save` runs stay local and gitignored). Each report carries its
provenance: source repo, quant, footprint, engine build, and the date measured.

For a quick look without running anything, see the rendered table at
[`reports/reference/RESULTS.md`](../reports/reference/RESULTS.md). It's generated
from those JSON reports — `spark llm reports` prints the table, and `--out`
writes it to a file:

```bash
spark llm reports                          # print every report you have
spark llm reports --dir reports/reference  # just the committed reference set
spark llm reports --dir reports/reference --out reports/reference/RESULTS.md  # regenerate the committed table
```

## Reproduce

```bash
spark engine status                       # confirm the engine matches the pin first
spark llm serve  <model>                  # add --parallel 1 for the largest models
spark llm bench  <model> --runs 2 --save  # speed      → reports/<model>.json
spark llm probe  <model> --save           # capability → same file
spark llm reports                         # render the table from the JSON
```

## Reading it

- **Active params, not total, drive speed.** Compact MoE models and low-active
  designs (A3B-class) are the snappy daily drivers; a model with high *total* but
  low *active* params can still be fast at a large footprint. More active compute
  per token (denser or hybrid Mamba/MoE models) trades throughput for raw
  capability and long-context strength.
- **Match the model to the workload.** Agentic loops (constant round-trips) are
  throughput-dominated → favor the fast A3B-class models. One-shot hard reasoning
  where you'll wait can justify the bigger, slower models.
- **Read speed *and* capability together.** A model can top the speed table yet
  fail system-prompt adherence — exactly the trade-off the capability column in a
  report surfaces and a TPS number alone hides.

## Notes

- **The largest models bench at `--parallel 1`.** Some (e.g. the 120B-class)
  fit-refuse at the default `--parallel 4` because the estimated KV cache pushes
  weights + KV + reserve past free memory. A single-request bench measures
  per-token generation speed, which `--parallel` (concurrency slots) doesn't
  change — only memory headroom does. The report records when a model was benched
  this way.
