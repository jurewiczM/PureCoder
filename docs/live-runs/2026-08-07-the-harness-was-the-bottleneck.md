# Live run — the harness was the bottleneck

_2026-08-07_

A 30B model was downloaded to test whether more capability would fix the tasks
this pipeline kept failing. It did not need to. Eight defects in the harness
were failing correct code, and once they were fixed two different models passed
everything.

## What the 30B measured first: a capital letter

The first batch scored **0 / 5**, every task at the retry ceiling. That looked
like a capability verdict and was not one. The implementations were correct
OCaml throughout; every test suite opened with

    Let () = pc_check (sum_list [1; 2; 3] = 6) "sum_list works on example"

A capital `L`. OCaml reads `Let` as a constructor and answers "Unbound
constructor Let", so nothing compiled. The model had begun a line the way a
sentence is begun.

Without the per-task transcripts this would have entered the record as *the 30B
scores 0/5* and retired a working model. The batch script had been throwing the
transcript away; keeping it was the change that made the rest of this possible.

## The seven that followed

Each was found the same way — by reading what a failing run actually said.

1. **`Let` at the head of a statement** is repaired before the gate, on the
   argument that already justified the misparenthesised repair: it has no valid
   reading, so there is exactly one thing it can have meant.
2. **The gate rejected valid OCaml.** It anchored on the opening `pc_check ((`,
   which is ordinary — `pc_check ((rev_string "abc") = "cba") "reverses"`
   compiles. What marks the malformation is where the *label* sits: before the
   closing paren, or after it. Anchored on the tail, the two are told apart.
   **This is why `rev_string` was the unstable task in every arm ever measured**
   — a string-returning function invites exactly the shape the gate refused, so
   the designer kept regenerating a suite that had been right the first time.
3. **The repair fixing (2) mangled a check with no label**, turning
   `pc_check (rev_string "ab" = "ba")` into `pc_check rev_string "ab" = "ba"`.
   Meaning-changing, in a function whose whole licence is that it is not.
   Requiring the captured group to close its own parenthesis separates them.
4. **"These tests never call the thing under test" had never run outside
   Python.** Targets came only from `public_names`, which parses Python and
   returns `[]` for everything else, and the check is skipped when targets are
   empty. So this was accepted for `insertion_sort`:

       let () = pc_check ((List.sort compare ["c";"ab";"d"] = ["ab";"c";"d"]) && ...) "sorts strings"

   Three checks of the standard library's sort; the implementation never
   called. `LanguageSpec` now carries a `definition` regex so a language can
   say what its own code defines.
5. **One mention was enough to pass that check.** A 17-check suite for
   `rev_string` got through with two checks on the target and the rest on a
   `StringSet` module that does not exist. Checks are counted now, and a
   minority aimed at the target is refused.
6. **The writer answered the documentation** instead of using it — returning
   `curry4` and a `StringSet` module with no `rev_string` anywhere.
   `defines_target` refuses an implementation that never names what was asked
   for, and asks it only where the tests ask it, so SQL — which has no
   functions — is not held to a rule it cannot meet.
7. **And so did the tester, which was the root cause.** Retrieved docs and the
   request were one string, docs first. Asked for `rev_string` with 1160
   characters of OCaml documentation in front, four consecutive designs never
   mentioned `rev_string` at all. `execute.py`'s first line is that tests come
   from the SPEC; that was true of the code the designer never sees and false
   of the documentation it was handed. The writer keeps the docs; the designer
   gets the request.

Plus the diagnostic that reported nothing: the `[docs]` hint printed only its
header — the line announcing that names follow — and never the names. Fixed,
it immediately showed its own bugs: it was treating the string literal
`"hello"` inside an echoed source line as an identifier and answering with
`hello.o`, while the actually-unbound value went unmentioned because OCaml
does not quote it. Build products are out of the index too (a real 843-name
OCaml index drops 52 entries).

## The measurement, after

Same five OCaml tasks, same 3017-chunk index, `--retries 4`, one run each.

| model | VRAM | speed | passed | attempts per task |
|---|---|---|---|---|
| **Qwen3-Coder-30B-A3B Q3_K_M** | **1864 MiB** | 33.3 tok/s | **5 / 5** | 1, 1, 1, 1, 1 |
| Qwen2.5-Coder-7B Q5_K_M | 4720 MiB | 23.4 tok/s | **5 / 5** | 1, 3, 1, 4, 1 |
| Qwen2.5-Coder-7B Q4_K_M | 4906 MiB | ~40 tok/s | 3 / 5 | 1, 3, ✗, 1, ✗ |

**The model was never the bottleneck.** Q5 — the model already in use, which
had scored 4, 3, 4, 3 across four control runs — passes everything on the fixed
harness. Nothing about it changed.

The 30B is still the better configuration, on three counts that are not the
score: half the attempts (5 against 10), **40% of the VRAM**, and 1.4× the
speed. That last combination was the surprise. The trade being tested was
capability up, speed down; a mixture-of-experts model activating ~3B parameters
per token with its experts in system RAM gives capability up, speed up, and
VRAM down at once.

MoE split, measured on this 6 GB card:

| flags | VRAM | speed |
|---|---|---|
| `-ngl 99 --cpu-moe` | 1864 MiB | 33.3 tok/s |
| `-ngl 99 -ncmoe 36` | 5140 MiB | 40.7 tok/s |
| `-ngl 99 -ncmoe 30` | — | OOM, 816 MiB short on the KV cache |

`--cpu-moe` is the one to run. It is 18% slower than `-ncmoe 36` and leaves
4.3 GB of VRAM free, which is what makes the embedder's fallback-to-CPU path
unnecessary rather than merely available.

## What this cost, and the rule it earns

**Q4_K_M was re-measured too, and its verdict survives — smaller than it
looked.** 2/5 on the broken harness, 3/5 on the fixed one, against 5/5 for the
other two. The capability gap is real; the size of it was partly the harness.

The methodological point is the one worth keeping. Four Q5 control runs had
established this benchmark as ±1 task noisy, and that reading was wrong: the
variance was mostly the harness, and `rev_string` was never model variance at
all. Then the harness was fixed *during* the experiment — so the 30B's 5/5 was
compared against a Q5 measured on different code, and the obvious conclusion
("the 30B fixed it") was available and false. One control run on the fixed code
cost ten minutes and inverted it.

**When a benchmark and the thing it measures are changed in the same session,
the control has to be re-run or the result is about neither.**

## Caveats

- One run per model on the fixed harness. The 30B's 5/5 at one attempt each is
  a stronger signal than a total would be, but it is still one run.
- Five tasks, one language, all small pure functions. Nothing here says
  anything about a 30B on a larger problem.
- Q3_K_M is two quantisation steps below the Q5 it is compared against. A Q3
  30B winning makes the architecture argument stronger, not weaker, but the
  comparison is not clean and is not claimed to be.
