# init

```spec
{
  "name": "init",
  "domain": "init",
  "summary": "First-time setup — create the config file",
  "handler": "core.init",
  "params": []
}
```

Walks through each connection setting interactively, showing the current default
so you can accept it with Enter or type a new value, then writes
`~/.config/spark.json`.

After it finishes, test the connection with `spark status`.
