# PureCoder — Build Status

The handoff: what is built, what was measured, what is known broken, what is
next. Boundaries are grouped by area and stated as what is true now; the
narrative of how each was found is in [docs/live-runs/](live-runs/).

## Done and tested

655 tests, all green, none of them needing a GPU or a running server
(`pytest -q`). CI runs the same suite on Python 3.10–3.12, and a `toolchains`
job runs it inside an image carrying all seven toolchains with
`PURECODER_REQUIRE_ALL_LANGUAGES=1`, where a skip is a failure: 565 passed / 0
skipped, against 549 / 10 on a bare runner.

`scripts/smoke.sh` is the live counterpart: six checks — both config artifacts,
Python and OCaml end to end, the contract seam, and the refusal path — in about
a minute against a running server. It asserts the printed verdict AND the exit
code, because those were out of step until 2026-08-15. It is not a benchmark;
`scripts/bench/` is where scores come from.

| Phase | Component | Status | How it was verified |
|---|---|---|---|
| 1 | llama.cpp + Qwen2.5-Coder on 6 GB | ✅ working | server up, ~93% pass on a 15-task baseline (manual) |
| 2 | GBNF grammars (.env, Makefile) | ✅ working | 100% structurally valid output |
| 2 | `client.py` (constrained gen, repeat-penalty) | ✅ tested | ChatML shaping, fence stripping, grammar loading |
| 3 | `validate.py` (config validators + loop) | ✅ tested | catches degeneration & malformed targets |
| 3 | `execute.py` (execution validation) | ✅ tested | executor cases + convergence via a scripted fake model |
| 3 | test-quality gate (`lint_tests`) | ✅ tested | one test per observed bad-test mode, all 5 |
| 4 | `rag.py` chunking + retrieval + gate | ✅ tested | search/gate/persistence proven w/ fake embedder; index-integrity refusals and ingest pruning covered |
| 4 | code-aware AST chunker | ✅ tested | function/class/method/preamble boundaries verified |
| 5 | `contract.gbnf` + `validate_contract` | ✅ tested | schema guards past the grammar; grammar verified against a live llama-server |
| 5 | `derive_contract` + fallback | ✅ tested | retries, feeds errors back, degrades on a dead server |
| 6 | `languages.py` registry | ✅ tested | every entry coherent; availability probed, not assumed |
| 6 | C++ / JavaScript / Rust / C# execution | ✅ tested | correct passes, wrong fails, no-checks fails -- in each language |
| 9 | SQL (SQLite) execution | ✅ tested | same three probes; a check is a row, a failing one names itself |
| 9 | `bench.py` contract measurement | ✅ built, ✅ run, ⬜ inconclusive | calibrated hermetically; run live twice, zero divergence in both arms because nine of ten arms ended in the loop refusing |
| 9 | declared packages (`code --with`) | ✅ tested | probed in the sandbox interpreter before any model call; refusals name the install |
| 9 | tree-sitter chunking | ✅ tested | C++/Rust/OCaml chunked by definition; ingest now sees those files at all |
| 10 | OCaml execution | ✅ tested | hand-written entry; passes the five bootstrap probes; 4 of 5 live tasks generate and validate |
| 10 | the fix loop shows its own output | ✅ tested | quoted source around a diagnostic, plus the previous attempt, bounded |
| 11 | test-first mode (`code --tdd`) | ✅ tested | the suite must fail against a stub before any code is written; confirmed on screen |
| 6 | `--lang` + per-language scaffolding | ✅ tested | refusals explain themselves; a C++ project builds standalone |
| 7 | `langstore.py` persistence | ✅ tested | round trip, shadow guard, corrupt files skipped |
| 7 | `bootstrap.py` probe gate | ✅ tested | two deliberately broken harnesses built and rejected |
| 7 | `learn` drafting + CLI | ✅ tested | drafts scripted; probes run g++ end to end |
| 8 | hybrid ranking + reviewed ingest | ✅ tested | exact-name signal decides where cosine is blind; nothing embedded before the user accepts |
| 8 | drafted project layout + its probe | ✅ tested | two-sided against real make; a recipe that touches nothing is rejected |
| 8 | a learned language keeps its docs | ✅ tested | `code --lang X` grounded with no second ingest; every failure path degrades instead of stopping |
| 8 | the writer's shape demand, derived | ✅ tested | templated from the proven helper and tail shape, carried to the live round and through the store |
| — | `scaffold.py` orchestrator | ✅ tested | every artifact written; failure correctly reported |
| — | `cli.py` unified entry point | ✅ wired | argparse + subcommands route; `status` runs |
| — | `status.py` live probe | ✅ tested | degrades gracefully with server down |
| 12 | toolchain image + `account()` | ✅ tested, ✅ run | one image, 7 toolchains; CI runs the suite in it with nothing allowed to skip — 565 passed / 0 skipped, against 549 / 10 on a bare runner |
| 12 | ten-task cross-language benchmark | ✅ built, ✅ run | 60 tasks over 6 languages, 2026-08-09; four of seven failures were the harness, not the model |
| 12 | `benchlog.py` failure attribution | ✅ tested, ✅ repaired | misattributed 4 of 7 real failures to `writer` on its first live run; re-classified over those same 60 transcripts, `writer` now holds exactly the 2 that did not compile |
| 13 | `agents.py` role ledger | ✅ tested, ✅ run | the loop records what each role spent as it spends it; separated the two genuine refusals of the 2026-08-16 run by role count alone |
| 13 | `scripts/bench/attribution.py` | ✅ built, ✅ run | 60 runs, 58 passes, median 1 attempt, 16 min; found SQL's 0/10 to be the task set and refuses it by name |
| 13 | `scripts/demo.sh` | ✅ run | one function per runnable language; 6 of 7 passed and the seventh's refusal was the tester, which the ledger showed |
| — | `purecoder serve` local HTTP API | ✅ tested | 14 cases over a real socket; same gates and refusals as the CLI; loopback-only asserted; a live generation driven through it |
| — | `POST /code/stream` (SSE) | ✅ tested | events arrive per attempt, last one is the `/code` envelope; driven live through the browser |
| — | the UI layer (`UI/app`) | ✅ built, ⬜ untested | three sections, each backed by an endpoint; typechecks and builds; a real run driven end to end in a browser. No component tests -- see UI/README.md |

