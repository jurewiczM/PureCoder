# Benchmark maintenance run — 2026-08-21

Two full passes of the ten-task corpus across all six runnable languages,
sixty tasks each, with every failure classified before anything was changed.

## What served the model

Not the documented binary. `llama-server` is not installed anywhere on this
machine's `PATH`; the only llama.cpp server present is the one bundled inside
the ollama snap:

```
/snap/ollama/131/lib/ollama/llama-server \
  -m /home/max/models/Qwen3-Coder-30B-A3B-Instruct-Q3_K_M.gguf \
  -ngl 99 --cpu-moe -c 16384 -fa on -ctk q8_0 -ctv q8_0 --port 8080
```

It is an upstream build and supports `--cpu-moe` and raw GBNF. It needs
`GGML_BACKEND_PATH=/snap/ollama/131/lib/ollama/cuda_v13/libggml-cuda.so`;
without it the server starts, reports `no usable GPU found`, and runs on CPU
while looking healthy. With it: 2545 MiB of VRAM, `/health` ok.

**The inference binary is part of what a live number means.** These scores are
not strictly comparable with the 2026-08-09 column, which was taken with a
different server.

## Before and after

`before` is `main` at `7f0f991`. `after` is that tree plus four fixes, one of
which was subsequently withdrawn — see the negative result below.

| language | before | after | note |
| --- | --- | --- | --- |
| python | 9/10 | 8/10 | regressed by a fix that has been withdrawn |
| c++ | 9/10 | 9/10 | one defect removed, a second revealed underneath |
| javascript | 10/10 | 10/10 | fix is latent in this corpus |
| rust | 10/10 | 10/10 | untouched |
| c# | 10/10 | 10/10 | fix is latent in this corpus |
| ocaml | 7/10 | 6/10 | **nothing in either run touches OCaml** |
| **total** | **55/60** | **53/60** | |

## The instrument has noise, and that is the load-bearing result

OCaml moved 7/10 → 6/10 between two runs with **no OCaml-affecting change
between them**: `count_vowels` passed in the first and failed in the second.
Nothing in the four fixes reaches that language.

So this corpus carries at least ±1 per column of run-to-run variance, and a
one-point move is not evidence by itself. That retroactively weakens every
"no regression" claim available from a single pair of runs — including the
ones for javascript, c# and c++ above. It is why the python regression below
is reported as real: not because the number moved, but because the transcript
names the mechanism.

Any future claim from this instrument needs repeats, not a second run.

## Harness defects found

| # | language | task | root cause | status |
| --- | --- | --- | --- | --- |
| 1 | c++ | `unique` | `#define PC_CHECK(x)` takes one parameter; the preprocessor counts commas before C++ sees the line, so a braced initialiser arrives as five arguments | fixed, PR #31 |
| 2 | c++ | `unique` | a braced list is not a primary expression, so `== {1,2,3}` cannot compile whatever the implementation does | fixed, PR #31 |
| 3 | javascript | `unique` | the 2026-08-09 container repair anchors on a literal to the right of `===` only; mirrored, loose and `!==` forms pass through | fixed, PR #32 |
| 4 | c# | `Unique`, `InsertionSort` | same mirror gap on `List<int>` reference equality | fixed, PR #33 |
| 5 | all | — | no test pinned the javascript or c# repair at all, so both were one regex edit from silent reversion | fixed in #32, #33 |
| 6 | python | `count_vowels` | the tester asserts an expected value it derived by hand and got wrong; nothing downstream can tell that from wrong code | **not fixed** — see below |

Defects 1 and 2 are one line of generated C++ and the second was invisible
until the first was gone. Both runs read `writer attempts=4` over a correct
`unordered_set` dedupe.

## The negative result: bounding the tester's arithmetic did not work

python/`count_vowels` failed both runs on this assertion:

```
assert count_vowels('AEIOUbcdEfGhIjKlMnOpQrStUvWxYz') == 10
```

Thirty characters, nine vowels — 10 is what you get by counting the `y` the
spec says never to count. Four correct implementations refused, twice, while
the same spec passed on the first attempt in all five other languages.

The attempted fix was a gate mode that refused the *question* rather than
judging the answer: an integer expectation over a literal longer than twelve
elements is arrived at by hand, so reject it and ask for shorter inputs. It
passed a unit probe of fourteen corpus-shaped assertions, firing on exactly
the observed defect.

Live, it made the column worse — 9/10 → 8/10:

```
python  count_vowels  gate  attempts=3
  count_vowels is asserted to return 0 for an input of 21 elements
```

Expecting **zero** over twenty-one consonants requires no counting at all. The
rule fired on precisely the suite the tester should have been allowed to write
and converted a soft `suspect-tests` into a hard refusal. A gate that refuses
correct work is the defect class, not a fix for it, so the change was
withdrawn rather than tuned.

The threshold was judgement with nothing behind it, and it could not have been
fitted: transcripts record code and verdicts but never test bodies, so no
corpus of real generated suites exists to measure against. **Keeping accepted
suites is the prerequisite for any future attempt at this.**

## The defect class this run actually points at

Three independent instances, one cause — the tester is the oracle and has no
way to compute:

1. python/`count_vowels` — asserted 10 vowels in a string that has 9.
2. javascript `sha256Hex` (found while smoke-testing HTML ingest) — asserted a
   SHA-256 digest it invented; the refused implementation is the correct
   `createHash('sha256').update(str).digest('hex')`, verified under node.
3. python/`initials` (harness corpus, first run) — asserted
   `initials('the quick brown fox') == 'TQB'`, dropping the last word.

The withdrawn gate would have caught none of 2 or 3: one is a string
expectation, the other a short input. This is roadmap item 3, and it is where
the next real gain is.

## Remaining MODEL_ERRORs

Genuine model failures, no harness action:

| language | task | what the model did |
| --- | --- | --- |
| ocaml | `is_palindrome` | wrote `return false` inside a `for` body; `return` is not an OCaml keyword. Also `for i = 0 to (len / 2)` raises on the empty string, which the spec calls a palindrome |
| ocaml | `unique` | recursed before consing, keeping the **last** occurrence where the spec demands the first — confirmed by compiling and running it: `unique [1;2;1;3;2;4]` → `1 3 2 4` |
| ocaml | `roman` | `match values, numerals with` never destructures the recursion variables, so `convert acc remaining` never advances — killed by the 10s execution timeout |
| ocaml | `count_vowels` | failed in the `after` run only, passed in `before`; within this instrument's noise and not separately diagnosed |
| python | `is_palindrome` | failed in the `after` run only, passed in `before`; same caveat |

OCaml remains the only language in this corpus that discriminates, which is
the finding that motivated re-pitching it.
