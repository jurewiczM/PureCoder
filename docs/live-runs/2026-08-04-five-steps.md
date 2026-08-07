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

## The grammars and the testers (2026-08-05)

The first sweep never issued an `env`, `make` or `project` command, so two of
the three grammars had not run at all — only `contract.gbnf`, which derived
cleanly in every grounded arm of the measurement. Covering them found the worst
defect of either day.

**Truncation detection had been dead, project-wide.** `purecoder env` for a
four-key spec returned a thirty-key file cut off mid-comment and reported
`ok=True`. The loop does check for truncation; the flag it checks had stopped
existing. llama.cpp now reports `stop_type: "limit"` where older builds set
`stopped_limit`, so `data.get("stopped_limit", False)` answered "complete" for
every cut-off generation, and the truncation branch in the config loop, the
code loop and the contract loop was unreachable. Restored, the same spec
truncates on attempt 1, retries, and returns exactly the four keys asked for.

Worth noting what hid it: the code path validates by execution and the contract
path by JSON parsing, so a truncated artifact failed there for other reasons.
`.env` is validated structurally, and half a config file is structurally
perfect.

**And what that repair then exposed: `env.gbnf` could not finish.** Its root
rule was `line*`, unbounded, so a scaffold's `.env` ran to `n_predict` every
time — three attempts, three truncations, artifact failed. The retry prompt
asking for "a shorter, complete file" was ignored all three times, which is the
rambling-comment lesson again: bound the shape in the grammar, where it costs
nothing and cannot be ignored. `line{0,20}` ends the file, and the model now
produces a complete twenty-line config instead of an infinite one.

**A contract example that could not be called.** The scaffold's contract for
`count_words(text)` offered `count_words() -> raises ValueError` as its
empty-input demonstration. The test designer implemented it exactly, and
correct code failed the loop three times over a call that takes no arguments
where one is declared. That is structural — a contract param has a name and a
type and no default — so `validate_contract` now refuses it, and derivation
retries into a well-formed example.

**`makefile.gbnf`: clean on the first attempt**, correct tabs and sane targets.

**The testers, which is what the measurement kept blaming.** C++ produced four
targeted checks on the first attempt, including an overflow case nobody asked
for, and passed. JavaScript produced four good checks and the loop correctly
refused the implementation over a real edge case (a trailing separator).
Neither wrapped its assertions in an uncalled function — their prompts have
always forbidden a wrapper. Python's, the oldest, did not until this run, which
is precisely why it was the one that failed that way.

So the picture the measurement painted — *the tester is the bottleneck* — needs
one qualification: it is the PYTHON tester, and part of what looked like model
weakness was a prompt that never made the demand its four siblings make.

**The wrong-contract boundary, a third time.** With the no-argument example
refused, derivation produced `count_words("Python3.8") -> {"python3": 1,
"8": 1}` — semantically wrong, since stripping punctuation yields `python38`.
The tester implemented it and correct code failed again. No mechanical check
catches that; it is the documented cost of grounding, and it stays documented
rather than patched.

## Retrieval, over documentation downloaded from the internet (2026-08-05)

Everything above used documentation already on the machine. This run fetched
the real thing: 61 tutorials and guides from the `ocaml.org` repository, plus
five stdlib `.mli` interfaces — 3044 chunks after ingest, 861 qualified names
extracted (`List` 32, `String` 28, `Hashtbl` 23).

**Retrieval itself is good.** "how to add a binding to a Hashtbl" returns
`hashtbl.mli` declarations; "List.fold_left argument order" returns the list
tutorial's `fold_left` example; "Seq.unfold lazy sequences" returns the two
chunks of the Seq tutorial that define and use it. The tree-sitter work pays
off visibly here — the `.mli` hits are labelled `value_specification find`
rather than being paragraphs of an interface file.

