# PureCoder — Build Status

_Snapshot of what's built, tested, and what's next._

## Done and tested

306 tests, all green, none of them needing a GPU or a running server
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
| 6 | `--lang` + per-language scaffolding | ✅ tested | refusals explain themselves; a C++ project builds standalone |
| 7 | `langstore.py` persistence | ✅ tested | round trip, shadow guard, corrupt files skipped |
| 7 | `bootstrap.py` probe gate | ✅ tested | two deliberately broken harnesses built and rejected |
| 7 | `learn` drafting + CLI | ✅ tested | drafts scripted; probes run g++ end to end |
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
- **Execution validation reaches five languages, still only run-to-completion
  code.** Python, C++, JavaScript, Rust and C# all compile (where needed), run
  real assertions, and prove a check executed. Go, Java, Swift and OCaml are
  declared and refuse until both a toolchain and a test idiom exist. Power
  Query is refused permanently -- it runs only inside Excel and Power BI.
- **Standard-library-only, whatever the language.**
  Three hard limits, each confirmed repeatedly against a live server on a
  "small web app that graphs random numbers" spec: the sandbox has no
  third-party packages, so anything importing `matplotlib`/`flask`/`PIL`
  cannot be validated no matter how correct it is; a server that calls
  `serve_forever()` never returns, so the timeout is the only possible
  verdict; and successive binds hit `TIME_WAIT`, so even a stdlib
  `http.server` answer fails to rebind on the next attempt. A missing import
  now triggers one stdlib-only retry and then stops, rather than burning the
  whole budget. Function-shaped, stdlib-only specs pass on the first attempt.
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
- **A learned language can be generated and validated, but not scaffolded.**
  It arrives with no `ProjectSpec` -- proving a language runs says nothing
  about how a project of it is laid out -- so `project` refuses it and points
  at `code`. It also has no `writer_system`, so a language whose harness needs
  a shape constraint (C#'s "no class wrapper, no Main" is the built-in example)
  has no way to express one.
- **The drafted build/run commands are the one place model output becomes a
  process.** They are argv rather than a shell string, shell syntax is refused,
  and the user confirms before the first execution. That is a closed door, not
  a sandbox — the executor's isolation is still a temp dir and a process group.
- The doc chunker is Python-and-markdown only, so a language's own code samples
  are chunked as prose. tree-sitter chunking remains the real fix, and it
  matters more here than anywhere else in the pipeline.
- RAG only helps where the model is ignorant (new/obscure/own-project APIs).
- **Ranking is two signals, and the gate is shared.** Cosine plus an
  IDF-weighted exact-name score, the latter bounded in `[0,1]` absolutely so it
  can share `min_score`. The consequence is deliberate: a chunk containing
  every rare token of the query clears the gate on the lexical signal alone,
  with a cosine of zero. A token in every chunk weighs nothing, so stopwords
  cannot do it. The weight (0.5) and the threshold (0.3) are the two numbers
  that decide this, and neither is tuned against a benchmark — there isn't one.
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
- Two seams are outside the automated suite: the live `/completion` call and
  the live embedding call. `/completion` has now been exercised by hand end to
  end, including grammar-constrained contract derivation; the embedding call
  has not. Neither is exercised by CI. A CI-able check does guard the class of
  grammar bug that broke `contract.gbnf` (no rule may span lines).

## Next steps (priority order)

1. **Wire RAG into the scaffolder** — doc-grounded whole projects.
2. **SQL**, once it has an assertion form. `sqlite3` ships with Python so the
   runner is free, but SQL has no `assert`, and the check idiom every other
   language gets from its harness needs real design rather than a `1/0` trick.
3. **Measure the contract layer** — does grounding actually reduce
   spec-divergence, or only make it visible? Needs a small task set with
   known-ambiguous specs. This is the claim the whole layer rests on and it
   is still unmeasured.
4. **A dependency story for the executor** — today anything outside the
   stdlib is unvalidatable. A per-run venv, or declaring allowed packages in
   the spec, would widen execution validation past its current ceiling.
5. **tree-sitter chunking** — multi-language code retrieval.
6. **Specialization track** — prune + vocab-trim Qwen2.5-Coder to reclaim
   context room on 6 GB (Flab-Pruner-style), the "make it custom" phase.

Done since the last snapshot: the reachability false green (runtime check
instrumentation), the `.env` guard (grammar bound plus a looser validator),
test leakage into the implementation (`lint_implementation`), the discarded
gate verdict, dependency thrash (one stdlib retry then stop), and sandbox
process-group cleanup.
