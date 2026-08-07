# PureCoder — Build Status

_Snapshot of what's built, tested, and what's next._

## Done and tested

558 tests, all green, none of them needing a GPU or a running server
(`pytest -q`). CI runs the same suite on Python 3.10–3.12.

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
| 9 | `bench.py` contract measurement | ✅ built, ⬜ unrun | instrument calibrated hermetically; no live numbers collected yet |
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

## Key findings from the build

1. **The writer is stronger than the tester.** Same model, same spec →
   correct code, wrong tests, repeatedly. Test generation is the weak link
   and the best target for specialization.
2. **Almost every failure was spec ambiguity or test quality**, never the
   writer. That's where remaining effort belongs.
3. **Lenient tools rubber-stamp garbage.** `make -n` passed 50-line `rm`
   spirals; semantic guards were required on top of the parse check.
4. **Context is double-edged** on a small card — feeding full code forward
   for coherence triggered degeneration. Minimal context per task is the rule.
5. **Real hardware is 6 GB, not the assumed 12** — shaped every choice and
   strengthens the case for the pruning/specialization track.
6. **Mechanically-generated tests are not automatically trustworthy.** The
   anchor generator turns contract examples into assertions with no model
   involved — and still produced five separate false greens before it was
   safe, none of which a passing suite revealed. All five came from embedding
   model-authored *expressions* into generated code: an unparenthesized
   expected value made `assert f(10,3) == 3, 1` assert only `== 3`; a `#`
   truncated an assertion to a bare truthiness check; a walrus rebound an
   exception name so the handler caught `BaseException`; `out: "f(1)"` emitted
   a tautology. The rule that ended it was structural, not another patch:
   **an anchor may embed data, never behaviour** — every value must be a
   literal. Worth remembering that the contract's author is the same model the
   anchors exist to check.
7. **A live run finds what unit tests cannot.** Two defects survived 147
   passing tests and six clean reviews: `contract.gbnf` did not parse in
   llama.cpp at all (multi-line rules are rejected — every contract run
   silently fell back), and the test designer can wrap its assertions in a
   `def test_x():` that nothing ever calls, so `run_python` exits 0 and the
   loop reports success on an implementation returning garbage. The second is
   a false green in the project's central claim and pre-dates the contract
   work entirely. Eight scaffold runs against a live server produced five more
   defects no test had thought to look for: the writer copying the tests it
   was shown into the implementation (they then ran twice), the gate's verdict
   being discarded when it gave up, a missing import burning the whole retry
   budget, a mid-loop constraint being dropped by the next prompt rebuild, and
   generated code orphaning spawned processes past the timeout.
8. **Constrain at the cheapest layer that can express it.** The `.env`
   rambling comment was attacked three ways: a system prompt (ignored), then a
   semantic validator (worked, but cost a model call per retry and still
   failed three attempts in a row). Bounding the line length *in the grammar*
   made it structurally impossible and free. Line length is shape, and shape
   is the grammar's job — the validator should never have been the first
   answer. It is kept, at a looser bound, for hand-written files and as
   defence in depth.

## Known boundaries (by design, documented)

- **Test-first proves the tests can fail, not that they are right.** `--tdd`
  runs the designed suite against a stub -- a function that exists and returns
  None -- and refuses it unless a check ran AND failed. That kills the whole
  class of suite no static gate can see (`assert True` parses, names the
  target, is not degenerate). What it cannot do is judge the EXPECTATION: a
  live run produced `assert word_count(' ') == 1` against a correct
  implementation, and a red suite full of wrong expectations is red for the
  wrong reason. The confirmation step exists for exactly that gap -- it is the
  one moment a person can read what will judge the code, before the code
  exists -- and `-y` skips it, which is a choice with a cost.
- **Test-first is Python only.** A stub needs a real signature in C++, Rust or
  OCaml, which the contract does not supply; there the empty-implementation run
  is a compile error, which is not evidence about assertions. Refused with the
  reason rather than approximated.

