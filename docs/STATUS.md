# PureCoder — Build Status

_Snapshot of what's built, tested, and what's next._

## Done and tested

123 tests, all green, none of them needing a GPU or a running server
(`pytest -q`). CI runs the same suite on Python 3.10–3.12.

| Phase | Component | Status | How it was verified |
|---|---|---|---|
| 1 | llama.cpp + Qwen2.5-Coder on 6 GB | ✅ working | server up, ~93% pass on a 15-task baseline (manual) |
| 2 | GBNF grammars (.env, Makefile) | ✅ working | 100% structurally valid output |
| 2 | `client.py` (constrained gen, repeat-penalty) | ✅ tested | ChatML shaping, fence stripping, grammar loading |
| 3 | `validate.py` (config validators + loop) | ✅ tested | catches degeneration & malformed targets |
| 3 | `execute.py` (execution validation) | ✅ tested | executor cases + convergence via a scripted fake model |
| 3 | test-quality gate (`lint_tests`) | ✅ tested | one test per observed bad-test mode, all 5 |
| 5 | `contract.gbnf` + `validate_contract` | ✅ tested | schema guards past the grammar |
| 5 | `anchors.py` (mechanical assertions) | ✅ tested | both shapes, malformed and custom-exception drops |
| 5 | `derive_contract` + fallback | ✅ tested | retries, feeds errors back, degrades on a dead server |
| 4 | `rag.py` chunking + retrieval + gate | ✅ tested | search/gate/persistence proven w/ fake embedder |
| 4 | code-aware AST chunker | ✅ tested | function/class/method/preamble boundaries verified |
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

## Known boundaries (by design, documented)

- Test gate catches *structural* bad tests, not plausible-but-wrong values.
- RAG only helps where the model is ignorant (new/obscure/own-project APIs).
- Chunker is Python-only (stdlib `ast`); other languages need tree-sitter.
- Two seams are outside the automated suite: the live `/completion` call and
  the live embedding call. Both are exercised by hand, neither by CI.

## Next steps (priority order)

1. **Semantic guard for `.env`** — the one validator that still rubber-stamps
   degenerate output; `examples/portcheck/.env` is a live example.
2. **Wire RAG into the scaffolder** — doc-grounded whole projects.
3. **tree-sitter chunking** — multi-language code retrieval.
4. **Specialization track** — prune + vocab-trim Qwen2.5-Coder to reclaim
   context room on 6 GB (Flab-Pruner-style), the "make it custom" phase.
5. **Measure the contract layer** — does grounding actually reduce
   spec-divergence, or only make it visible? Needs a small task set with
   known-ambiguous specs.