## Key findings from the build

1. **The writer is stronger than the tester.** Same model, same spec → correct
   code, wrong tests, repeatedly. Test generation is the weak link and the best
   target for specialization. Six separate measurements now point here.
2. **Almost every failure was spec ambiguity or test quality**, never the
   writer.
3. **Lenient tools rubber-stamp garbage.** `make -n` passed 50-line `rm`
   spirals; semantic guards were required on top of the parse check.
4. **Context is double-edged** on a small card — feeding full code forward for
   coherence triggered degeneration. Minimal context per task is the rule.
5. **Real hardware is 6 GB, not the assumed 12.** It shaped every choice.
6. **Mechanically-generated tests are not automatically trustworthy.** The
   anchor generator involves no model and still produced five false greens,
   none of which a passing suite revealed. All five came from embedding
   model-authored *expressions* into generated code: an unparenthesized value
   made `assert f(10,3) == 3, 1` assert only `== 3`; a `#` truncated an
   assertion to a truthiness check; a walrus rebound an exception name so the
   handler caught `BaseException`; `out: "f(1)"` emitted a tautology. The rule
   that ended it was structural: **an anchor may embed data, never behaviour**
   — every value must be a literal.
7. **A live run finds what unit tests cannot.** Every session has found defects
   a fully green suite could not see. See the live-run table below.
8. **Constrain at the cheapest layer that can express it.** The `.env` rambling
   comment survived a system prompt and a semantic validator; bounding the line
   length *in the grammar* made it structurally impossible and free. Shape is
   the grammar's job.
9. **The model was never the bottleneck.** Qwen3-Coder-30B-A3B at Q3_K_M runs
   on this 6 GB card with `-ngl 99 --cpu-moe` — experts in system RAM, ~3B
   activated per token — in 1864 MiB at 33.3 tok/s, less VRAM and more speed
   than the 7B it replaces. But the 7B's Q5_K_M passes the same five OCaml
   tasks once the harness is fixed, having scored 4, 3, 4, 3 on the broken one.
   The 30B's case rests on halving the attempts, not on the score. Q4_K_M was
   measured and rejected on capability (3 of 5).

## Known boundaries

### Tests and gates

- **Test-first proves a suite CAN fail, not that its expectations are right.**
  `--tdd` runs the designed suite against a stub and refuses it unless a check
  ran AND failed, which kills the class no static gate can see (`assert True`
  parses, names the target, is not degenerate). It cannot judge the
  EXPECTATION: a live run produced `assert word_count(' ') == 1` against
  correct code. The confirmation step is the one moment a person reads what
  will judge the code before the code exists, and `-y` skips it.
- **Test-first is Python only.** A stub needs a real signature, which the
  contract does not supply; elsewhere an empty implementation is a compile
  error, which is not evidence about assertions. Refused, not approximated.
