# Bootstrapping a language from its docs — design

**Date:** 2026-08-03
**Status:** designed, not built

## The problem

Adding a language today means hand-writing a `LanguageSpec`: a check helper in
that language, an epilogue that fails the run when no check executed, a tester
prompt naming the helper, and build/run commands. Five entries exist because a
human wrote five entries.

The ask: point PureCoder at the docs for a language it has never seen and have
it produce that entry itself.

## What the evidence says

Two results decide the shape of this, and both cut against the obvious design
("retrieve the docs, ask the model to describe the language").

**[No Silver Bullet](https://huggingface.co/papers/2501.19085)** (Jan 2025)
measured five adaptation techniques on R and Racket across model sizes:

| technique | effect |
|---|---|
| translation examples (Python↔target pairs) | +0.9–6.3%, significant on every model above 1B — the only technique that never hurt |
| few-shot in the target language | +8.4% on R for Copilot |
| **translation rules** (prose: "in X, assert like Y") | **worst**; below baseline on 2/6 models for R, 4/6 for Racket |
| fine-tuning | wins below ~1B, *hurts* above 33B |

A 7B model sits in the band the paper calls mixed — no consistent winner — but
translation examples are the safe bet at every size that matters here.

The [LRPL survey](https://arxiv.org/abs/2410.03981) adds that doc-retrieval
alone yields only modest gains for low-resource languages. That claim comes
from a search summary rather than a paper read in full; the No Silver Bullet
numbers carry the argument without it.

**Consequence for this design:** the drafting prompt must be built out of
*worked examples in the target language*, not a prose description of it. The
existing C++ and JavaScript entries are exactly such examples — a check helper,
an epilogue, a tester prompt, side by side. They become the few-shot payload.
Retrieved docs supply the vocabulary the model lacks (how this language writes
to stderr, exits non-zero, declares a macro); the examples supply the shape.

## What MultiPL-E already proves about the field set

[MultiPL-E](https://github.com/nuprl/MultiPL-E) translates HumanEval into 22
languages via one hand-written translator per language. Its `LanguageTranslator`
ABC is close to a `LanguageSpec`, which is a useful independent check that the
field set is the right one:

| MultiPL-E | PureCoder |
|---|---|
| `file_ext()` | `extension` |
| `test_suite_prefix_lines()` | `preamble` |
| `test_suite_suffix_lines()` | `epilogue` |
| `no_completion_prompt_stub()` | `entry_stub` |
| `deep_equality()` | **absent** |
| `stop()` | **absent** |
| `gen_literal` / `gen_list` / `gen_dict` / `gen_set` | **absent, deliberately** |

Two real gaps, both out of scope here and recorded so they are not rediscovered:

- **`deep_equality`** is a required per-language primitive in MultiPL-E, because
  `==` on collections differs wildly. PureCoder hides it in prose inside the
  JavaScript `test_system` (the `JSON.stringify` hack). A bootstrapped language
  will need it and there is nowhere to put it.
- **Per-language `stop` tokens**: `client.complete` accepts `stop` and no spec
  supplies one.

The literal renderers are absent *on purpose*. That was the anchors layer, which
cost five Critical false greens and was deleted. Nothing here reintroduces the
idea of compiling model-authored expressions into generated code.

## Design

### The governing claim

A bootstrapped language is a **candidate** until it proves itself. The proof is
mechanical and is the same bar the hand-written entries meet: a `LanguageSpec`
that cannot distinguish a correct implementation from a wrong one is not a
language entry, it is a rubber stamp. The project's rule — *if it cannot be
executed, it is not emitted* — is not weakened by this feature; it is what makes
the feature safe.

### Flow

```
docs dir ──ingest──▶ DocStore ──retrieve──▶ drafting (worked examples + docs)
                                                  │
                        ┌─────────────────────────┴───────────┐
                        │                                     │
                  preamble, epilogue,                   build / run argv
                  test_system, check_call,                     │
                  extension, add/tests fixtures        printed for confirmation
                        │                                     │
                        └──────────────┬──────────────────────┘
                                       ▼
                            six probes on the candidate
                    ┌──────────────────┴──────────────────┐
                    │                                     │
              all pass                              any fails
                    │                                     │
      write languages/<name>.json                 refuse, report which
```

### Drafting: one focused call per field

Following `scaffold.py`'s rule — low context per task, not merely low tokens —
each field is a separate call carrying only the slice it needs:

| call | drafts | grounded by |
|---|---|---|
| 1 | `preamble` (the check helper) + `check_call` | C++ and JS preambles; docs on stderr, exit codes, macros/functions |
| 2 | `epilogue` (the no-checks tail) | C++ and JS epilogues; docs on program entry points |
| 3 | `add` fixture: correct impl, wrong impl, a three-check test body | the drafted preamble; docs on function syntax |
| 4 | `build` / `run` argv | docs on compiling and running a single file |

`test_system` is **not** drafted freely. It is a template with the language's
name, `check_call` and file extension substituted in — the tester prompt is
PureCoder's own discipline, and the paper's finding about prose rules is a
direct warning against letting the model write its own instructions.

`extension` is asked for on the command line, not inferred. It is one token of
user input against a class of silent failure.

### Trust boundary for build/run

Model-drafted argv is printed and requires explicit confirmation before its
first execution. Every existing entry's commands were hand-written; this is the
first place a local model's output reaches `subprocess.Popen`, and it is a
different trust category from anything else in the codebase. Confirmation is
recorded in the saved JSON so it is not re-asked, and re-asked if the commands
ever change.

### The gate: six probes

Five mechanical, run against the drafted `add` fixture:

1. correct implementation → **passes**
2. wrong implementation → **fails**, and the error text carries the failed check
3. empty test body → **fails** with "no checks ran"
4. syntactically broken implementation → the build or run error is **surfaced**
5. infinite loop → **times out** rather than hanging

Then one live round: `generate_validated_python` against the new spec on a
bubble sort, which must converge within the retry budget. This is the only probe
needing llama-server, and it is what distinguishes a harness that merely runs
from one the writer and tester can actually work inside.

Probe 2 is the one that matters. A check helper that prints on failure but exits
0 passes probes 1, 3, 4 and 5 and is worthless — it is the false-green class
this project keeps rediscovering, and it is caught here mechanically rather than
by reading generated code.

### Storage

`$PURECODER_HOME` or `$XDG_DATA_HOME/purecoder/languages/<name>.json`, loaded
into `REGISTRY` at import. A bootstrapped entry may not shadow a built-in one:
the registry's hand-written entries are the reference, and silently overriding
`python` with a drafted approximation is a failure mode with no upside.

Each file records `bootstrapped: true`, the docs directory it came from, and the
date — provenance the CLI shows, so a bootstrapped language is never mistaken
for a proven one at a glance.

## Boundaries

- The chunker is Python-and-markdown only. Docs in other formats fall back to
  markdown chunking, which is adequate for prose but blind to code samples in
  the target language. tree-sitter chunking remains the real fix.
- A language whose test idiom needs a framework, a project file, or a package
  manager is out of reach: the harness assembles exactly one file.
- Nothing here addresses `deep_equality` or per-language stop tokens.
- A language the model has *no* pretraining exposure to will likely fail probe
  3 or the live round. That is the honest outcome, and the refusal names which
  probe failed.
