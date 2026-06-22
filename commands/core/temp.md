# temp

```spec
{
  "name": "temp",
  "domain": "temp",
  "summary": "GPU temperature, utilization, power, clocks + active throttle reasons",
  "handler": "core.temp",
  "params": []
}
```

Read-only GPU telemetry from the DGX, parsed from `nvidia-smi` over SSH: temperature,
utilization, power draw, SM clock, and any **active throttle reasons** (decoded from
`clocks_event_reasons.active`). `spark status` shows a one-line summary (temp + util,
and a `THROTTLING` flag if clocks are being capped); `spark temp` is the detail view.

```bash
spark temp
```

Throttle reasons that mean clocks are being limited — *SW power cap*, *HW/SW thermal
slowdown*, *HW power brake* — are flagged in red; *idle* / *app clocks setting* are
shown as informational. Note: thermal-**safety** throttling is automatic in the GB10
hardware/driver, so this is **visibility**, not control — `spark` takes no action.