- **The gate catches structural bad tests, not plausible-but-wrong values.**
  That boundary has never been crossed and is where the next real gain is.
- **Reachability is proven at runtime, not statically.** Tests are instrumented
  to count checks that execute, so asserts sitting in a `def test_x():` nobody
  calls now fail with "no checks ran". The static gate still cannot see it.
- **A wrong contract fails correct code rather than passing wrong code.** The
  first live contract run produced `parse_ports('80,443') -> [443, 80]` for a
  spec saying "sorted", and the anchor faithfully failed a correct
  implementation. Noisy-wrong beats silent-wrong, but it is not free.
- **The project edits model-authored test code in exactly one place.**
  `test_fix` rewrites `pc_check ((expr) "label")`, which applies a string to a
  boolean and cannot compile under any reading — one possible intent, so the
  rewrite is meaning-preserving by construction. Declared per language, tested
  from both sides. Refusing was tried first and ended runs at attempts=0 with
  the writer never reached. A malformation with two possible meanings does not
  belong in this field.

### Execution and languages

- **Seven languages execute, and only run-to-completion code.** Python, C++,
  JavaScript, Rust, C#, SQL and OCaml compile where needed, run real
  assertions, and prove a check executed. Go, Java and Swift are declared and
  refuse until both a toolchain and a test idiom exist. Power Query is refused
  permanently — it runs only inside Excel and Power BI.
- **SQL's harness lives in two places.** SQL has no assertion and no reliable
  non-zero exit: SQLite's `RAISE` works only inside a trigger and takes a
  literal, and `SELECT 1/0` returns NULL. So a check is a ROW, and the verdict
  is read back by a stdlib `sqlite3` driver rather than a neutral interpreter.
  That asymmetry is defensible only because the driver is ours; the invariant a
  test enforces is that the *spec* proves a check ran, in the tail or in the
  runner. In exchange SQL reports every failing check, not just the first.
  `project --lang sql` refuses — a Makefile recipe would have to reproduce the
  driver.
- **The sandbox inherits this venv, and now says what it has.** The executor
  runs `sys.executable`, so `import numpy` succeeds. What was missing was any
  declaration: `code --with numpy` probes the import in the real interpreter
  before a single model call and refuses with the exact `pip install` line. The
  permission reaches the test designer too, since a writer allowed numpy and a
  tester that is not produces assertions that cannot run. Deliberately NOT
  built: a per-run venv that pip-installs on demand — it needs the network, CI
  cannot exercise it, and a flaky install is a "generated but unchecked" tier
  by another name. `--with` is Python-only and refuses elsewhere.
- **Two live limits are still real**, on a "small web app that graphs random
  numbers" spec: `serve_forever()` never returns, so the timeout is the only
  verdict, and successive binds hit `TIME_WAIT`. A missing import triggers one
  stdlib-only retry and then stops. Function-shaped specs pass first attempt.
- **A toolchain that splits its streams the other way loses its diagnostics.**
  `execute.py` ended a failed run with `stderr or stdout`, and the `or`
  discarded stdout whenever stderr said anything — `dotnet` writes
  `candidate.cs(11,1): error CS0106: ...` to stdout and a content-free
  "compilation failed" to stderr, so the fix loop was asked to repair an error
  it was never shown, and one live task spent four attempts doing exactly that.
  Both streams now, stderr first, each trimmed SEPARATELY so a chatty stdout
  cannot push a traceback out of the window. `_DIAGNOSTIC` knows MSBuild's
  `path(12,34): error CS0106:` and OCaml's `File "x.ml", line 14, characters
  6-12:` — the latter anchored on `characters N-M`, which Python never writes,
  because `File "x", line N` also matches a traceback frame and turned one
  traceback into thirty diagnostics.
- **A harness collision is explained, not prevented.** When a failed attempt
  redefines something the harness provides, the retry prompt names it — the
  toolchain would only say "multiple definition of `main'" about a file the
  writer has never seen. The check is textual, so it can miss, and it is only a
  hint on an already-failed run.
- **Drafted commands reach the machine two ways, unequally narrow.** Build and
  run are argv and shell syntax is refused outright. A project recipe cannot be
  argv (`g++ ... && ./main` needs `&&`), so it is a shell line with the shell's
  other powers denied by name — pipes, redirection, `;`, substitution, a
  backgrounding `&` — and `run`/`test` must name the entry file. The user
  confirms both, and `make install` is never run. That is a closed door, not a
  sandbox: isolation is still a temp dir and a process group.

### Retrieval

