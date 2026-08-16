# The UI layer

A local page over `purecoder serve`. Three sections, each backed by an
endpoint: **Runs** (a generation's transcript, streamed), **Languages** (the
registry as probed on this machine, with the reason for each refusal) and
**Grammars** (each `.gbnf` and its root rule).

## Running it

Two processes. The API first:

```bash
.venv/bin/python -m purecoder serve --port 8100
```

Then the page:

```bash
cd UI/app && pnpm install && pnpm dev      # http://127.0.0.1:5273
```

The model server has to be up too, or every run fails for one reason and the
header says so.

`pnpm build` typechecks and produces `dist/`. There is no component test
harness: the UI holds no logic the endpoints do not already own, and
`tsc --noEmit` plus a build is the whole check.

## Why it looks like this

The three sections are the ones with data behind them. The mock this replaces
had six, and four of them showed things the pipeline does not produce — its
Grammar tab checked English prose for passive voice, because it pattern-matched
the word *grammar* rather than reading what the project means by it. A tab that
has to invent its contents is the defect this repository exists to avoid,
rendered in CSS.

The transcript is the page rather than a detail view, and that is the one
layout decision worth defending. A run reports `ok=False attempts=4` whether
the model wrote bad code or the harness refused good code; those are opposite
bugs and the verdict cannot tell them apart. Only the transcript can. A
dashboard that leads with counters is built around the number this project has
already learned not to trust — see
[docs/live-runs/2026-08-07-the-harness-was-the-bottleneck.md](../docs/live-runs/2026-08-07-the-harness-was-the-bottleneck.md),
where nine defects sat behind a plausible score until someone opened a log.

Colour is reserved for verdicts; nothing decorative is tinted. Each verdict
also carries a shape — filled, half, hollow — because a tool whose entire
output is pass or fail cannot encode that in red against green alone.

## The Figma export

`AI Module UI Layer.make` is the original mock, kept as the design artifact it
is. It is a zip of git packs; nothing builds from it, and the app in `app/`
does not import from it.

## Notes

- The dev server proxies `/api` to `127.0.0.1:8100`. `purecoder serve` binds
  loopback only — it runs model-authored code in a subprocess on this machine —
  and proxying keeps it that way without a CORS header or a second open port.
- Run history lives in the tab. A reload clears it; nothing is written to disk.
- No webfont. The system mono stack renders with the network off and picks up
  JetBrains Mono when it is installed.
