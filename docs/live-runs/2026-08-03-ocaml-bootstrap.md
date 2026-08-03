# Live run: learning OCaml from its docs

**Date:** 2026-08-03
**Feature under test:** `purecoder learn` (`purecoder/bootstrap.py`)
**Model:** Qwen2.5-Coder-7B-Instruct, Q4_K_M, on an RTX 4050 Laptop (6 GB)
**Target language:** OCaml (originally as `ocaml5`; the real name works since the fixes below)

## Why OCaml

It is the hardest fair test available on this machine. The toolchain is present
(`ocaml` runs a file directly, exit codes and stderr propagate), the paradigm is
genuinely unlike the three worked examples the drafting prompt carries, and it
has no `main` function — which turned out to be the thing that broke.

Docs were four markdown pages assembled from ocaml.org reference text: `Printf`,
`Stdlib` (exit, refs, comparison), basic syntax, and compiling/running. 15 chunks
after ingestion.

## Result

**The gate did its job completely. The drafting needed five fixes and is still
not finished.**

Every failure was caught, named, and explained with the compiler's own message.
Nothing unproven was ever registered. Best run reached **4 of 5 probes**.

| run | outcome |
|---|---|
| 1 | refused — `learn ocaml` is a built-in name |
| 2 | 3/5 probes — fences embedded mid-fixture |
| 3 | refused during drafting — check-call extracted as `if` |
| 4 | 3/5 probes — tail called `pc_tests ()`, tests were top-level |
| 5 | **5/5 probes**, then the live round failed on the tester's output |
| 6 | refused during drafting — my own `./{bin}` guard was too strict |
| 7 | 4/5 probes — one malformed `empty` fixture snippet |

The variation between runs at `temperature=0.2` is itself a finding: four runs
produced three different build/run drafts (`ocaml {src}` with no build twice,
`ocamlc -o {bin} {src}` + `./{bin}` twice).

## Defects found, all now fixed with regression tests

Each test is built from the output that produced the failure.

### 1. Fence markers in three shapes `strip_fences` cannot see

`strip_fences` handles one well-formed pair: a fence alone on the first line and
alone on the last. Drafting produces three shapes it cannot:

- a fence **per section** inside a multi-part answer, so stripping the outer
  pair leaves inner markers embedded mid-fixture
- a closing fence **welded to the last statement** — `;;` and the backticks on
  one line
- an opening tag **never closed**

All three reached the compiler as a syntax error pointing at the fence rather
than at anything the model got wrong, which makes the diagnostic actively
misleading. `bootstrap.unfence` now removes every marker wherever it appears.
Safe because a triple backtick is not valid syntax in any language the executor
runs — if it is there, it is markup.

### 2. Check-call extraction returned a keyword

OCaml answered `if pc_check (1 = 1) then () else ()`. Taking the first
identifier yielded `if`.

The first fix — require the token to appear in the preamble — **also failed**,
because `if` appears in the preamble too, inside the helper's own body
(`if not cond then ...`). A regression test caught that before it shipped.

