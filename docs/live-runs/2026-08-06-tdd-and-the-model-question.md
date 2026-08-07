# Live run — test-first, and the model question

_2026-08-06_

Two things were exercised against a real server: the new test-first mode, and
the question of whether a smaller or cheaper model would serve this pipeline
better. The second produced the more useful result, and it is a negative one.

## Test-first, live

`code --tdd` turns the request into a contract, the contract into tests, runs
those tests against a stub — a function that exists and returns `None` — and
refuses them unless a check ran AND failed. Then it shows the suite and the
failure and asks.

The first live run made the case better than the design did. Asked for
*"parse_ports ... into a **sorted** list of ints"*, the contract came back with

    example : parse_ports('80,443') -> [443, 80]

Unsorted. The tests encoded it faithfully, and correct code was then failed by
them, three attempts running. This is the identical defect recorded in
`docs/STATUS.md` months ago from the first live contract run — the same
function, the same wrong example.

With `-y` that happened silently. Without it, `assert parse_ports('80,443') ==
[443, 80]` is on screen, above a printed failure, before a single line of
implementation exists. That is the whole argument for the confirmation step,
and the reason `-y` is documented as a choice with a cost rather than a
convenience.

**What it cannot do**, from the same session: a run produced `assert
word_count(' ') == 1` against a correct implementation. Red, for the wrong
reason. Proving a suite *can* fail is not proving its expectations are right,
and no mechanical check closes that gap — which is exactly why the confirmation
is a human one.

One defect fell out one layer down: a contract example with an empty outcome
(`word_count('hello world') -> ` and nothing after the arrow) was being
accepted. An example that states no outcome grounds neither the writer nor the
tester, and it is refused now.

## How much context this card can actually hold

The launch command everything here was built against used 4k of context. That
number was inherited and never measured. Measured, on a 6 GB card:

| weights | offload | context | KV | VRAM | speed |
|---|---|---|---|---|---|
| Q5_K_M | 20/29 | 4k | f16 | 3.8 GB | 19 tok/s |
| **Q5_K_M** | **24/29** | **16k** | **q8_0** | **4.7 GB** | **23 tok/s** |
| Q5_K_M | 29/29 | 16k | q8_0 | 5.5 GB | 35 tok/s |
| Q4_K_M | 29/29 | 32k | q8_0 | 5.4 GB | 40 tok/s |

**The fastest configuration is unusable.** At full offload the embedder — about
275 MB, most of it torch's CUDA context rather than the model — has nowhere to
go, and every doc-grounded run dies with a raw `torch.OutOfMemoryError` in the
middle of an ingest. It now falls back to CPU on a memory error, but the
configuration that forces that fallback is still the wrong one to choose.

Four times the window costs about 500 MB. Worth taking, and worth being precise
about *why*: the **output** budget was never binding. Across six generations
including an `LRUCache` class, nothing hit the 512-token cap. The pressure is
on the prompt — retrieved documentation, the contract, the previous attempt,
the source quoted around a diagnostic — all of which this session added to.

## Would a cheaper model do?

The suggestion was a 3B. The cheaper version of the same bet is a smaller
quantisation, and it can be tested with weights that are 4.7 GB rather than a
new architecture: **Q4_K_M is what this project's own documentation claimed to
be running**, and it fully offloads at 40 tok/s against Q5's 23.

Same five OCaml tasks, same harness:

| weights | speed | retries | passed |
|---|---|---|---|
| Q5_K_M | 23 tok/s | 4 | **4 / 5** |
| Q4_K_M | 40 tok/s | 4 | 2 / 5 |
| Q4_K_M | 40 tok/s | **7** | **2 / 5** |

The third row is the one that settles it. The argument for a smaller model is
that speed buys attempts, and the retry loop converts attempts into passes. So
Q4 was given its entire speed advantage as extra retries — and recovered
**nothing**. `rev_string` and `insertion_sort`, which Q5 passed on the *first*
attempt, failed at seven.

**The failures are systematic, not stochastic.** More samples from a weaker
distribution do not converge on a right answer; they repeat a wrong one more
cheaply. That answers the 3B question without downloading a 3B: it is a larger
cut than Q5 to Q4, and it would be paid for in the tester — the component this
pipeline's honesty rests on, and already the bottleneck in eight of ten
measured arms.

It also puts a number beside the specialization plan, which proposes the same
trade at a larger scale. Its go/no-go bars exist for this reason, and this run
is the first evidence that they will bind.

## How noisy is this benchmark, actually?

The index this batch retrieves from was lost to a `/tmp` cleanup, so before
testing anything new it was rebuilt from the same source — and came back at
**3017 chunks, the exact count of the destroyed one**. The corpus reproduces.

Q5 was then re-run on it as a control, to check that the recorded numbers still
mean what they said. Four runs of the same weights, same flags, same corpus:

| task | run 1 | run 2 | run 3 | run 4 |
|---|---|---|---|---|
| `sum_list` | ✓ 1 | ✓ 1 | ✓ 1 | ✓ 1 |
| `max_of_list` | ✓ | ✓ 2 | ✓ 2 | ✓ 2 |
| `insertion_sort` | ✓ 1 | ✓ 1 | ✓ 1 | ✓ 1 |
| `rev_string` | ✓ 1 | ✗ 3 | ✗ **0** | ✗ 4 |
| `is_palindrome` | ✗ 4 | ✗ 4 | ✓ 2 | ✗ 4 |
| **total** | **4/5** | 3/5 | 4/5 | 3/5 |

**Three of the five tasks are deterministic**, to the attempt. Every bit of the
variance lives in the two string tasks — and `attempts=0` names the stage it
lives at: the test-design gate never accepted a suite, so the writer was never
asked for code. The instability is in the tester, not the coder, which agrees
with the tester being blamed in eight of ten previously measured arms.

Two consequences. **A single five-task score carries ±1 task of noise**, so no
configuration can be judged by its total alone; the per-task attempt counts are
the measurement, and attempts-to-pass is far steadier than pass/fail. And
`is_palindrome`, which had failed at max retries in *every* arm ever run, passed
at 2 on run 3 — four failures in a row were a small sample of a noisy stage, not
the wall they looked like.

The Q4 verdict survives this. Q4's 2/5 sits below the observed Q5 range of 3–4,
and the seven-retry row — where failures outlived their remedy — is what carried
it in the first place. Wider error bars, same conclusion.

## Caveats

- Five tasks, a sampled model, and — measured directly above — ±1 task of
  run-to-run noise on the total. The Q4/Q5 gap at four
  retries alone would be inside the noise; the seven-retry row is what makes it
  worth acting on, because it shows the failures surviving the remedy.
- The 4k → 16k measurement is this card and these weights. Nothing about it
  transfers to a different GPU.
- Q4_K_M weights are kept on disk as a rejected candidate, not deleted, so the
  comparison can be repeated.