- Test gate catches *structural* bad tests, not plausible-but-wrong values.
- Assertion *reachability* is proven at runtime, not by the gate. The tests
  are instrumented to count checks that actually execute, so a suite whose
  asserts sit in a `def test_x():` nobody calls now fails with "no checks
  ran". The static gate still cannot see it -- the proof is the run.
- A wrong contract now fails *correct* code rather than silently passing wrong
  code. The first live contract run produced `parse_ports('80,443') ->
  [443, 80]` for a spec that said "sorted", and the anchor faithfully failed a
  correct implementation. Noisy-wrong beats silent-wrong, but it is not free.
- **Execution validation reaches seven languages, still only run-to-completion
  code.** Python, C++, JavaScript, Rust, C#, SQL and OCaml all compile (where
  needed), run real assertions, and prove a check executed. Go, Java and Swift
  are declared and refuse until both a toolchain and a test idiom exist. Power
  Query is refused permanently -- it runs only inside Excel and Power BI.
- **SQL is validated, but its harness lives in two places.** SQL has no
  assertion and no reliable way to end a script non-zero: SQLite's `RAISE`
  works only inside a trigger and takes a *literal*, so a failing check cannot
  name itself from inside SQL, and `SELECT 1/0` returns NULL rather than
  failing. So a check is a ROW -- a boolean and a label inserted into a table
  the preamble creates -- and the verdict is read back by the driver, which is
  a stdlib `sqlite3` one-liner rather than a neutral interpreter. That is a real
  asymmetry with the other five, where the tail carries the proof, and it is
  defensible only because this driver is ours: the invariant a test now enforces
  is that the *spec* proves a check ran, in the tail or in the runner. What SQL
  gets in exchange is that every failing check is reported, not just the first.
  There is no project layout -- a Makefile recipe would have to reproduce the
  driver -- so `project --lang sql` refuses and `code` is unaffected.
- **The sandbox has whatever this environment has, and now says which.** The
  earlier text here claimed the sandbox had no third-party packages at all.
  That was wrong, and a one-line probe disproves it: the executor runs
  `sys.executable`, so it inherits the venv PureCoder itself runs in, where
  `import numpy` succeeds and validates. What was true is that nothing
  *declared* or *verified* anything -- the pipeline discovered a package's
  absence by failing three attempts deep. `code --with numpy` now declares it,
  the import is probed in the interpreter the executor will really use before a
  single model call, and a package that is missing is refused with the exact
  `pip install` line. The permission goes into the shared task text so the test
  designer gets it too -- a writer allowed numpy and a tester that is not
  produces assertions that cannot run -- and the stdlib-only nudge no longer
  takes the permission back. What is deliberately NOT built: a per-run venv
  that pip-installs on demand. It needs the network, CI cannot exercise it, and
  a flaky install mid-run is a "generated but unchecked" tier by another name.
  `--with` is python-only and refuses for any other language rather than being
  silently ignored.
- **Two live limits that are still real**, both confirmed repeatedly against a
  live server on a "small web app that graphs random numbers" spec: a server
  that calls `serve_forever()` never returns, so the timeout is the only
  possible verdict; and successive binds hit `TIME_WAIT`, so even a stdlib
  `http.server` answer fails to rebind on the next attempt. A missing import
  triggers one stdlib-only retry and then stops, rather than burning the whole
  budget. Function-shaped specs pass on the first attempt.
- **A learned language is proven, not trusted.** Five mechanical probes plus a
  live round decide it, and a harness that cannot fail wrong code is refused.
  What the probes cannot see is *idiom*: a spec can pass every one and still
  produce code no practitioner of that language would write.
- The first live run of the bootstrap layer and its five defects, all now
  fixed, are written up in
  [docs/live-runs/2026-08-03-ocaml-bootstrap.md](live-runs/2026-08-03-ocaml-bootstrap.md).
