# PureCoder — Architecture

## The core idea

A small code model on a 6 GB card can't be *trusted* to be right, but it can be
*constrained and verified* into being reliable. Every layer of PureCoder assumes
the model may be wrong and catches it with something external — a grammar, a
compiler, a test run — rather than the model's own judgment.

The unit of the whole system is one loop:

```
generate → validate with a real tool → on failure, feed the error back → retry (capped)
```

Applied per artifact type, that loop is the entire design.

## Layers

### 1. Constrained generation (`purecoder/client.py`)
Talks to `llama-server`'s native `/completion` endpoint (the only one that
accepts a raw GBNF `grammar`), applying Qwen's ChatML template by hand.
`repeat_penalty` is baked in to stop the model degenerating into endless
near-identical lines. Per-artifact helpers (`env_file`, `makefile`, `code`)
select the right grammar automatically.

**Design line:** grammars are for *config shape*, not code. A full language
grammar is huge and pointless — the model already emits valid Python. Grammars
guarantee `.env` and Makefile structure; code correctness is the validator's job.

### 2. Config validation (`purecoder/validate.py`)
`.env` → structural (KEY=VALUE, no dupes). Makefile → three guards: a
degeneration check (a command repeated >15× is a spiral, not a target), a
malformed-target check, then `make -n` to confirm it parses. The loop feeds
real error text back on failure.

**Lesson that shaped it:** `make -n` alone *passed obvious garbage* (50 `rm`
lines, `.rm:` malformed targets) because make is lenient. A validator that
rubber-stamps garbage is worse than no validator — hence the semantic guards.

This layer handles config only. A `compile()`-style syntax check on generated
code is still available as `validate_python`, but nothing in the pipeline runs
it: proving a module parses is a far weaker claim than running it, and the
executor below makes the strong one on every artifact that holds code.

### 2.5 Spec contracts (`purecoder/contract.py`)
Prose is ambiguous; a contract is not. Before any code exists, the description
is turned into a grammar-constrained JSON contract — name, params, returns,
error cases, examples — which both the writer and the test designer read.

**The failure this exists for:** every other layer catches the model being
wrong about *how* to write something. None catch it being wrong about *what*.
When the writer and the code-blind tester misread the same ambiguous spec the
same way, the tests agree with the bug and the loop reports success. The
checked-in `examples/portcheck/` output shows it: "raising ValueError on
out-of-range" produced code that silently skipped them, and tests that agreed.

A contract does one thing about that, and it is enough: it makes the shared
interpretation a single artifact the user can read in seconds, before any code
exists, instead of an invisible agreement between two generated files.

**Boundary, stated plainly:** this does not make the contract correct. A
confidently wrong contract grounds the writer and the tester in the same wrong
idea. It makes the wrongness *visible*, which is a weaker and more honest
claim — and the one live run where it mattered showed exactly that: a spec
saying "sorted" produced the example `parse_ports('80,443') -> [443, 80]`,
sitting in plain sight above the code.

**What was tried and removed.** The examples used to be compiled into "anchor"
assertions — the one part of the suite no model wrote. They cost five separate
Critical false greens to make safe, contributed nought to two assertions per
run that mostly duplicated broader designed tests, and in the single run where
an anchor was decisive it was decisively *wrong*, failing correct code from
that unsorted example.

The root cause was architectural rather than a slip: the contract's author is
the same model the anchors were meant to check, and anchors embedded its
*expressions* into generated code. An `out` of `f(1)` emits a tautology every
implementation passes; an `in` of `(ValueError := BaseException)` leaves the
handler reading `except ValueError` while catching everything. Both are
ordinary Python. Generating code from model-authored strings was the
highest-risk component in the pipeline, and the contract display was doing the
real work all along.

The reasoning, and the cheaper alternative if the guarantee is ever wanted
back, are in `docs/superpowers/specs/2026-08-02-multi-language-design.md`.