- **The gate was repaired twice, and the second repair never shipped.** First
  the lexical score counted only tokens the corpus knew, so `cheapest flights
  to Lisbon in March` scored a perfect 1.000 against OCaml docs because "march"
  appears somewhere; weighting unseen tokens at the rarest known weight
  separated the bands (real 1.10–1.34, unrelated 0.50–0.70) and the threshold
  moved to 0.8. Then `retrieve_context` — the only retrieval `code`, `ask` and
  `project` actually perform — kept the `min_score=0.3` it had inherited, so
  every doc-grounded run in the project was gated at 0.3 anyway: "best pizza
  toppings" injected 1311 characters of tutorial into the writer's prompt.
  Retrieval no longer accepts a threshold argument at all, and a test asserts
  it cannot grow one back. Re-measured over fifteen queries: ten real questions
  score 1.043–1.400 and all retrieve, five unrelated score 0.498–0.736 and none
  do. **Worth sitting with:** the gate was measured, repaired, documented and
  celebrated, and the number that shipped was the old one two calls away.
- **It changes `ask`.** An index that loads but answers nothing used to be
  unreachable and now happens. `code` degrades to an ungrounded run, because
  its harness proves the output either way; `ask` refuses, because there the
  index is the command rather than an improvement.
- **RAG only helps where the model is ignorant, and can actively hurt.**
  Measured: `sum_list` in OCaml passed first attempt ungrounded and failed four
  attempts with the docs index attached — retrieved tutorial text diluted the
  prompt until the tester reverted to a malformation the counter-example had
  been suppressing. That is why the constraint moved to `test_lint` and
  `test_fix`, which hold regardless of prompt size.
- **Retrieval runs twice; the second query is the error.** A retry is keyed on
  what the TOOLCHAIN objected to — `Unbound value String.rev` names the gap
  exactly where prose only describes the goal. Bounded at half the first
  budget, excluding what was already injected. Its text reaches the prompt and
  never `error`, so it cannot disturb the no-progress signal. **Not yet shown:
  that the retrieved text helps.** The mechanism is proven, the value is not.
- **Ranking is two signals sharing one gate.** Cosine plus an IDF-weighted
  exact-name score bounded in `[0,1]`, so a chunk holding every rare token of
  the query clears on the lexical signal alone at cosine zero — that is
  `min_lexical`, the exact-symbol rescue, and it is only safe now that an
  unrelated question can no longer score 1.0 lexical. Stopwords cannot do it,
  since a token in every chunk weighs nothing. Measured over 7128 chunks of
  this repo with real bge-small embeddings on six queries, the lexical signal changed
  the ranking in four and *rescued* none — cosine already ranked a chunk
  containing the symbol first. The guarantee is real; on this corpus it was not
  yet needed. The weight (0.5) and the threshold (0.8) are calibrated on one
  corpus, not tuned against a benchmark — there isn't one.
- **Retrieval cost is the model call, not the search.** Over 7495 chunks:
  lexical ~0.005 ms (inverted index), cosine ~3 ms (brute force), loading an
  index ~220 ms. Embedding the query dominates. A real docs directory is the
  OCaml case at 15 chunks, where the inverted index buys nothing measurable —
  headroom for a large corpus, not a fix anyone was waiting on. Brute-force
  cosine stays: an ANN index would trade exactness for milliseconds nobody
  needs.
- **Code is chunked by its own grammar, where one is installed.** tree-sitter
  splits C++, Rust, OCaml, JavaScript, C#, Go, Java and SQL by definition, in
  the shape the Python AST chunker already used. Two limits: node types are
  matched by SUFFIX (`_definition`, `_item`, `_binding`) rather than a
  per-language table, so an unusual grammar may drop a definition into the
  preamble — degraded, not wrong; and a chunk's name comes from a bounded
  breadth-first walk, so an identifier nested deeper than four levels yields a
  chunk labelled by node type. Without the optional package, code degrades to
  prose chunking.
- **A capability and its wiring are two things.** `ingest` matched only
  `.py/.md/.txt/.rst`, so the files the chunker exists for were never offered
  to it — an OCaml docs directory of `.ml` samples was skipped whole. The
  pattern is derived from the chunker's own extension table now.
- **`ingest` cannot read the documentation most projects publish.** The web's
  docs are HTML, skipped whole rather than stripped and indexed. The ocaml.org
  tutorials only worked because they are markdown in their source repository.
