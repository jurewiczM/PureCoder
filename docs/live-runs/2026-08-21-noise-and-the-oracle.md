# 2026-08-21 — the instrument has noise, and the tester is the oracle

Two full passes of the ten-task corpus, sixty tasks each, plus a first run of a
new six-task corpus. Four harness defects fixed, one attempted fix withdrawn,
and two conclusions this write-up originally got wrong and now states
correctly.

Transcripts are under `~/models/bench/` with tags `before`, `after` and
`harness`: `<tag>-results.tsv` for the ledger and `<tag>-<lang>-<task>.log`
per task. Every quotation below names its file.

## What served the model, and why that matters

Not the documented binary. `llama-server` is not on this machine's `PATH` at
all; the only llama.cpp server present is the one bundled in the ollama snap:

```
GGML_BACKEND_PATH=/snap/ollama/131/lib/ollama/cuda_v13/libggml-cuda.so \
/snap/ollama/131/lib/ollama/llama-server \
  -m /home/max/models/Qwen3-Coder-30B-A3B-Instruct-Q3_K_M.gguf \
  -ngl 99 --cpu-moe -c 16384 -fa on -ctk q8_0 -ctv q8_0 --port 8080
```

Without `GGML_BACKEND_PATH` it starts, reports `no usable GPU found`, and runs
on CPU while `/health` returns ok. With it: 2545 MiB of VRAM.

The inference binary is part of what a live number means, so these scores are
not strictly comparable with the 2026-08-09 column.

## The trees

| tree | what | where |
| --- | --- | --- |
| `before` | `main` | `7f0f991` |
| `after` | `main` + four fixes, one since withdrawn | `c097ac0`, pushed as `origin/archive/2026-08-21-after` |

The `after` tree is archived rather than described, because a number nobody can
reconstruct is not a measurement. Note what it is **not**: it is not the tree
that exists if PRs #31–#33 merge. That tree contains a c++ fix that has never
been run against the ten-task corpus, and lacks the withdrawn gate. **No score
in this document describes it.**

## Before and after

| language | before | after | note |
| --- | --- | --- | --- |
| python | 9/10 | 8/10 | the lost point is `is_palindrome`, not the withdrawn gate — see below |
| c++ | 9/10 | 9/10 | `unique` failed both, on two different errors |
| javascript | 10/10 | 10/10 | |
| rust | 10/10 | 10/10 | untouched by anything here |
| c# | 10/10 | 10/10 | |
| ocaml | 7/10 | 6/10 | nothing in either tree touches OCaml |
| **raw** | **55/60** | **53/60** | |
| **corrected for harness defects** | **57/60** | **55/60** | every prior write-up quotes this view; both are given because neither alone is honest |

## The noise, which is the load-bearing result

OCaml moved 7/10 → 6/10 between two runs with **no OCaml-affecting change
between them**. `before-results.tsv` has `ocaml count_vowels ok 2`;
`after-results.tsv` has `ocaml count_vowels suspect-tests 4`. Nothing in the
four fixes reaches that language.

So this corpus carries at least ±1 per column of run-to-run variance. A
one-point move is not evidence. That applies to every "no regression" claim
available from a single pair of runs — including the ones for javascript, c#
and c++ above, and including this document's own first conclusion.

Any future claim from this instrument needs repeats, not a second run.

## The withdrawn gate cost nothing, and is still withdrawn

**This document previously said the gate regressed python 9/10 → 8/10. That
was wrong**, and the run data it cited disproves it.

python/`count_vowels` failed in **both** runs. The gate changed which bucket it
failed in, not whether it failed:

```
before-results.tsv:  python  count_vowels  suspect-tests  4
after-results.tsv:   python  count_vowels  gate           3
```

The point python actually lost was `is_palindrome` — `ok 1` before,
`suspect-tests 4` after — which this same document files as noise. Attributing
it to the gate was double-counting.

The gate is withdrawn anyway, on evidence that does not depend on the score.
It refused a suite that was correct:

```
after-results.tsv, python/count_vowels:
  test design failed the quality gate: count_vowels is asserted to return 0
  for an input of 21 elements
```

Expecting **zero** vowels over twenty-one consonants requires no counting at
all. The rule fired on precisely the suite the tester should have been allowed
to write. A gate that refuses correct work is the defect class, not a fix for
it — so it was withdrawn rather than tuned, and the branch (`5f22448`) deleted.

What it was aimed at is real and unfixed: on 2026-08-09 and again in `before`,
the tester asserted `count_vowels('AEIOUbcdEfGhIjKlMnOpQrStUvWxYz') == 10`
against a string holding nine vowels — 10 being what you get by counting the
`y` the spec says never to count.

The threshold it used could not have been fitted, and that is the durable
lesson: transcripts record code and verdicts but never test bodies, so no
corpus of real generated suites exists to measure against. **Keeping accepted
suites is the prerequisite for any future attempt**, and is a far smaller
change than the gate was.