- The test-first mode and the model comparison are written up in
  [docs/live-runs/2026-08-06-tdd-and-the-model-question.md](live-runs/2026-08-06-tdd-and-the-model-question.md),
  including the run where a contract's own example (`parse_ports('80,443') ->
  [443, 80]`, unsorted, for a spec that says sorted) was caught on screen
  before any implementation existed.
- **A second live run, over everything built since, found three more** --
  [docs/live-runs/2026-08-04-five-steps.md](live-runs/2026-08-04-five-steps.md).
  SQL's writer was never told the database starts empty (and saying so in
  `writer_system` did not fix it -- the mechanical hint on the failed run did);
  Python's tester, alone among the five, was never told to keep its assertions
  out of a function nobody calls; and the tree-sitter chunker lost every
  declaration in a real OCaml `.mli`, because `val` parses as
  `value_specification` and the suffix list said `_specifier`. All three were
  invisible to 450 passing tests and were found by pointing the pipeline at
  real input. That run used Q5_K_M at 20/29 layers, where the older findings
  above were made on Q4_K_M -- a mismatch at the time, and since resolved the
  other way: Q5_K_M is now the documented model, because Q4 was measured and
  rejected (finding 9).
- **A third live run found eight, and they were failing correct code** --
  [docs/live-runs/2026-08-07-the-harness-was-the-bottleneck.md](live-runs/2026-08-07-the-harness-was-the-bottleneck.md).
  A capitalised `Let` at the head of a statement; a gate anchored so that
  valid doubly-parenthesised OCaml was refused; a repair that mangled a check
  written with no label; "these tests never call the target" unreachable
  outside Python, so a suite testing `List.sort` instead of `insertion_sort`
  was accepted; one target mention enough to pass that check; the writer
  answering retrieved documentation instead of using it; the test designer
  doing the same, which was the other half of it; and a `[docs]` hint that
  printed its header and withheld the names. All nine were invisible to 536
  passing tests, and every one was found by reading a failing run's transcript
  rather than its verdict. The benchmark that found them is versioned now, at
  [scripts/bench/](../scripts/bench/), transcripts and all -- an earlier
  version discarded them and a batch of correct implementations was nearly
  recorded as a capability result.
- **The bootstrap's own prompts were three of its four failure modes.** Six
  `learn ocaml` runs against real stdlib `.mli` files produced: a model
  explanation compiled as source (`unfence` strips fences, not prose); a
  structural command check with no retry, where one bad sample ended the run;
  a prompt demanding `PC_CHECK` when OCaml reserves capitalised identifiers for
  constructors, so four redrafts failed an instruction that cannot be followed;
  and the model copying a line of its own prompt into the fixture. Fixed, the
  harness went from three of five probes -- failing identically across four
  redrafts -- to five of five, then failed the live round on tester type
  errors, which is the older documented boundary. Two later runs got three of
  five again: **bootstrap on this model is possible and unreliable, where
  before it was impossible.** Nothing registered in any of the six runs, which
  is the gate's whole claim.
- **`purecoder measure` has been run, and it could not measure what it exists
  to measure.** Zero divergence in both arms across two full passes; nine of
  ten arms ended in the loop refusing, eight of those blaming the test
  designer. Spec-divergence only exists downstream of a passing run, so a
  pipeline that fails closed on these specs gives the instrument nothing to
  see. The first pass was also confounded by the tester-wrapping defect above
  and was discarded rather than published. What the run does give is the first
  quantitative form of finding 1: the tester is the bottleneck, in eight of ten
  arms.
- **OCaml is wired, and it was written by hand.** It was the language the
  bootstrap layer existed for, and six live `learn` runs never registered one:
  the drafting model wrote `let PC_CHECK cond =` (OCaml reserves capitals for
  constructors), explained its code in English inside the source, echoed the
  prompt back, glued an extension onto `{src}`, and finally produced `end
  else`. Every one of those is fixed where it belongs -- and the entry is still
  hand-written, because the probes do not care who wrote a spec and a language
  nobody can generate for is worth less than an hour of typing. It passes the
  same five bootstrap probes a learned entry must, which a test asserts, and
  `code --lang ocaml` validated on the first attempt live. Two consequences:
  `learn ocaml` is now refused like `learn python` (a wired entry is reserved,
  so `go`/`java`/`swift` carry the placeholder tests), and an OCaml failure is
  an ordinary failure again rather than an expected limit. Harder algorithms
  still fail on writer competence -- a live bubble sort produced an OCaml type
  error three attempts running -- which is the boundary every language has.
- **Retrieval reaches the code artifact of a project, and only that one.**
  `code`, `ask` and `project` share one resolver, so an explicit `--store` or a
  learned language's own index grounds all three. Inside a scaffold the
  documentation goes to the execution-validated module alone: the Makefile's
  targets come from `ProjectSpec`, the `.env` is derived from the code it is
  shown, and the README is prose. Folding the context into the description
  instead of passing it separately sent it to the README prompt too -- caught
  by a test, not by review.
- **A learned language is now scaffoldable, when its layout can be proven.**
  `learn` drafts a `ProjectSpec` and probes it two-sided against a real `make`:
  correct code must build and run, code that cannot parse must fail. If it does
  not hold, the language is still registered without one -- `project` refuses
  it, `code` and `ask` do not notice. What the probe proves is narrower than it
  sounds: for a one-file project `make test` builds and runs the file rather
  than running a suite, exactly as the hand-written C++ and JavaScript entries
  do. `make install` is never run, so it is trusted rather than proven.
  A learned language now gets a `writer_system`, but see the boundary below for
  what that is and is not evidence of.
- **The writer's demand is derived and exercised; nothing proves it was
  needed.** A drafted entry now tells the writer that the file already defines
  the check helper and either supplies the entry point or runs at top level, and
  that it should add no wrapper. Both facts come from artifacts the probes
  proved, and the live bubble-sort round exercises the demand end to end -- but
  that round only shows the writer can work *with* it. There is no mechanical
  two-sided probe of *necessity* here, and the obvious one does not work:
  pasting the tail into the implementation slot to see whether duplication
  breaks the build fails a top-level-statement language for the wrong reason
  (two tails, so the counter check runs before the tests, so "no checks ran"),
  which would manufacture a constraint for a language that needed none. The
  hand-written entries stay asymmetric on purpose -- a built-in is empty because
  a person judged it unnecessary; a drafted one is filled because nobody judged
  anything. Two further limits. It is *narrower than C#'s hand-written demand*,
  which also forbids `using` directives: that is a fact about C#, not about
  `assemble()`, and a derived demand that banned imports outright would break a
  language whose implementation legitimately needs one. And it applies to
  languages learned from here on -- an entry already saved under
  `$PURECODER_HOME` keeps the field empty, since filling it needs the fixture,
  which is not stored. Re-running `learn` is the only way to backfill.
- **A collision is explained after the fact, not prevented by a gate.** When a
  failed attempt defines something the harness already provides, the retry
  prompt names it -- the toolchain reports that as "multiple definition of
  `main'" in a file the writer has never seen. The check is textual (it runs for
  languages with no parser here), so it can miss, and it is deliberately only a
  hint on an already-failed run: a false positive would cost a line of context
  and point the model at code that is fine.