- **An index is refused rather than half-trusted.** Vectors and chunk metadata
  are two files paired by row index, so a count mismatch used to inject
  documentation under a filename it never came from — no exception, right-
  looking score. `load` refuses on a count or shape mismatch, on a model other
  than the one that built the index, and on an unreadable file; `search`
  refuses a query of the wrong dimension. It cannot detect a merely STALE
  index — docs edited since the last `ingest` are answered from the old text.
- **Documentation names an API; it does not enumerate one.** Judging code
  against the extracted symbol library produced 45 findings on this project's
  own source, all of them correct code the docs had no reason to mention. What
  it can do is answer *did you mean* once the toolchain has already rejected a
  name, which needs no completeness — so `purecoder/symbols.py` deliberately
  has no function that takes code. It would not have helped either recorded
  OCaml failure. Its value is `ask` over a real library's docs, which is not
  the case that motivated writing it.
- **Retrieval reaches the code artifact of a project, and only that one.**
  Inside a scaffold the documentation goes to the execution-validated module
  alone. Folding the context into the description instead of passing it
  separately sent it to the README prompt too — caught by a test, not review.

### Bootstrap and learned languages

- **A learned language is proven, not trusted.** Five mechanical probes plus a
  live round decide it, and a harness that cannot fail wrong code is refused.
  What the probes cannot see is *idiom*: a spec can pass every one and still
  produce code no practitioner would write.
- **Bootstrap on this model is possible and unreliable.** Six `learn ocaml`
  runs against real stdlib `.mli` files failed four ways, three of them the
  bootstrap's own prompts: a model explanation compiled as source (`unfence`
  strips fences, not prose); a structural check with no retry, so one bad
  sample ended the run; a prompt demanding `PC_CHECK` when OCaml reserves
  capitals for constructors, so four redrafts failed an instruction that cannot
  be followed; and the model copying its own prompt into the fixture. Fixed,
  the harness went from three of five probes to five of five, then failed the
  live round on tester type errors. Two later runs got three of five again.
  Nothing registered in any of the six, which is the gate's whole claim.
- **OCaml is wired, and it was written by hand.** It was the language the
  bootstrap existed for. The entry is hand-written because the probes do not
  care who wrote a spec, and it passes the same five a learned entry must.
  `learn ocaml` is refused like `learn python`, so `go`/`java`/`swift` carry
  the placeholder tests, and an OCaml failure is an ordinary failure again.
  Harder algorithms still fail on writer competence — a live bubble sort
  produced a type error three attempts running.
- **A learned language is scaffoldable when its layout can be proven.** `learn`
  drafts a `ProjectSpec` and probes it two-sided against real `make`: correct
  code must build and run, unparseable code must fail. If it does not hold the
  language is registered without one — `project` refuses it, `code` and `ask`
  do not notice. Narrower than it sounds: for a one-file project `make test`
  builds and runs the file rather than running a suite. `make install` is never
  run, so it is trusted rather than proven.
- **The writer's demand is derived and exercised; nothing proves it was
  needed.** A drafted entry tells the writer what the harness already provides,
  from artifacts the probes proved. There is no two-sided probe of NECESSITY,
  and the obvious one does not work: pasting the tail into the implementation
  slot fails a top-level-statement language for the wrong reason, manufacturing
  a constraint for a language that needed none. Hand-written entries stay
  asymmetric on purpose — a built-in is empty because a person judged it
  unnecessary, a drafted one is filled because nobody judged anything. It is
  narrower than C#'s hand-written demand, which also forbids `using` directives
  — a fact about C#, not about `assemble()`. It applies only to languages
  learned from here on; re-running `learn` is the only way to backfill.

### Measurement

- **The contract measurement is built, has been run twice, and is
  inconclusive.** `bench.py` holds five deliberately ambiguous specs, each with
  a hidden hand-written oracle, run through both arms. Zero divergence in both
  arms, because nine of ten arms ended in the loop refusing — spec-divergence
  only exists downstream of a passing run. The instrument is sound and the task
  set is starved. Divergence is defined mechanically as *the loop reported
  success and the oracle disagreed*, with separate buckets for code the oracle
  cannot call (a `NameError` is not a misreading) and for runs that never
  finished. It deliberately does NOT measure visibility — whether a reader
  would have caught a wrong contract — because both proxies are worse than
  saying so: evaluating model-authored example expressions is the mistake that
  cost five false greens, and string-matching makes `[80, 443]` and `[80,443]`
  disagree for no reason. A contract grounds both roles, so a difference
  between arms cannot be attributed to either.
- **`purecoder measure` gave the first quantitative form of finding 1.** Eight
  of ten arms ended blaming the test designer.
