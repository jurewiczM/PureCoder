# PureCoder — Build Status

_Snapshot of what's built, tested, and what's next._

## Done and tested

450 tests, all green, none of them needing a GPU or a running server
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

- Test gate catches *structural* bad tests, not plausible-but-wrong values.
- Assertion *reachability* is proven at runtime, not by the gate. The tests
  are instrumented to count checks that actually execute, so a suite whose
  asserts sit in a `def test_x():` nobody calls now fails with "no checks
  ran". The static gate still cannot see it -- the proof is the run.
- A wrong contract now fails *correct* code rather than silently passing wrong
  code. The first live contract run produced `parse_ports('80,443') ->
  [443, 80]` for a spec that said "sorted", and the anchor faithfully failed a
  correct implementation. Noisy-wrong beats silent-wrong, but it is not free.
- **Execution validation reaches six languages, still only run-to-completion
  code.** Python, C++, JavaScript, Rust, C# and SQL all compile (where needed),
  run real assertions, and prove a check executed. Go, Java, Swift and OCaml are
  declared and refuse until both a toolchain and a test idiom exist. Power
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
- **A learned language is only as good as its tester.** OCaml now registers on
  the first attempt with all five probes green, but generating *with* it stalls
  on test quality: the writer produced correct OCaml every time while the
  tester produced source the compiler rejected. The loop refuses honestly
  rather than emitting it. That is the expected outcome, not an open defect --
  `ocaml` is a placeholder entry (no runner, no test idiom, `available()` says
  so), and it meets the project's oldest finding, that the writer is stronger
  than the tester, for a language the model barely saw. Wiring OCaml properly
  is separate work.
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
- RAG only helps where the model is ignorant (new/obscure/own-project APIs).
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
- **The contract layer is now measurable, and still unmeasured.** `bench.py`
  holds five deliberately ambiguous specs, each with a hidden hand-written
  oracle encoding the intended reading, and runs every task through both arms
  (`--contract` on and off). Spec-divergence is defined mechanically as *the
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
- Two seams are outside the automated suite: the live `/completion` call and
  the live embedding call. `/completion` has now been exercised by hand end to
  end, including grammar-constrained contract derivation; the embedding call
  has not. Neither is exercised by CI. A CI-able check does guard the class of
  grammar bug that broke `contract.gbnf` (no rule may span lines).

## Next steps (priority order)

1. **SQL**, once it has an assertion form. `sqlite3` ships with Python so the
   runner is free, but SQL has no `assert`, and the check idiom every other
   language gets from its harness needs real design rather than a `1/0` trick.
2. **Run the contract measurement.** The instrument exists (`purecoder
   measure`); the numbers do not. It needs a live llama-server, and one pass
   over five tasks in two arms is ~10 model rounds. Until it is run, the
   layer's central claim stays argued rather than measured.
3. **A per-run venv, if the declaration ever needs to install anything.**
   `--with` covers what the environment already has; anything else is still a
   manual `pip install`. Doing it automatically means network access inside a
   run and a failure mode CI cannot exercise, which is why it was left out
   rather than half-built.
4. **Specialization track** — prune + vocab-trim Qwen2.5-Coder to reclaim
   context room on 6 GB (Flab-Pruner-style), the "make it custom" phase.

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