- **Drafted commands reach the machine two ways, and they are not equally
  narrow.** Build and run are argv, and shell syntax is refused outright. A
  project recipe cannot be argv -- `g++ ... && ./main` needs `&&` -- so it is a
  shell line with the shell's other powers denied by name (pipes, redirection,
  `;`, command substitution, a backgrounding `&`), and `run`/`test` must name
  the entry file. The denylist is calibrated against the five hand-written
  layouts and a test keeps it there. The user confirms both before anything
  runs, and `make install` is never run at all. That is a closed door, not a
  sandbox — the executor's isolation is still a temp dir and a process group.
- **Code is chunked by its own grammar now, where one is installed.** The
  chunker was Python-and-markdown only, so a learned language's samples were
  cut at paragraph boundaries -- worst exactly where this project cares most.
  tree-sitter splits C++, Rust, OCaml, JavaScript, C#, Go, Java, SQL and the
  rest by definition instead, with the same shape the Python AST chunker
  already used: one chunk per definition, a large class split into members, the
  comment above a definition kept with it, everything else in a preamble. Two
  limits worth stating. The node types are matched by SUFFIX (`_definition`,
  `_item`, `_binding`) rather than a per-language table, so an unusual grammar
  may drop a definition into the preamble rather than naming it -- degraded, not
  wrong. And the name on a chunk's label is found by a bounded breadth-first
  walk, so a grammar that nests its identifier deeper than four levels yields a
  chunk labelled by node type. The package is an optional extra; without it,
  code degrades to prose chunking exactly as before.