- **The cross-language benchmark saturates as a capability instrument.**
  Corrected for harness failures, five of six languages score 10/10 and only
  OCaml discriminates. `roman` — the intended hardest, and the reason for a
  3/4/3 ramp — passed first attempt everywhere else. As a *pipeline* benchmark
  53/60 is a real result; as a way to compare models or catch a regression it
  cannot see. Also measured: pinning a task's edge cases in its spec text does
  not stop a tester contradicting them, because a spec is a prompt.
- **It also has run-to-run noise of at least ±1 per column.** Two passes on
  2026-08-21 moved OCaml 7/10 → 6/10 with nothing in that language touched
  between them. A single pair of runs therefore cannot show the absence of a
  regression, whatever the totals do — repeats are the only way to make a
  one-point claim from this instrument
  ([noise-and-the-oracle](live-runs/2026-08-21-noise-and-the-oracle.md)).
- **The classifier was repaired, and the fix had to be measured in both
  directions.** `benchlog.py` requires two things before blaming the model: the
  loop printed `suspecting the tests, redesigning them` AND a check actually
  ran. Keying on the marker alone was tried first and was the original defect
  with its sign flipped — it moved all seven failures out of `writer`,
  including three OCaml runs whose code genuinely did not compile. Re-run over
  the same sixty transcripts (2026-08-17, no server needed): 53 ok, and of the
  seven failures `writer` holds exactly the two that did not compile. Zero of
  the four harness failures remain in it. The bucket is `suspect-tests` rather
  than `tester` on purpose — a flag meaning *open this transcript*, not a
  verdict. One of the five flagged (`ocaml-unique`) was genuinely the model's:
  flagged rather than mislabelled, which is the honest limit.
- **Two runners disagree about whether OCaml may be measured ungrounded, and
  it is unresolved.** `batch.sh` exits rather than measure OCaml without its
  docs index, on the grounds that OCaml is the ignorant case retrieval exists
  for and an ungrounded column cannot be told apart from a regression. That
  guard predates the 30B, and `attribution.py` scored OCaml 9/10 ungrounded on
  2026-08-16 — so either the guard is stale or that column is not comparable to
  the grounded numbers elsewhere. Both tools are in the repo, both are used,
  and they cannot both be right.
- **The ledger is written from the inside, and that is why it is not a guess.**
  `agents.py` records what each role spent as the run spends it. It separates
  the two genuine refusals of the 2026-08-16 corpus run by role count alone —
  OCaml's `is_palindrome` (writer 4, tester 1, a real type error) from Python's
  `count_vowels` (writer 4, **tester 2**, a wrong expectation the no-progress
  rule already suspected). Both report `stopped on: writer`. The classifier now
  separates that pair too, but only the ledger can say *what a role spent*.
  The first `scripts/demo.sh` run produced the same shape: six of seven
  languages passed and SQL refused reporting `stopped on: writer`, with the
  writer's output `SELECT COALESCE(SUM(n), 0) AS total FROM totals` — correct,
  and exactly what the spec asked for. The tester's checks could not be
  satisfied, and the ledger recorded `tester 2/writer 4`. A reader shown only
  the stop would go and fix correct code, which is why every role is printed.
- **Deliberately not built: per-role retry budgets.** An earlier `Agent`
  declared one and did not enforce it, so the UI rendered "tester 2 of 3" while
  the real cap was 4. A denominator the numerator can exceed is decoration that
  reads like a guarantee. The cap the run actually had is recorded instead.

### Model, grammars and serving

- **A grammar that cannot end is a grammar that always truncates.** `env.gbnf`
  had an unbounded `line*` root; bounded to 20. `makefile.gbnf` had the
  identical defect and kept it for six more days, found by the first
  `scripts/smoke.sh` run: `purecoder make` wrote a correct Makefile, then
  `# Even more concise version:`, then another, each dying on `n_predict`. Two
  things carry forward. The bound must be tight enough that a COMPLETE file
  fits inside `n_predict` rather than merely being finite — at 24 items the
  same prompt still rambled, and only 14 stopped it. And a bound alone would
  not catch a restart that FITS, so `validate_makefile` gained the semantic
  half: a target defined twice is the file starting over, which `make` accepts
  with a warning and a zero exit.
- **A client-side flag can rot without a single test noticing.** Truncation
  detection read `stopped_limit`, which current llama.cpp no longer sends (it
  reports `stop_type: "limit"`), so every truncation retry was unreachable and
  a `.env` cut off mid-comment validated clean. The fake model sets the flag
  itself and the field only exists in a live response. The lesson is narrower
  than "test more": an adapter to someone else's API is where a contract test
  against a recorded real response earns its place, and there is still none.
