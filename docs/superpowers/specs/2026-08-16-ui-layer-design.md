# The UI layer — design

_2026-08-16. Approved in brainstorming; this is the record, not a proposal._

## What this is

A local web UI over `purecoder serve`, replacing a Figma Make mock
(`UI/AI Module UI Layer.make`) that was styled well and wrong about the
project. The mock's Grammar tab checked English prose — "passive voice on line
2; comma splice on line 4" — because it pattern-matched the word *grammar*
instead of reading what this project means by it. Four of its six tabs showed
data the pipeline does not produce.

The rule for this layer is the same one the rest of the project runs on: **if
it cannot be shown from something real, it is not shown.** No tab exists for
data no endpoint can answer.

## Sections

Three, down from six. Each maps to an endpoint.

| Section | Source | What it shows |
|---|---|---|
| Runs | `POST /code/stream` | the transcript of a generation, live; verdict and attempt count |
| Languages | `GET /status` | the registry, probed — with the refusal reason, not just a boolean |
| Grammars | `GET /grammars` | the three `.gbnf` files and their root bounds |

Cut, and why:

- **Gate** is not a section. Its rejections (`[tests] attempt 1 rejected: only
  2 assertion(s)`) happen inside a run and appear in that run's transcript,
  which is where a reader needs them — next to the code they judged.
- **Docs** and **Bench** have no endpoints. A section for either would have to
  invent its contents, which is the defect being removed.
- **Server status** — model, VRAM, tokens/sec — is a header strip. It is
  context for everything, not a place to navigate to.

## Layout

```
┌───────────────────────────────────────────────────────────┐
│ PURECODER          server up · Qwen3-Coder-30B · 33 tok/s │
├────────┬──────────────────┬───────────────────────────────┤
│ RUNS   │ ● word_count  py │  word_count · python          │
│ LANGS  │ ○ parse_ports py │  ok · 2 attempts              │
│ GRAMS  │ ● sum_list    ml │  ───────────────────────────  │
│        │ ◐ Makefile       │  [tests] accepted (19 lines)  │
│        │                  │  [attempt 1] AttributeError…  │
│        │                  │  [attempt 2] all tests passed │
│        │                  │  ───────────────────────────  │
│        │                  │  def word_count(text): …      │
│        │                  │  ───────────────────────────  │
│        │                  │  [ spec input ]        [RUN]  │
└────────┴──────────────────┴───────────────────────────────┘
```

Rail, run list, transcript. The verdict is a dot in the list; the evidence is
the pane. That ordering is the project's own finding made structural: a run
reports `ok=False attempts=4` whether the model wrote bad code or the harness
refused good code, and the only thing that tells them apart is the transcript.
A layout that leads with counters is built around the number this project has
already learned not to trust.

The spec input lives in the transcript pane's footer, not on a separate
screen — submitting a run and reading it are the same activity.

## Colour

Phosphor: a desaturated green-cast slate, so the chrome does not compete with
the one saturated green on the page.

```
base    #0a0f0d      surface #0f1712      line    #17231c
text    #dfe7e0      muted   #8fa593      faint   #4d6152
pass    #7fb069  ●   retry   #c9a227  ◐    refused #c05746  ○
```

Two rules:

1. **Colour is reserved for verdicts.** Nothing decorative is tinted. The
   mock spent its accent on the brand, the borders, the headers *and* the
   failure badge, so a refused run looked like every other element.
2. **Every verdict carries a shape as well as a hue.** Filled, half, hollow.
   The mock's red/green badges are indistinguishable to a colour-blind reader,
   which for a tool whose entire output is pass/fail is a defect rather than a
   preference.

Typography is JetBrains Mono throughout, vendored rather than fetched from the
Google Fonts CDN — a loopback tool should render with the network off. Press
Start 2P is kept for the wordmark alone; at 8px in body copy it was a costume.

## Backend changes

All additive. Nothing existing changes behaviour.

**`execute.py`** — `generate_validated_python` gains `on_event=None`. At each
point it already prints, it also calls `on_event({...})` with a structured
record: `{"kind": "tests"|"attempt"|"verdict", "attempt": n, "text": str}`.
Every existing caller passes nothing and is unaffected; `verbose` still
controls printing and is independent of this.

**`server.py`** — three additions:

- `POST /code/stream`: the same work as `/code`, emitted as server-sent
  events. Each event is one `on_event` record; the final event carries the
  same envelope `/code` returns, so a caller that ignores the stream and reads
  only the last event gets exactly the blocking behaviour.
- `GET /grammars`: name, text and root rule of each `.gbnf` in the package.
- `GET /status`: each language grows `{available, reason}` in place of a bare
  boolean. The reason is the useful half — `'sqlite3' is not installed`,
  `go is declared but not implemented yet`.

Loopback-only stays loopback-only. The UI is served by Vite in development and
proxies `/api` to `127.0.0.1:8100`, so nothing binds a second public port and
no CORS header is needed.

**Run history lives in the browser session**, not the server. The server stays
stateless; a refresh clears the list. Persisting runs is a real feature and a
different one — it needs a store, a retention rule and a migration story, none
of which this layer should invent.

## What is deliberately not built

- No auth. There is no remote; the server refuses to bind one.
- No run persistence, for the reason above.
- No editing of generated code in the browser. The pipeline's claim is that
  what it emits was executed; a text box that lets a human change it after the
  fact makes that claim false without saying so.

## Testing

The backend additions are covered by `tests/test_server.py` in the style the
file already uses — a real socket, real requests. Three cases that matter:
an SSE stream whose events arrive in order and whose last event matches what
`/code` would have returned; `/grammars` reporting a bounded root; and
`/status` carrying a refusal reason for an unwired language.

The UI is checked by `tsc --noEmit` and a production build. There is no
component test harness and this design does not add one: the UI holds no logic
worth testing that the endpoints do not already own.