- **A capability and its wiring are two things.** `ingest` matched only
  `.py/.md/.txt/.rst`, so the files the new chunker exists for were never
  offered to it -- an OCaml docs directory of `.ml` samples was skipped whole.
  The pattern is now derived from the chunker's own extension table, which is
  the third time this session that a working feature was reachable by nothing.
- **RAG only helps where the model is ignorant, and it can actively hurt.**
  Measured: `sum_list` in OCaml passed on the first attempt ungrounded and
  failed four attempts with the docs index attached -- the retrieved tutorial
  text diluted the prompt until the tester reverted to a malformation the
  prompt's counter-example had been suppressing. That is the bill for a gate
  that never refuses, and it is why the constraint moved to `test_lint` and
  `test_fix`, which hold regardless of how much context is in the prompt.
- **The project now edits model-authored test code, in one narrow place.**
  `test_fix` rewrites `pc_check ((expr) "label")` to the form that compiles.
  The justification is that the construct applies a string to a boolean and
  cannot compile under any reading, so it has exactly one possible intent and
  the rewrite is meaning-preserving by construction. It is declared per
  language, tested from both sides, and `test_lint` stays behind it for shapes
  the repair does not match. Refusing was tried first and ended runs at
  attempts=0 with the writer never reached. Nothing here infers intent -- a
  malformation with two possible meanings does not belong in this field.
- **The gate refuses again, and the reason it could not was a bug rather than
  a threshold.** It was recorded here first as a boundary about embedding
  floors -- that was wrong, and worth leaving on the record. The lexical score
  is the share of a query's rare tokens a chunk holds, and tokens the corpus
  had never seen were dropped from the denominator instead of counted against
  the match, so a chunk matching ONE incidental token scored as well as one
  matching every token: `cheapest flights to Lisbon in March` scored a perfect
  1.000 against OCaml documentation because "march" appears somewhere. With
  unseen tokens weighted at the rarest known weight, unrelated queries fall to
  0.50-0.70 while real ones stay at 1.10-1.34 -- ranges that used to overlap,
  which is exactly why raising the threshold had looked like corpus-fitting.
  The default is now 0.8, set nearer the junk end because a too-tight gate
  drops documentation silently. `min_lexical` preserves the exact-symbol
  rescue at cosine zero, which is only safe now that an unrelated question can
  no longer score 1.0 lexical. Live: 7 of 7 real questions retrieve, 0 of 5
  unrelated ones do. The threshold itself remains a judgement calibrated on one
  corpus and eleven queries; the separation it exploits is the measured part.
- **`ingest` cannot read the documentation most projects publish.** It matches
  prose and source extensions; the web's docs are HTML, which is skipped whole
  rather than stripped and indexed. The ocaml.org tutorials used to test
  retrieval are markdown in their source repository, which is the only reason
  that test was possible.
- **Ranking is two signals, and the gate is shared.** Cosine plus an
  IDF-weighted exact-name score, the latter bounded in `[0,1]` absolutely so it
  can share `min_score`. The consequence is deliberate: a chunk containing
  every rare token of the query clears the gate on the lexical signal alone,
  with a cosine of zero. A token in every chunk weighs nothing, so stopwords
  cannot do it. The weight (0.5) and the threshold (0.3) are the two numbers
  that decide this, and neither is tuned against a benchmark — there isn't one.
- **Retrieval cost is the model call, not the search.** Over 7495 chunks --
  this repo with `llama.cpp/` in it, which is a deliberately pathological
  corpus, not a docs directory -- the lexical signal is ~0.005 ms (inverted
  index), cosine ~3 ms (brute-force matmul), and loading an index ~220 ms. The
  embedding of the query dominates all of it. Worth being plain about the
  scale: a real docs directory is the OCaml case at 15 chunks, where the old
  per-chunk walk cost microseconds and the inverted index buys nothing
  measurable. It is headroom for a large corpus, not a fix for something
  anyone was waiting on. Brute-force cosine stays for the same reason: an ANN
  index would trade exactness and a dependency for milliseconds nobody needs.