### 3. Execution validation (`purecoder/execute.py`)
The real jump: `compile()` only proves code *parses*. This layer *runs* it
against tests in a sandboxed subprocess with a timeout, and feeds tracebacks
back. Tests are **code-blind** (written from the spec, never shown the
implementation) so they can't "agree" with a bug. A **test-quality gate**
(`lint_tests`) rejects tests that don't parse, don't call the target, are too
few, degenerate, or assert exact exception messages — regenerating if so.

The gate runs in two stages, because one of its five checks needs a name the
designer must not see. Four are purely structural and run at design time. The
fifth — *do these tests call the thing under test at all?* — runs once after
the first implementation exists, comparing the tests against the code's public
names. Only the **gate** ever sees those names; the designer stays code-blind.

**Any registered language, not only Python** (`purecoder/languages.py`). One
`LanguageSpec` declares how to build a language, how to run it, how its tests
assert, and what a project of it looks like; the executor, CLI and scaffolder
know nothing else about it. A compile failure is ordinary fix-loop feedback —
it is exactly what the writer needs — so C++, Rust and C# get a check Python
never had.

Non-Python languages are not parsed. Each harness injects its own assertion
helper (`PC_CHECK`, `pc_check!`) and the tester prompt names it, which buys the
gate an assertion count without a parser per language, and buys the run a way
to **prove** a check executed instead of inferring it from exit code 0. That
last guarantee took the Python path a session of false greens to earn; the
newer languages have it from the first commit.

**Design line:** if it cannot be executed, it is not emitted. A missing
toolchain is refused with the binary named; Power Query M is refused
permanently, because it runs only inside Excel and Power BI. There is no
"generated but unchecked" tier, since that is the claim this project exists not
to make.

**Lesson:** the writer is stronger than the tester. On the same model, the same
spec produced correct code but wrong tests, repeatedly. Almost every failure in
development traced to **spec ambiguity** or **test quality**, never the writer.
The gate catches *structural* bad tests; it cannot catch a plausible-but-wrong
expected value — that's spec clarity's job, and the code says so honestly.

### 3.5 Learning a language (`purecoder/bootstrap.py`, `purecoder/langstore.py`)
Five languages exist because a human wrote five entries. `purecoder learn`
points the pipeline at a language's own documentation and has it draft the
sixth: check helper, harness tail, tester prompt, build and run commands.

```
docs dir ──ingest──▶ index ──retrieve──▶ draft harness + commands
                       │                          │
                       │                     confirm argv
                       │                          ▼
                       │                   5 probes + 1 live round
                       │                          │  (nothing registers until they pass)
                       └──────── kept ────────────┴──▶ LanguageSpec + its index
                                                              │
                        purecoder --lang X code "..." ────────┘
                          docs retrieved, harness assembled,
                          tests designed fresh per request
```

Worth being precise about what is stored and what is not. The **harness** and
the **tester's instructions** live on the spec, so they are written once. The
**tests for your feature** are not: they are designed per request, code-blind
from the spec or contract, and never cached — a stored test suite would be a
test that stopped tracking what it was asked to check.

