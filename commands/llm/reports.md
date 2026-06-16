# llm reports

```spec
{
  "name": "llm.reports",
  "domain": "llm",
  "subcommand": "reports",
  "summary": "Render captured bench + probe reports as a Markdown table",
  "handler": "llm.reports",
  "params": [
    {"name": "dir", "type": "string", "help": "Reports directory to read (default: reports/)"},
    {"name": "out", "type": "string", "help": "Write the table to a file instead of stdout"}
  ]
}
```

Renders every captured report — the JSON files written by `spark llm bench
--save` and `spark llm probe --save` — into one Markdown table, fastest model
first. Each row carries the provenance recorded at measurement time: source
repo, quant, footprint, and the speed/capability numbers.

The committed reference set lives in `reports/reference/`; your own runs land in
`reports/` (gitignored). JSON files are the source of truth — this command is
just the human view, so the table in [`docs/benchmarks.md`](../../docs/benchmarks.md)
can be regenerated rather than hand-maintained.

    spark llm reports                         # print the table
    spark llm reports --out docs/results.md   # write it to a file
    spark llm reports --dir reports/reference # only the committed reference set
