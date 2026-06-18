<!-- Thanks for contributing! See CONTRIBUTING.md for the conventions below. -->

## What & why

<!-- The durable change and the reason it exists. -->

## How tested

<!-- e.g. ran the local checks; served/probed model X on a DGX; not DGX-tested. -->

## Checklist

- [ ] New/changed commands follow the single-source model: manifest +
      `lib/handlers/<domain>.py` handler returning a structured dict +
      registered in `HANDLERS`
- [ ] Core stays **stdlib-only** (any new dependency is optional and gated behind
      a clear install hint, lazy-imported)
- [ ] Docs updated for the change (command manifest help, README command list,
      related guides/playbooks)
- [ ] `python -m compileall -q lib bin` is clean and the help + schema audit
      passes (see CONTRIBUTING.md)
- [ ] Commits use a conventional prefix, a ≤72-char subject, and a durable
      WHAT/WHY body (no narrative)