**The chunker was handing the model fragments.** Retrieved chunks opened
mid-token: `de effect`, `rom the left hand end`, `htening to illustrate`,
`` l`. ``. A section longer than the window was sliced by character. The window
is now filled by whole lines, with the overlap carried by whole lines too, and
a line longer than the entire window — a minified file, a long URL — still
falls back to character slicing because it has no boundary to respect.
Re-ingested: every observed fragment is gone, and chunks opening with a short
lowercase fragment fall from 331 to 152, the remainder being ordinary `let ...`
code lines.

**The retrieval gate does not gate.** This was already documented as "looser in
practice than the number suggests"; here is the sharp version. `min_score` is
0.3. Against this corpus:

| query | top score |
|---|---|
| `List.fold_left argument order` | 1.261 |
| `Seq.unfold lazy sequences` | 1.240 |
| `how to add a binding to a Hashtbl` | 1.196 |
| **`premier league offside rule`** | **1.103** |
| **`how do I bake sourdough bread at home`** | **0.826** |

A question with no relationship whatsoever to the corpus outscores most real
ones, and nothing is ever refused. Two causes compound: bge-small's cosine
floor for any English text is high, and the hybrid score adds a lexical term on
top, so the scale runs past 1.0 while the threshold stayed where it was for
cosine alone. The README's claim that chunks are injected "ONLY IF they clear a
similarity threshold" is, as written, not true of any query anyone would type.

It is left unfixed on purpose. Raising the number to fit this corpus is tuning
against one sample, which is exactly what `docs/STATUS.md` says has never been
done here — and the failure mode of a too-tight gate (dropping documentation
the model needed) is worse and quieter than the current one. What the run
supplies is the evidence a future fix should be measured against.

**One bootstrap defect, from drafting against these docs.** The build command
came back as `ocamlc -o {bin} {src}.ml`, which passes every trust-boundary
check and asks the compiler for `candidate.ml.ml`. Normalised now, the way
`./{bin}` already was — refusing it was tried first and the model glued the
suffix back on three redrafts running.

**And a gap worth naming: the corpus had to be markdown.** `ingest` matches
prose and source extensions; real documentation on the web is usually HTML, and
an HTML docs directory is skipped entirely rather than stripped and indexed.
The tutorials used here happen to be markdown in their source repository, which
is why this run was possible at all.

## Making OCaml actually work (2026-08-05)

Wiring the entry made `code --lang ocaml "a function add a b"` pass on the
first attempt. Anything harder failed, and chasing that produced three fixes to
the fix loop itself — none of them OCaml-specific in cause.

**The fix loop could not see what it was fixing.** A compiler saying `line 16,
characters 59-62` was talking about a file the writer had never been shown: its
own previous output is not in the retry prompt, and the harness wrapped around
it never was. `quoted_source` now quotes those lines out of the *assembled*
file with the offending one marked, and the previous implementation itself goes
into the retry prompt, bounded at 2000 characters because finding 4 says
feeding full code forward triggers degeneration. Measured on the bubble sort
that prompted it: failures moved from "the harness will not compile" to a
genuine algorithmic bug — the loop working on the right problem, even where the
model could not finish.

**Retrieval was making generation worse, and that is the broken gate's bill.**
`sum_list` — `List.fold_left (+) 0`, about as easy as OCaml gets — passed on
the first attempt ungrounded and failed four attempts *with* the docs index
attached. The retrieved tutorial text diluted the prompt enough that the tester
reverted to `pc_check ((expr) "label")`, a malformation the prompt's own
counter-example had been suppressing, and the writer seeded a fold with
`min_int`. This is the cost of the gate that never refuses, measured: an
ungated retrieval injects documentation for a query that needed none, and the
context it spends is not free.

**So the constraint moved to the layer that can hold it — twice.** First a gate
rule (`test_lint`, declared per language because C++ writes
`PC_CHECK((a + b) == c, "x")` legitimately). That fixed the grounded `sum_list`
run and broke two others in a new way: the designer reproduced the malformation
on every attempt and the run ended at **attempts=0**, gate never satisfied,
writer never reached.

Then a repair (`test_fix`). It is worth being plain that this is the first
place the project edits code a model wrote. The justification is narrow and
mechanical: `pc_check ((expr) "label")` applies a string to a boolean and
cannot compile under any reading, so it has exactly one possible intent, and
the rewrite is meaning-preserving by construction rather than by judgement. It
is declared per language, tested from both sides, and the lint rule stays
behind it for shapes the repair does not match. The same argument the bootstrap
layer already uses for `./{bin}` and `{src}.ml`: refusing was tried first and
did not converge.

**Measured, five doc-grounded OCaml tasks, four retries each:**

| task | before | after |
|---|---|---|
| `sum_list` | ✗ (4 attempts) | ✓ (2) |
| `max_of_list` | ✗ (4) | ✓ (2) |
| `rev_string` | ✗ (**0** — gate never satisfied) | ✓ (1) |
| `is_palindrome` | ✗ (4) | ✗ (4) |
| `insertion_sort` | ✗ (**0**) | ✓ (1) |

**0 of 5 to 4 of 5**, including a real sorting algorithm on the first attempt.
The two `attempts=0` rows are the ones worth noticing: those runs never reached
the writer at all, and both now pass.

`is_palindrome` still fails, as does the bubble sort that started this. Both
are the writer's own OCaml, and that is the boundary this project has always
had -- with one difference worth recording: the failures are now algorithmic
rather than syntactic. The loop is arguing with the model about the answer
instead of about the language.

## Standing caveats on all of the above

- One pass, five tasks, two arms, a sampled model: directional, not
  significant. Divergence in particular is a *rare* event by construction, and
  a five-task set cannot estimate a rare event's rate.
- Q5_K_M at 20/29 layers, not the Q4_K_M the rest of the docs assume.
- A contract grounds the writer and the test designer, so any difference
  between arms cannot be attributed to either alone.