- **Documentation names an API; it does not enumerate one.** The symbol library
  extracted from the docs cannot decide that a name is wrong — judging code
  against it produced 45 findings on this project's own source, all of them
  correct code the docs had no reason to mention. What it can do is answer *did
  you mean* once the toolchain has already rejected a name, which needs no
  completeness. That inversion is the whole design of `purecoder/symbols.py`,
  and there is deliberately no function in it that takes code. It would not
  have helped either recorded OCaml failure — both were `Syntax error` from the
  tester's output, and the one name error was `Unbound value pc_tests`, a
  harness name no documentation contains. Its value is `ask` over a real
  library's docs, which is not the case that motivated writing it.
- **Measured, and the result is weaker than the motivation.** Run over this
  repo (7128 chunks, real bge-small embeddings) on six queries — five exact
  symbols and one prose control — the lexical signal changed the ranking in
  four, but it never *rescued* a query: cosine already put a chunk containing
  the symbol first, and every query cleared the gate on cosine alone. So the
  guarantee this buys ("the page defining the symbol cannot be ranked out by a
  page merely about it") is real, but on this corpus it was not yet needed.
  Related, and more interesting: bge-small's cosine floor is high enough that
  `min_score=0.3` almost never fires. The gate is looser in practice than the
  number suggests.
- Chunker is Python-only (stdlib `ast`); other languages need tree-sitter.
- **An index is refused rather than half-trusted.** The vectors and the chunk
  metadata are two files, and `search` pairs them by row index, so a count
  mismatch used to inject documentation under a filename it never came from —
  no exception, right-looking score. `load` now refuses on a count or shape
  mismatch, on a model other than the one that built the index, and on a file
  it cannot read; `search` refuses a query of the wrong dimension. What it
  cannot detect is an index that is merely *stale* — docs edited since the last
  `ingest` are still answered from the old text.
- **The contract layer has been measured, and the measurement could not see
  what it exists to see.** `bench.py` holds five deliberately ambiguous specs,
  each with a hidden hand-written oracle encoding the intended reading, and
  runs every task through both arms (`--contract` on and off). Run twice
  against a live server it returned ZERO divergence in both arms, because nine
  of ten arms ended in the loop refusing -- spec-divergence can only occur
  downstream of a passing run. The instrument is sound and the task set is
  starved: that is a result about this model and these specs, not about
  contracts, and the next step names the three ways out. Spec-divergence is defined mechanically as *the
  loop reported success and the oracle disagreed*, with a separate bucket for
  code the oracle cannot call at all (a `NameError` is not a misreading) and
  another for tasks the loop never finished (a dead server must not read as a
  clean run). What it deliberately does NOT measure is visibility -- whether a
  reader would have caught a wrong contract printed above the code. That needs
  a person, and the two available proxies are both worse than saying so:
  evaluating the contract's model-authored example expressions is the mistake
  that cost this project five false greens and a deleted subsystem, and string-
  matching them makes `[80, 443]` and `[80,443]` disagree for no reason. One
  further limit is in the report itself: a contract grounds the writer AND the
  test designer, so a difference between arms cannot be attributed to either.
- **A client-side flag can rot without a single test noticing.** Truncation
  detection read `stopped_limit`, which current llama.cpp no longer sends (it
  reports `stop_type: "limit"`), so every truncation retry in the project was
  unreachable and a `.env` cut off mid-comment validated clean. Nothing in the
  suite could see it: the fake model sets the flag itself, and the field only
  exists in a live response. The lesson is narrower than "test more" -- an
  adapter to someone else's API is exactly where a contract test against a
  recorded real response earns its place, and there is still none.
- **A grammar that cannot end is a grammar that always truncates.** `env.gbnf`
  had an unbounded `line*` root, so a scaffold's `.env` hit `n_predict` on
  every attempt once truncation was detectable again. Bounded to 20 lines. The
  same fix as the rambling comment, for the same reason: the prompt asking for
  brevity was ignored three times in a row.
- Two seams are outside the automated suite: the live `/completion` call and
  the live embedding call. `/completion` has now been exercised by hand end to
  end, including grammar-constrained contract derivation; the embedding call
  has not. Neither is exercised by CI. A CI-able check does guard the class of
  grammar bug that broke `contract.gbnf` (no rule may span lines).

## Next steps (priority order)

1. **Make the contract measurement conclusive.** The instrument is built and
   has been RUN -- twice -- and it could not see what it exists to see: zero
   divergence in both arms, because nine of ten arms ended in the loop
   refusing. Spec-divergence only exists downstream of a passing run, so a
   model that fails closed on these specs starves the measurement. It needs a
   larger or easier task set, more repeats, or a stronger model, and the
   choice between those is a real decision rather than a chore.
2. **The tester, which every measurement now points at.** Eight of ten
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
3. **HTML in `ingest`.** It matches prose and source extensions, and the web's
   documentation is HTML -- skipped whole rather than stripped and indexed. The
   ocaml.org tutorials only worked because they are markdown in their source
   repository, which is not how most projects publish.
4. **A per-run venv, if the declaration ever needs to install anything.**
   `--with` covers what the environment already has; anything else is still a
   manual `pip install`. Doing it automatically means network access inside a
   run and a failure mode CI cannot exercise, which is why it was left out
   rather than half-built.
5. **Merge the branch stack.** Fourteen branches, `chore/harden-ci` through
   `feat/13-tdd`, 136 commits, strictly linear -- each one contains the last,
   verified link by link. Nothing is on `main`. They are open as a stacked
   chain of PRs, each based on its predecessor so a reviewer sees only that
   branch's own commits; they must merge bottom-up, and each merge retargets
   the next onto `main` automatically. `feat/13` alone would carry the lot in
   one merge, at the cost of any reviewability.
6. **Specialization track** -- prune + vocab-trim Qwen2.5-Coder to reclaim
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

Done since the last snapshot: the reachability false green (runtime check
instrumentation), the `.env` guard (grammar bound plus a looser validator),
test leakage into the implementation (`lint_implementation`), the discarded
gate verdict, dependency thrash (one stdlib retry then stop), and sandbox
process-group cleanup.

Done in the retrieval pass after that: an index that could pair a chunk with
another chunk's source is now refused rather than answered from; the gate can
no longer inject a heading with no documentation under it; `ingest` prunes
caches and shows what it will index before embedding anything; ranking gained
an exact-name signal; the lexical index was inverted (400-500x on a large
corpus); and a learned language keeps the docs it was learned from.

Done in the bootstrap pass after that: a learned language now tells its writer
what its harness already provides -- derived from the helper name and the shape
of the drafted tail, never asked of the model -- and a failed attempt that
collides with the harness anyway is told which name collided instead of being
handed a linker error about a file it never saw.

Done since, in one long live session: SQL wired (a check is a row, since SQL
has no assertion); the contract measurement built and run; declared packages
(`--with`) verified before any model call; tree-sitter chunking, and then the
`ingest` pattern that had been skipping every file it exists for; OCaml wired
BY HAND after six drafting attempts failed, and held to the same five probes a
learned entry must pass; truncation detection repaired after llama.cpp renamed
the field it read, which had made every truncation retry unreachable; the
retrieval gate repaired -- it could not refuse because unknown query tokens
were dropped from the lexical denominator, not because the threshold was wrong;
the fix loop taught to show the writer its own previous output and the source a
diagnostic points at; test-first mode; and the context window measured up from
4k to 16k, with Q4_K_M weights tried and rejected on capability.

Done in the session after that: nine harness defects that were failing correct
code, and the model question settled. Qwen3-Coder-30B-A3B at Q3_K_M runs on
this 6 GB card with `-ngl 99 --cpu-moe` -- experts in system RAM, ~3B activated
per token -- in **1864 MiB at 33.3 tok/s**, which is less VRAM and more speed
than the 7B it replaces. It passes all five OCaml tasks at one attempt each.
But so does Q5_K_M on the fixed harness, having scored 4, 3, 4, 3 on the broken
one: the model was never the bottleneck, and the 30B's case rests on halving
the attempts, not on the score.
