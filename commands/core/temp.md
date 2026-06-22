# temp

```spec
{
  "name": "temp",
  "domain": "temp",
  "summary": "GPU temp/util/power/clocks + throttle reasons + what's running (training/inference) and for how long",
  "handler": "core.temp",
  "params": []
}
```

Read-only GPU telemetry from the DGX, parsed from `nvidia-smi` over SSH: temperature,
utilization, power draw, SM clock, and any **active throttle reasons** (decoded from
`clocks_event_reasons.active`). `spark status` shows a one-line summary (temp + util,
and a `THROTTLING` flag if clocks are being capped); `spark temp` is the detail view.

It also reports a **`Running`** line — what's actively on the GPU and how long it's
been going: a live **training** run (`spark train` — the `spark-train-<name>` container
is the ground truth, with its step progress + elapsed) and any **inference**
llama-servers (model · quant · port, with process uptime). If nothing GPU-bound is up,
it says so.

```bash
spark temp
```

Example under a training load:

```
NVIDIA GB10
  Temp      80°C
  Util      96%
  Power     60 W
  SM clock  2385 MHz
  Throttle  none
  Running   training  mydemo9b — step 500/2000 (25%)  up 1h54m
```

Throttle reasons that mean clocks are being limited — *SW power cap*, *HW/SW thermal
slowdown*, *HW power brake* — are flagged in red; *idle* / *app clocks setting* are
shown as informational. Note: thermal-**safety** throttling is automatic in the GB10
hardware/driver, so this is **visibility**, not control — `spark` takes no action.
