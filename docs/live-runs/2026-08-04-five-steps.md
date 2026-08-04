# Live run — the five steps, against a real server

_2026-08-04_

## Setup

No GGUF had ever been downloaded for this project, but `ollama` had one:
`qwen2.5-coder:7b-instruct-q5_K_M`, sitting as a plain GGUF blob under
`/var/snap/ollama/common/models/blobs/`. Ollama itself is not usable here — the
pipeline needs llama.cpp's native `/completion` endpoint, because that is the
only one that accepts a raw GBNF `grammar`, and contract derivation is
grammar-constrained. So the local `llama-server` was pointed at ollama's blob:

```bash
llama-server -m /var/snap/ollama/common/models/blobs/sha256-52476f59a3cf... \
             -ngl 20 -c 4096 -fa on --port 8080
```

**Note the quantisation differs from every earlier finding in this repo.** The
documented setup is Q4_K_M; this is Q5_K_M, at 20 of 29 layers offloaded,
3.8 GB of 6.1 GB VRAM. Nothing below should be compared with an earlier number
without saying so.

## What was exercised

Everything built in the five-step session, in the order it was built:

| Step | Exercised | Verdict |
|---|---|---|
| writer demand for a learned language | `learn ocaml`, six runs | **four defects fixed; 3/5 probes → 5/5; never registered** |
| SQL | `code --lang sql`, four runs | **two defects, both fixed** |
| contract measurement | `purecoder measure`, twice | **first run invalid; see below** |
| declared packages | `code --with numpy`, both paths | refusal path exact; happy path exposed a defect |
| tree-sitter chunking | real OCaml `.mli` files | **one defect, fixed** |

## Defect 1 — SQL's writer was never told the database is empty

Spec: *"a view named added over a table orders(id, amount) that returns the
total amount as a column named total."*

The writer produced a correct view and no table. `no such table: main.orders`
came back three attempts running, unchanged, after which the loop's no-progress
rule concluded the tests were at fault and redesigned them. They were fine.

Every other language hands the writer an environment that already exists — a
compiler, a runtime, a standard library. SQL hands it an empty database, and
nothing said so. Adding that to `writer_system` was the obvious fix and **it did
not work**: the re-run produced a view over a table nobody created, again.

What worked was moving the constraint to the layer that can enforce it.
`missing_relation` turns SQLite's own error into an instruction — *the database
starts empty, nothing else creates `orders`, so your output must CREATE TABLE
orders and INSERT its rows* — as a hint on an already-failed run, in the same
slot as the documentation and harness-collision hints.

This is the project's oldest lesson about layers, arrived at independently for
the third time: the `.env` rambling comment ignored a system prompt and yielded
to a mechanical bound. A prompt asks; a fed-back diagnostic tells.

**And the fix's own first version was wrong, caught by the run verifying it.**
It answered `no such column: id` with "CREATE TABLE id", and the model created
a table called `id`. A missing column is a different mistake from a missing
table; the hint now says which.

Result: the spec that failed three runs at attempt 3 passes at attempt 2.

## Defect 2 — Python's tester was never told not to wrap its assertions

`code --with numpy "a function mean_of(xs) ... using numpy"` failed three
attempts with `no checks ran: 0 executed, 1 required`. The designer had written

```python
def test_mean_of():
    assert np.isclose(mean_of([1, 2, 3]), 2.0)
    ...
```

which nothing calls. The runtime instrumentation caught it and the loop refused
honestly — three times, at full generation cost.

The four non-Python tester prompts all forbid a wrapper (`no main()`, `no test
framework`, `output ONLY test statements`). Python's — the oldest of the five —
never did. It does now.

This one matters beyond its own run: see the measurement below.

## Defect 3 — the chunker lost every declaration in an OCaml interface

The tree-sitter chunker was written and tested against hand-written samples.
Run against the real thing — ten stdlib `.mli` files from `/usr/lib/ocaml` —
`option.mli` produced four chunks, three of them prose fragments like
`()] otherwise. *)`.

Two causes. `val` parses as `value_specification`, and the suffix list carried
`_specifier` — a different word — so all sixteen declarations fell through to
the preamble, exceeded the budget, and went to the markdown chunker, which cut
them mid-token. An interface file contains nothing else, so the chunker was
useless on exactly the corpus it was built for. And `type 'a t = ... None | Some
of 'a` was labelled `None`, because the name walk reached the variant's first
constructor before OCaml's `type_constructor`.

Measured over those ten files: **220 chunks → 540**, and `option.mli` from 4
fragments to 18 labelled declarations, each carrying the doc comment above it —
which in an interface file is the only prose there is.

The lesson is about the tests, not the code: a hand-written sample exercises the
shapes you thought of.

## The contract measurement, and why the first run was thrown away

First run, before the tester fix:

```
  arm           agreed  diverged  unusable   no code
  plain              3         0         0         2
  grounded           0         0         0         5
```

Read naively, that says grounding destroyed the pipeline. It says nothing of the
sort. **Four of the five grounded failures were `no checks ran`** — defect 2,
the tester wrapping its assertions in a function nobody calls. The arm that
looked catastrophic was measuring a bug that has nothing to do with contracts,
and the confound is visible only because the run's log records why each task
failed rather than only that it did.