What actually separates them: the drafting prompt *dictated* the name. The
helper is now matched by name first (case-insensitive, with whatever sigil the
language attaches — Rust's is `pc_check!`), with the preamble check as the
fallback for a model that renamed it anyway.

### 3. The tail and the tests never saw each other

`draft_epilogue` ran before `draft_fixture` and neither was shown the other's
output. OCaml got a tail calling `pc_tests ()` while the tests were written as
top-level statements, so the harness could not compile however good either half
was alone.

`draft_fixture` now receives the epilogue and is told its snippets must define
whatever the tail calls.

### 4. The worked examples bias the harness shape

**This is the finding that matters most.**

Two of the three examples (C++, Rust) need an entry point and call
`pc_tests()`; JavaScript does not. Asked for OCaml — which runs top-level
statements in order — the model generalised the majority shape.

This was verified rather than assumed. Taking the model's own preamble and its
own tests, and correcting **only** the epilogue's shape by hand:

```ocaml
(* what the model drafted *)          (* what OCaml needs *)
let () =                              let () =
  pc_tests ();                          if !pc_checks < 1 then begin
  if !pc_checks < 1 then begin            Printf.eprintf "no checks ran\n";
    eprintf "no checks ran\n";            exit 2
    exit 2;                             end
  end
```

...made **all five probes pass**. The drafting is close; the example bias is
what stands in the way. (The model also wrote `eprintf` unqualified, a second
error hiding behind the first.)

The epilogue prompt now states the invariant: the tests are already placed
between the helper and the tail, so provide an entry point only if the language
requires one.

### 5. `./{bin}` — and my first fix being worse than the bug

`{bin}` expands to an absolute path, so `./` prefixed to it resolves against
the working directory instead and the run fails.

My first fix **refused** the draft. That turned a mechanical formatting habit
into a hard failure and became the blocker on the very next run. It has exactly
one correct reading, so it is now normalised (`./{bin}` → `{bin}`).

## Resolved (second session, same day)

All three items below were fixed and re-verified against a live server. The
headline: **`purecoder learn ocaml ./ocaml-docs --ext .ml` now registers on the
first attempt, five probes green, twice in a row.**

### Bootstrap now redrafts — fixed

`learn_language` loops: probe, and on failure redraft the whole harness with
the failing probes' names and their toolchain output. Whole rather than in
part, because a compile error on probe 1 could be the helper, the tail or the
fixture and attributing it needs a parser per language. Confirmation stays
outside the loop — it reads stdin, and a probe failure does not implicate the
commands, since the build ran. `--draft-retries` defaults to 2.

**Feeding back the diagnostic was necessary but not sufficient.** Told "Unbound
value pc_tests", the model reads it as *define* `pc_tests` and keeps the tail
that calls it — three drafts running. So the shape contradiction is now
detected mechanically (`dangling_calls`) and named: an identifier the tail
applies to arguments that nothing in the preamble or the tests defines. The
redraft states the choice explicitly. Definitions are excluded by looking for a
block after the closing paren, or `int main() {` reads as a call to something
undefined and the hint fires on the shape that is correct.

### Blame attribution — fixed, without a per-language parser

The tests do not change between attempts, so an identical failure across
*different* generated code is evidence the tests are at fault. The loop already
computed that signal for `NO_PROGRESS_LIMIT` and read it only as "stop". The
tests now get one redesign before it gives up.

Deliberately **not** line-number attribution: g++, rustc, C# and OCaml all
format diagnostics differently, and that is the per-language surface the
registry exists to avoid.

Seen live on the first real use of the learned entry:

```
[attempt 3] the same failure on different code -- suspecting the tests, redesigning them
```

### Reserved names — fixed

One set was answering two questions: "was this hand-written?" and "may a draft
take this name?" `RESERVED_NAMES` now covers wired entries and standing
refusals only, so `go`, `java`, `swift` and `ocaml` — placeholders declared so a
refusal can name them — are learnable. `learn ocaml` no longer needs the
`ocaml5` workaround.

The snapshot keeps specs rather than names, because `register` replaces entries
in place: once a learned `ocaml` lands there is no placeholder left to consult.

### Found while fixing: supplied tests were silently replaced

Not in the original report, and worse than anything in it. The post-code gate
redesigned tests the **caller** passed in — discarding the one thing they asked
the code to be checked against, then reporting success against tests they never
wrote. Supplied tests that fail the gate are now reported as a failure with the
gate's reason.

## Still open

### The tester is the ceiling for a low-resource language

The harness is sound and the language registers. Generating *with* it is a
different matter: asked for a `double` function, the writer produced correct
OCaml (`let double x = x * 2`) on every attempt while the tester produced
source the compiler rejected — through a test redesign and six attempts, ending
in an honest refusal rather than an unvalidated artifact.

This is the project's oldest finding — *the writer is stronger than the tester*
— amplified for a language the model has thin exposure to. No harness change
addresses it. The realistic levers are a stronger tester prompt per language,
few-shot test examples in the target language, or a specialised model.

## Caveats on this run

- **The docs were partly hand-written.** `printf.md` and `stdlib.md` are
  transcribed ocaml.org reference text, but `syntax.md` and `running.md` were
  written by hand and are cleaner and more targeted than a real doc dump. The
  test is therefore somewhat favourable to the drafting.
- **The live round still has not passed for OCaml.** The five mechanical
  probes clear on the first attempt now, but the bubble-sort round depends on
  the tester, which is the open item above. `--no-live` was used for the runs
  that registered.
- RAG required installing `sentence-transformers` (~2 GB with torch); it is not
  in the base install.

## Reproducing

```bash
# 1. serve the model
./llama.cpp/build/bin/llama-server \
  -m ~/.cache/huggingface/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct-GGUF/snapshots/*/qwen2.5-coder-7b-instruct-q4_k_m-00001-of-00002.gguf \
  -ngl 99 -c 4096 -fa on --port 8080

# 2. retrieval extra
pip install -e ".[rag]"

# 3. learn, answering the command confirmation
echo y | PURECODER_HOME=/tmp/pchome \
  purecoder learn ocaml ./ocaml-docs --ext .ml --no-live
```

`--no-live` skips the bubble-sort round; drop it to exercise the full gate.
`--draft-retries N` sets how many drafting attempts to allow.
Set `PURECODER_HOME` to keep the learned entry out of the real store.
