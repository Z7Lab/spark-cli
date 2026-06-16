# llm probe

```spec
{
  "name": "llm.probe",
  "domain": "llm",
  "subcommand": "probe",
  "summary": "Verify a loaded model's tool-calling and prompt adherence (via llm-probe)",
  "handler": "llm.probe",
  "params": [
    {"name": "model",   "positional": true, "help": "Loaded model to probe (default: the only loaded one)"},
    {"name": "port",    "type": "int",    "help": "Target a specific server port instead of by name"},
    {"name": "timeout", "type": "int",    "default": 180,  "help": "Seconds per request (local models can be slow)"},
    {"name": "runs",    "type": "int",    "default": 1,    "help": "Runs per test (higher = consistency check)"},
    {"name": "tests",   "type": "string", "help": "Comma-separated subset of tests to run"},
    {"name": "model_id","type": "string", "help": "Override the model id sent to the endpoint (default: auto-discover)"},
    {"name": "report",  "type": "bool",   "help": "Show cached probe results instead of running a new probe"},
    {"name": "serve",   "type": "bool",   "help": "Load the model first (fit-checked) if it isn't already, then probe"},
    {"name": "quant",   "type": "string", "help": "Quant to load with --serve when several exist (e.g. UD-Q4_K_XL)"},
    {"name": "unload",  "type": "bool",   "help": "Unload the model after a successful probe (pair with --serve for a one-shot check)"},
    {"name": "save",    "type": "bool",   "help": "Save the capability result to a per-model report under reports/"},
    {"name": "out",     "type": "string", "help": "Write the report to a specific path instead of the default reports/ location"}
  ]
}
```

Probes a **loaded** model for the things benchmarks don't measure: whether it
actually emits valid **tool calls**, honors **enum/argument constraints**, and
adheres to the **system prompt** — the capabilities that decide if a model is
usable in an agent workflow. Where `spark llm bench` answers *"how fast?"*, this
answers *"does it actually work?"*. Serve a model first with
`spark llm serve <model>`.

This is an **optional** capability backed by the external
[`llm-probe`](https://pypi.org/project/llm-probe/) tool — it is not bundled, so
the core CLI stays dependency-free. Install it once with:

    pipx install llm-probe     # recommended
    pip install llm-probe      # or into the current environment

spark points llm-probe at the served model's OpenAI-compatible endpoint
(`http://<host>:<port>/v1`, no SSH), runs the capability suite, and prints
llm-probe's pass/fail report. Results are cached under
`~/.cache/spark/probe/` so re-probing the same port flags regressions.

    spark llm serve Qwen3.6-35B-A3B
    spark llm probe Qwen3.6-35B-A3B
    spark llm probe --port 30000 --runs 3
    spark llm probe --tests tool_call_basic,tool_call_large --timeout 240

One-shot "is this model any good?" — serve (fit-checked), probe, then unload,
leaving memory as it found it. `--serve` reuses `spark llm serve`, so its
fit-check, quant selection, and port assignment all apply; pass `--quant` to
load a specific quant non-interactively. It loads with a single request slot
(`--parallel 1`) — probing is sequential, so the smaller KV cache lets the
largest models fit without changing what any one request sees:

    spark llm probe GLM-4.7-Flash --serve --unload
    spark llm probe GLM-4.7-Flash --serve --quant UD-Q4_K_XL --runs 3

Pass `--save` to record the per-test capability result in this model's report
under `reports/` (shares provenance with `spark llm bench --save`, which fills
the speed section of the same file). `spark llm reports` renders them all:

    spark llm probe GLM-4.7-Flash --save
    spark llm reports

Re-view past results without re-running (no loaded model needed). With no
target it lists every cached probe; narrow with `--port`:

    spark llm probe --report
    spark llm probe --report --port 30000