Worth stating plainly: the instrument's first real use found a defect in the
pipeline rather than a fact about contracts. That is a reasonable thing for a
first measurement to do, and it is exactly why the number was not published.

The re-run, against the fixed tester prompt, is recorded below.

Second run, same five tasks, same server, tester prompt fixed:

```
  arm           agreed  diverged  unusable   no code
  plain              1         0         0         4
  grounded           0         0         0         5
```

The tester fix held — `no checks ran` appeared eight times in the first run and
**not once** in the second. What it revealed underneath is the more useful
result.

**Zero divergence, in both arms, in both runs.** Nine of the ten arms ended in
`no code`: the loop exhausted its retries and refused. Eight of those hit *"the
same failure on different code — suspecting the tests"*, the heuristic that
blames the test designer when an identical failure survives new code.

So the contract claim remains unmeasured, and now for a stated reason rather
than a missing instrument. Spec-divergence is a failure that can only occur
*downstream of a passing run*: the loop must report success before the oracle
can disagree with it. On this model, these specs almost never get that far. You
cannot measure a rare event behind a bottleneck that fails closed.

Three things follow, and none of them is "contracts do not work":

1. **The pipeline fails closed, which is the design working.** Five ambiguous
   specs, ten attempts, and not one instance of confidently wrong code
   reaching the user. The cost of that is a low completion rate.
2. **The tester is the bottleneck, and this is the first number for it.**
   Finding 1 in `docs/STATUS.md` has been qualitative since it was written.
   Eight of ten arms ending in "suspecting the tests" is the quantitative form.
3. **The instrument needs a stronger model, more tasks, or both**, before it
   can say anything about grounding. A five-task set was sized to detect a
   difference in divergence; it cannot detect one in an event that never
   occurred. Whether the right response is more repeats, easier specs, or
   accepting that this model cannot exercise the question is a decision for
   whoever runs it next — the numbers above are the input to that decision, not
   an answer about contracts.

Worth being explicit about one thing the second run does *not* show: the plain
arm fell from 3 agreed to 1. That is not evidence about anything. The runs
differ in the tester prompt AND in sampling, five tasks apart, and the honest
read is that both numbers are inside the noise of a set this size.

## The language bootstrap — three defects, and the gate holding

`learn ocaml` was run against ten real stdlib `.mli` files, six times. It never
registered. What it produced instead is the most useful part of this run,
because each failure had a different cause and two of them were the prompt's
fault rather than the model's.

**Run 1 — the model explained its code, and the explanation compiled.** The
drafted preamble ended with a paragraph beginning "This OCaml code declares a
mutable reference `pc_checks` initialized to zero...". Every drafting prompt
says "no prose, no explanation"; nothing enforced it, and `unfence` strips fence
markers only. It reached ocamlc as `Error: Syntax error` at line 14. The
redraft was then handed a compiler complaining about the model's own English,
which it did not read as an instruction to stop writing English.

**Run 2 — a structural check with no second chance.** Drafting died on "the
build command never writes to {bin}". The check is right: `ocamlc {src}`
compiles to `a.out` and leaves the run command with nothing to execute. But the
same machine had drafted `ocamlc -o {bin} {src}` correctly minutes earlier, and
the harness had been getting a redraft-with-diagnostics since the last live run
while the commands had not. One bad sample was ending the run.

**Runs 3–4 — the prompt was demanding invalid OCaml.** `let PC_CHECK cond =`,
answered by `Unbound constructor PC_CHECK`. OCaml reserves capitalised
identifiers for constructors, and the drafting prompt dictates a helper "named
PC_CHECK" with three uppercase worked examples behind it. Four redrafts, all
failing the same way, on an instruction that cannot be followed in that
language. The name is now a request with an escape clause — and nothing
downstream ever needed the capitals, since `draft_check_call` has always
matched case-insensitively.

**Run 5 — the model copied its own instructions into the answer.** The fixture
contained "and ends with this, which runs the tests:", verbatim from the
drafting prompt. Eight words, no code punctuation: under the prose filter's
threshold, and lowering that threshold would eat real comments (an OCaml
`(* the number of checks that have run so far *)` is nine words of mostly
English). The filter for this one is exact instead — the line was in the
request — and fires only on lines that look like instructions rather than code,
because the worked examples *are* the prompt.

**Run 6 — five probes out of five.** The harness was valid OCaml, could fail
wrong code, could detect an empty suite, and reported its errors. It failed at
the live bubble-sort round: `This expression has type 'a array`, a tester type
error. That is the documented boundary — *a learned language is only as good as
its tester* — and a different problem from the four above.

Two later runs at the same settings got three of five again. **So the honest
summary is that OCaml bootstrap on this model is possible and unreliable, where
before these fixes it was impossible**: the prose, the echo and the capitals
each blocked it deterministically, regardless of sampling.

The property that matters held throughout. Nothing registered. Six runs, five
distinct failure modes, and not once did a harness that could not prove its own
correctness end up in the registry — which is the entire claim the bootstrap
gate makes.

## Standing caveats on all of the above

- One pass, five tasks, two arms, a sampled model: directional, not
  significant. Divergence in particular is a *rare* event by construction, and
  a five-task set cannot estimate a rare event's rate.
- Q5_K_M at 20/29 layers, not the Q4_K_M the rest of the docs assume.
- A contract grounds the writer and the test designer, so any difference
  between arms cannot be attributed to either alone.