## Harness defects

Provenance is stated per row, because two of these were not found by these runs
and a table that implies otherwise is the same kind of false claim this
directory exists to catch.

| # | language | task | root cause | found by | state |
| --- | --- | --- | --- | --- | --- |
| 1 | c++ | `unique`, `word_count` | `#define PC_CHECK(x)` takes one parameter; the preprocessor counts commas first, so a braced initialiser arrives as five arguments | this run, `before-cpp-unique.log` | fix open, PR #31, unmerged |
| 2 | c++ | `unique` | a braced list is not a primary expression, so `== {1,2,3}` cannot compile whatever the code does | this run, same log, **same compile as defect 1** | fix open, PR #31, unmerged, never run live |
| 3 | javascript | — | the 2026-08-09 container repair anchors on a literal to the right of `===` only; mirrored, loose and `!==` forms pass through | reading `languages.py`; javascript scored 10/10 in both runs | fix open, PR #32, unmerged |
| 4 | c# | — | the same mirror gap on `List<int>` reference equality | reading `languages.py`; c# scored 10/10 in both runs | fix open, PR #33, unmerged |
| 5 | — | — | no test pinned the javascript or c# repair at all, so both were one regex edit from silent reversion | reading `tests/` | closed by #32, #33 |

Defects 1 and 2 are one line of generated C++ and **both appear in the same
compile** — `before-cpp-unique.log` reports each twice. An earlier draft said
the second was invisible until the first was fixed; the log disproves that. The
true statement is narrower and still worth keeping: both runs read `writer
attempts=4` over a correct `unordered_set` dedupe, so the score could not tell
them apart even though the transcript held both.

Defect 1 has the only two-directional live evidence here, from the new corpus:

```
harness-results.tsv, on main:   cpp  word_count  writer  4   macro "PC_CHECK" passed N arguments
verify-results.tsv, with #31:   cpp  word_count  ok      1
```

None of these fixes is merged. "Fixed" would claim completion for work that has
neither landed nor, in defect 2's case, been measured.

## The defect class this run actually points at

Three independent instances in one day, one cause — the tester is the oracle
and cannot compute:

1. python/`count_vowels` — asserted 10 vowels in a string that has 9.
2. javascript `sha256Hex`, found while smoke-testing HTML ingest — asserted a
   SHA-256 digest it invented. The refused implementation is the correct
   `createHash('sha256').update(str).digest('hex')`, verified under node.
3. python/`initials`, `harness-python-initials.log` — asserted
   `initials('the quick brown fox') == 'TQB'`, dropping the last word, against
   a correct implementation.

The withdrawn gate would have caught neither 2 nor 3: one is a string
expectation, the other a short input. This is roadmap item 3, and these are
three data points for it rather than one.

## Model errors

Diagnosed, with the evidence that settles each:

| language | task | what the model did |
| --- | --- | --- |
| ocaml | `is_palindrome` | wrote `return false` inside a `for` body; `return` is not an OCaml keyword. Reconstructing the file layout from `LanguageSpec.assemble` puts the offending line in candidate code, not scaffold. Also `for i = 0 to (len / 2)` raises on the empty string, which the spec calls a palindrome |
| ocaml | `unique` | recursed before consing, keeping the **last** occurrence where the spec demands the first — compiled and run to confirm: `unique [1;2;1;3;2;4]` → `1 3 2 4` |
| ocaml | `roman` | `match values, numerals with` never destructures the recursion variables, so `convert acc remaining` never advances; killed by the 10s execution timeout |

## Failures that are not classified

Listed separately and deliberately. Their ledger verdict is `suspect-tests`,
which `scripts/bench/README.md` defines as *open this transcript* — it is not a
claim about the model, and an earlier draft of this document filed two of them
under model errors, which is exactly the misreading `CLAUDE.md` warns about.

| language | task | verdict | status |
| --- | --- | --- | --- |
| python | `is_palindrome` | `suspect-tests 4` in `after` only | not diagnosed; within this instrument's noise |
| ocaml | `count_vowels` | `ok 2` in `before`, `suspect-tests 4` in `after` | not diagnosed; the noise datum this document's central claim rests on |

## The new corpus

`scripts/bench/harness-tasks.tsv`, six tasks whose difficulty is in the
assertion rather than the algorithm (PR #35). First run, four languages,
against `main`:

```
python: 5/6   javascript: 6/6   cpp: 5/6   csharp: 4/6
```

Three of those four score 10/10 on the ten-task set. It discriminates on its
first run — and it found two c# defects that are classified but not yet
diagnosed (`==` on `IEnumerable<(string,int)>`, and a `CS1001` at the preamble
boundary in `min_max`).

OCaml remains the only language in the *ten-task* corpus that discriminates,
which is what motivated re-pitching it. That reading depends on the corrected
totals above, not the raw ones.