- **A refused run exited 0.** `_print_result` printed `ok=False` and returned
  `None`, and `main()` ends `return ... or 0`. Only a refusal *before* the loop
  exited non-zero. Nothing downstream noticed because both bench scripts grep
  the verdict line out of the transcript instead. Found by writing a smoke test
  that tried to assert on `$?`.
- **A skip is a claim, and CI has a job where none is allowed.** `account()`
  puts every registered language in exactly one state — runnable,
  unimplemented, unvalidatable, or missing-toolchain **with the binary named**
  — deriving that name from the language's own argv so it cannot drift. The
  ad-hoc gate it replaced listed g++, node, rustc and ocamlc and *not* dotnet,
  so C# was the one language it could not have caught leaving. Outside the
  container job a skip is reported, not fatal: a machine without Docker has to
  keep working. The image is execution-only; generation stays on the host.
- **`-hf` is the wrong start command once weights are on disk.** It resolves
  against the HuggingFace cache alone, so a 14.7 GB GGUF in `~/models` is
  invisible and gets downloaded again — fifty minutes of it before anyone
  checked, with the file already present and the server nine seconds away from
  `-m`. Nothing was broken, which is the point: a status line naming a repo can
  cost an hour where one naming a path cannot. `status.py` looks in `~/models`
  and the llama.cpp cache and prints `-m <path>` when it finds the documented
  file. `scripts/smoke.sh` prints the same line.
- **Only `/completion` accepts raw GBNF**, so `client.py` applies ChatML by
  hand. GGUF chat-template metadata never reaches the sampler — re-downloading
  weights to fix a template does nothing.

### Untested seams

- The live `/completion` call and the live embedding call. `/completion` has
  been exercised by hand end to end, including grammar-constrained contract
  derivation; the embedding call has not. Neither is in CI. A CI-able check
  does guard the class of grammar bug that broke `contract.gbnf` (no rule may
  span lines).
- The UI has no component test harness: `tsc --noEmit` plus a build is the
  whole check. See [UI/README.md](../UI/README.md).

## Live runs

Every session against a live server has found defects a fully green suite could
not. The verdict is not the evidence — the transcript is.

| Date | Found | Write-up |
|---|---|---|
| 2026-08-03 | 5 defects in the bootstrap layer, all fixed | [ocaml-bootstrap](live-runs/2026-08-03-ocaml-bootstrap.md) |
| 2026-08-04 | 3: SQL's writer never told the database starts empty (`writer_system` did not fix it; the mechanical hint on the failed run did); Python's tester never told to keep assertions out of an uncalled function; the chunker lost every declaration in a real OCaml `.mli` because `val` parses as `value_specification` and the suffix list said `_specifier` | [five-steps](live-runs/2026-08-04-five-steps.md) |
| 2026-08-06 | test-first mode and the model comparison, including a contract's own example (`parse_ports('80,443') -> [443, 80]`, for a spec saying sorted) caught on screen before any implementation existed | [tdd-and-the-model-question](live-runs/2026-08-06-tdd-and-the-model-question.md) |
| 2026-08-07 | 8, all failing CORRECT code: a capitalised `Let`; a gate refusing valid doubly-parenthesised OCaml; a repair mangling an unlabelled check; "these tests never call the target" unreachable outside Python; one target mention enough to pass it; the writer answering retrieved docs instead of using them; the tester doing the same; a `[docs]` hint printing its header and withholding the names | [the-harness-was-the-bottleneck](live-runs/2026-08-07-the-harness-was-the-bottleneck.md) |
| 2026-08-09 | 6, and the suites could not pass: `unique([]) === []` is false in JavaScript for every input, `==` on `List<int>` is reference equality in C#, and a Python suite asserted `count_vowels(...) == 10` where the answer is 9 | [the-tests-could-not-pass](live-runs/2026-08-09-the-tests-could-not-pass.md) |
| 2026-08-15 | `makefile.gbnf`'s unbounded root, and a refused run exiting 0 | first `smoke.sh` run |
| 2026-08-16 | SQL's 0/10 was the TASK SET: every corpus task asks for a function and SQLite has no user-defined functions, so no attempt could have passed. The runner refuses SQL against this corpus by name now. Fourth time a number here turned out to be about the harness or the task set rather than the model, and the first the ledger made visible without opening a log | `attribution.py` |
| 2026-08-17 | the ledger's own output: a reason containing a newline broke the roles block into a row with no role attached. Same defect in the CLI and the UI, found by driving an SQL refusal | smoke 6/6, demo 6+1 |
| 2026-08-21 | 4 harness defects, all failing CORRECT code: a C++ `PC_CHECK` macro that could not take an argument containing a comma, and a braced list that is not an expression (both in one compile of `unique`); the JavaScript and C# container repairs anchored on one operand order only. One attempted fix WITHDRAWN for refusing a valid suite. The instrument moved OCaml a point with nothing in that language touched, so it carries at least ±1 per column | [noise-and-the-oracle](live-runs/2026-08-21-noise-and-the-oracle.md) |

