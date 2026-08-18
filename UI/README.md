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

**The page is a record, not a terminal.** Three rules carry that, and each
encodes something true rather than decorating something.

*Type is evidence class.* Proportional means the interface is talking;
monospace means the machine produced this and it is exact. Nothing is
monospaced for atmosphere. The first version set every word on the page in
mono, which reads as a costume and — worse — left nothing able to outrank
anything else: the verdict and the word "language" were the same size in the
same face, on a page whose whole job is to make one of them findable.

*Colour is reserved for verdicts.* The chrome is neutral graphite with no cast,
so the only chroma on screen is a ruling. The ground used to be a green-black
that put a phosphor tint behind everything, which is a hue competing with the
one thing that needs to signal. Each verdict also carries a shape — filled,
half, hollow — because a tool whose entire output is pass or fail cannot encode
that in red against green alone.

*Attribution is structure.* The transcript is laid out as a ledger: role names
hang in a left margin against a continuous rule, and the line sits beside them.
Who produced a line is read off the layout rather than hunted for inside it —
which is the project's own thesis about `ok=False attempts=4` in the one place
it can be seen at a glance. On the SQL refusal it renders as
tester → writer ×3 → *tester redesigns the suite* → tester, and the redesign is
the tell that the writer was not what went wrong.

No webfont, deliberately: a loopback tool should render with the network off,
so both families are stacks the machine already has.

## The three cards, and what they are allowed to claim

Above the transcript: **This run** (attempts against the cap, elapsed,
grounded or not), **The tester** (suites accepted, drafts the gate threw back,
suite size, and whether the no-progress rule had it rewritten) and **What was
proved** (the gates, each with what it checks and what it actually did on this
run).

Two rules keep them honest. `not run` is a first-class answer — a gate that
never fired proved nothing, and rendering it as a tick would be a false green
in the one panel built to prevent those. And `status.ts` derives every field
from events the loop emitted or from the verdict envelope; nothing is
estimated.

The weak seam is named in that file: the suite's line count and the gate's
rejection reason exist only inside sentences meant for a person, so those two
are parsed out of text. The patterns sit in one `MARKERS` table, and if the
loop is reworded they degrade to `unknown` rather than to a wrong number.
`kind`, `agent` and `attempt` are structured fields and are used wherever they
can answer the question instead.

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
