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

It also reports a **`Running`** line — what's actively on the GPU and **for how long**,
plus the command to get that thing's detail. By design `temp` owns only *what + uptime*
and **points** to the owner of the detail rather than re-deriving it (so a number like
step progress has a single source and can't drift):

- a live **training** run → detected from the `spark-train-<name>` container (ground
  truth — state files can go stale); shows the run + uptime, points to
  `spark train status <name>` for step/ETA.
- **inference** llama-servers → model · port + process uptime, points to `spark llm list`
  for quants/footprints.
- nothing GPU-bound up → it says so.

```bash
spark temp
```

Example under a training load:

```
NVIDIA GB10
  Temp      76°C
  Util      96%
  Power     59 W
  SM clock  2385 MHz
  Throttle  none
  Running   training mydemo9b  up 2h17m  → spark train status mydemo9b
```

Throttle reasons that mean clocks are being limited — *SW power cap*, *HW/SW thermal
slowdown*, *HW power brake* — are flagged in red; *idle* / *app clocks setting* are
shown as informational. Note: thermal-**safety** throttling is automatic in the GB10
hardware/driver, so this is **visibility**, not control — `spark` takes no action.