## Next steps (priority order)

The first two entries here were the diagnostics loss and the classifier's
misattribution. Both are done -- `c201f19` and `9e81587` -- and the entries
above record what each was measured at. They are named here because a list that
silently drops its top two items reads as if nothing moved.

1. **Re-pitch the corpus, now that there is data.** Five of six languages score
   10/10 corrected, and the 2026-08-16 attribution run put 50 of 58 passes on
   the first attempt. The 3/4/3 difficulty ramp was judgement with nothing
   behind it and the top of it is not a top: `roman` passed first try
   everywhere but OCaml. The two prerequisites this was waiting on -- a
   classifier that does not blame the wrong component, and a fix loop that is
   shown the diagnostics -- are both in, so the reason to defer it is gone.
   The design question is what a harder task should be *for*: the set is a
   pipeline instrument, and a task nobody's writer can pass measures the model
   instead, which is the confound this directory keeps rediscovering.
2. **Make the contract measurement conclusive.** The instrument is built and
   has been RUN -- twice -- and it could not see what it exists to see: zero
   divergence in both arms, because nine of ten arms ended in the loop
   refusing. Spec-divergence only exists downstream of a passing run, so a
   model that fails closed on these specs starves the measurement. It needs a
   larger or easier task set, more repeats, or a stronger model, and the
   choice between those is a real decision rather than a chore.
3. **The tester, which every measurement now points at.** Eight of ten
   measured arms ended in "suspecting the tests". Two of today's three
   mechanical wins were tester-shaped (`test_lint`, `test_fix`), and test-first
   proves a suite CAN fail without judging whether its expectations are right.
   This is where the next real gain is, and it is not a model problem: a
   `--tdd` suite is confirmed by a human precisely because nothing mechanical
   can check an expectation.

   Six of the nine defects found on 2026-08-07 were in this same component,
   and the largest was that the test designer had been handed the retrieved
   documentation along with the request -- so it wrote tests about the docs.
   The remaining known gap is narrower than it was: the gate can now tell that
   a suite is aimed at the target, in any language, but still not that its
   expectations are right.
4. **HTML in `ingest`.** It matches prose and source extensions, and the web's
   documentation is HTML -- skipped whole rather than stripped and indexed. The
   ocaml.org tutorials only worked because they are markdown in their source
   repository, which is not how most projects publish.
5. **A per-run venv, if the declaration ever needs to install anything.**
   `--with` covers what the environment already has; anything else is still a
   manual `pip install`. Doing it automatically means network access inside a
   run and a failure mode CI cannot exercise, which is why it was left out
   rather than half-built.
6. **The branch stack is gone and is not coming back.** `main` carries the lot
   as of 2026-08-09 (PR #19, 113 commits). New work is a branch off `main` and
   one PR against it. Fifteen stacked branches bought reviewability nobody used
   and cost a stale-ref conflict, a rebase of twelve branches, a PR GitHub
   refused to retarget, and a merge order that silently dropped 11,000 lines.
   The operative rules are in CLAUDE.md: `git fetch` before believing anything
   about `main`, merge bottom-up by base, and a rebase collides with ignored
   files too.
7. **Specialization track** -- prune + vocab-trim Qwen2.5-Coder to reclaim
   context room on 6 GB (Flab-Pruner-style), the "make it custom" phase.
   **Planned, not built**, and the plan says why:
   [docs/superpowers/specs/2026-08-04-specialization-plan.md](superpowers/specs/2026-08-04-specialization-plan.md).
   It needs the fp16 checkpoint (~15 GB) and GPU hours this machine does not
   have. Worth reading beside finding 9: a quantisation step down already cost
   more capability than its speed bought back, which is the same trade pruning
   proposes at a larger scale, and the plan's go/no-go bars exist for exactly
   that reason.

   The 30B result changes the case for it. The context room this track exists
   to reclaim was reclaimed for free by a mixture-of-experts model -- 1.9 GB of
   VRAM against the 7B's 4.7 -- so the scarcity that motivated pruning is no
   longer the binding one. Worth revisiting before any GPU hours are spent.