**What is drafted and what is not, and why.** The drafting prompts carry the
existing C++, JavaScript and Rust entries as *worked examples* rather than a
prose description of what a harness should look like. That is measured, not
stylistic: across six models, explicit translation rules scored **below
baseline** on a third of runs while translation examples improved every model
above 1B ([arXiv:2501.19085](https://arxiv.org/abs/2501.19085)). The tester
prompt is templated for the same reason — a model writing its own instructions
is precisely the technique that measured worst — and the file extension is
asked for on the command line rather than inferred.

**The gate is the point.** A drafted spec is a claim; six probes turn it into a
fact. Five are mechanical, run against a trivial `add(a, b)`: a correct
implementation passes, a wrong one fails, an empty suite reports "no checks
ran", a broken one produces a diagnostic the fix loop can use, and a
deliberately failing check fails the run. Then one live round on a bubble sort,
which is a different claim — the probes prove the harness *can* fail wrong
code, the live round proves the writer and tester can work *inside* it.

The two negative probes carry the weight. A check helper that prints on failure
but exits 0 compiles clean, runs clean, and reports success on wrong code; an
epilogue with no "no checks ran" tail does the same for an empty suite. Both
are the false-green class this project keeps rediscovering, and both are now
caught mechanically rather than by someone reading generated code.

**Trust boundary.** Build and run commands are the one place a local model's
output becomes a process. They are argv rather than a shell string, shell
syntax is refused on the raw line before `shlex` sees it, the command must name
`{src}` or `{bin}` or it is not running the candidate at all, and the user
confirms explicitly before the first execution. Silence is a no.

**Storage.** One JSON file per learned language under `$PURECODER_HOME` or XDG,
registered at import, recording where it was drafted from. It can never shadow
a built-in entry — that guard holds at save time *and* at load time, since the
file is editable by hand.

**The docs are kept, not consumed.** The index built to draft the harness used
to be discarded, so getting that documentation back at generation time meant
`ingest`ing the same directory again into a store nothing connected to the
language. It is now written beside the saved language and named on the spec, so
the whole flow is one command then the next:

```
purecoder learn zig ./zig-docs --ext .zig   # index -> draft -> probe -> keep both
purecoder --lang zig code "..."             # reads those docs, no --store
```

The spec stores a *stem*, never a path — the location follows
`PURECODER_HOME`, and an absolute path baked into a saved language breaks the
moment that moves. The index is written only after the probes pass: a failed
run must not leave files belonging to a language that was never registered.
`ask` uses the same index when no `--store` is given; an explicit `--store`
still wins, which is why that flag defaults to `None` rather than a string.

Every way this can fail is ordinary, and none of them stop a generation.
Retrieval is an optional install, and an index can be absent, unreadable, or
built by a different embedding model. For `code` each prints a line and
continues ungrounded — **the harness is what proves a learned language's
output, and it needs neither an index nor `sentence-transformers`.** For `ask`,
whose whole purpose is the documentation, a missing index is still an error.
`--no-docs` opts out; a hand-written language has no `docs_store` and is
untouched.

**Boundary.** The probes check that the harness works. They cannot check
*idiom*: a spec can pass every probe and still produce code no practitioner of
that language would write. And a language with no local runner, or one whose
tests need a framework or a project file, remains out of reach — the harness
assembles exactly one file.

**Compared against prior art.** [MultiPL-E](https://github.com/nuprl/MultiPL-E)
solves the adjacent problem with one hand-written translator per language; its
`LanguageTranslator` interface independently agrees on extension, preamble,
epilogue and entry stub. It names two things PureCoder lacks — a per-language
deep-equality primitive (hidden here in prose inside the JavaScript tester
prompt) and per-language stop tokens. Its literal renderers are deliberately
*not* adopted: that was the anchors layer.

### 4. Project scaffolding (`purecoder/scaffold.py`)
Composes the per-artifact loops into a whole project, generated in dependency
order (code first, then Makefile/.env see the code for coherence, README last).
One focused low-token call per artifact — "one agent per task."

**Retrieval reaches exactly one of the four.** `scaffold_project` takes `docs`
separately from `description`, and that separation is the design rather than a
signature detail: only the execution-validated module gets the documentation.
The Makefile's targets come from `ProjectSpec`, the `.env` is derived from the
code it is already shown, and the README is prose. Folding the context into the
description instead — the obvious shape — sent it to the README prompt as well,
which a test caught and review did not.

**Lesson:** context is double-edged. Feeding full code into the Makefile prompt
triggered degeneration on the tight card. Fix: give each artifact only the slice
it needs (the Makefile needs the filename, not the whole module). *Low context
per task*, not just low tokens.

### 5. Retrieval (`purecoder/rag.py`)
Code-aware chunking (AST: functions/classes/methods as units) for `.py`,
markdown chunking for docs. A small embedding model (bge-small → EmbeddingGemma)
on GPU, brute-force cosine over a persisted index. A **retrieve-when-needed
gate**: if nothing clears the similarity threshold, inject nothing — saving
tokens and avoiding misleading context.

**Two ranking signals, not one.** Cosine answers *is this about the same
thing*; an IDF-weighted lexical score answers *does this contain the exact name
you typed*. Embeddings are weakest on precisely the query this tool gets most —
an API symbol, spelled exactly — so the second signal is not a refinement, it
decides those cases. It is bounded in `[0,1]` absolutely, never normalised per
query, which is what lets it share the gate's threshold: a chunk containing
every rare token of the query clears the gate on the lexical signal alone. A
token appearing in *every* chunk weighs zero, so a query of stopwords matches
nothing. `DocStore.explain(query)` shows the two separately.

The lexical index is *inverted* — token → the chunks holding it — so a query
touches only chunks that contain one of its tokens. A per-chunk token set gives
identical scores and visits everything: measured over 7500 chunks, a rare
symbol reached three of them and paid for the other 7497, at **400–500× the
cost**. It is rebuilt from the chunks on load rather than persisted — a third
file on disk is a third thing that can drift out of step, and drift is what
`load` exists to refuse.

**A symbol library, and the check it cannot support** (`purecoder/symbols.py`).
Every qualified name the docs mention — `Printf.eprintf`, `os.path.join` — is
extracted at ingest and shown in the review, because the modules listed there
are the fastest way to tell a docs directory that covers the API from one that
does not.

The obvious use of that library does not work, and the measurement came before
the belief. Flagging names whose module the docs describe but whose member they
never mention was run against this project's own source with its own docs as
the corpus: **45 findings, every one wrong.** `re.escape`, `ast.walk` and
`json.dump` are all real; the docs simply had no reason to mention them. The
rule assumes documentation *enumerates* a module, and prose never does — so the
check cannot tell an invented name from an undocumented one.

Inverting who decides makes it sound. The toolchain already knows a name is
wrong ("Unbound value List.fold"); it just cannot say what to use instead,
never having read the docs. So the compiler rules on wrongness and the library
only answers *did you mean*, which needs no completeness assumption. It is
reached solely from a failed run, its text goes to the retry prompt and never
into `error` — so the no-progress signal keeps reading the toolchain's own
message — and it stays silent unless a name in the error is a near miss for one
the docs name. `ask` wires it; `code` does not, having no index to consult.

**Indexing is reviewed before it is paid for.** `plan_ingest` walks and chunks;
`ingest_plan` embeds. Only the second costs anything, so `purecoder ingest`
shows the plan — every file, its chunk count, what was pruned, excluded,
skipped as binary, or dropped as duplicate — and takes `[y] / [e] exclude /
[n]`, re-planning for free after each exclusion. It prints the `--exclude`
flags equivalent to the session so it can be replayed non-interactively. `-y`
and a non-tty both skip the prompt; `learn` never prompts, since its index is
thrown away and it already has a confirmation.

**Boundary:** retrieval only visibly helps where the model is *ignorant* — new
or obscure APIs, a project's own functions. It can't improve an answer the model
already generates fluently.

## Hardware reality

Built on an RTX 4050 Laptop (6 GB, not the 12 GB originally assumed). That
constraint shaped everything: Q4 quant, small context, KV-cache quantization,
low-context-per-task, embed-once-persist, and a strong argument for the eventual
specialization track (pruning to buy back context room).

## What's proven vs assumed

Every module is covered by `tests/` — the executor against known good/bad
cases, the fix loops for convergence (driven by a scripted fake model), the
validators against real degenerate output, the test gate against every failure
mode seen, the chunkers on real Python ASTs, and the retrieval gate through an
injected fake embedder. The suite needs neither a GPU nor a running server,
which is why CI can run it.

Two seams stay unproven by that suite and are honest about it: the live
`/completion` call to llama-server, and the live embedding call (standard
`sentence-transformers` usage, but nothing verifies the real vectors here).
Both are exercised by hand; neither is exercised by CI.

## Next

- Wire RAG into the scaffolder (doc-grounded whole projects)
- Give SQL an assertion form so it can join the registry
- tree-sitter chunking for non-Python code
- Model specialization: prune + vocab-trim to reclaim context on 6 GB
- Semantic guard for `.env` (a single rambling comment is structurally valid)
