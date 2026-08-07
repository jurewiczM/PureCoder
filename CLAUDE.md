# Working on PureCoder

Read [docs/STATUS.md](docs/STATUS.md) first — it is the handoff: what is built,
what was measured, what is known broken, and what comes next. This file is only
the things that are easy to get wrong and expensive to rediscover.

## Commits and PRs carry no tool attribution

Max has asked for this repeatedly. No `Co-Authored-By: Claude`, no
`Claude-Session:`, no "Generated with" trailer — in commit messages **or in PR
bodies**. It is his repository under his name and the attribution is noise in
permanent history.

Verify before every push:

```bash
git log --format="%B%an%ae" main..HEAD | grep -icE "claude|anthropic"   # must be 0
```

## Checks

```bash
.venv/bin/python -m pytest tests/ -q      # 558 tests, no GPU, no server needed
.venv/bin/ruff check purecoder tests
```

Toolchain-dependent tests self-skip when a compiler is absent, so a green run
on a machine without `ocamlc` proves less than it looks. CI installs them.

## The rule this project keeps relearning

**A prompt asks; a mechanical constraint tells.** Every time a defect was
attacked with better prompt wording it came back — the `.env` rambling comment
(three attempts), SQL's empty database, OCaml's capitalised identifiers. What
held was always a constraint one layer down: a grammar bound, a repair applied
before the gate, a check the model cannot route around.

Before writing "the prompt now says…", ask what would make the bad output
impossible instead.

## Live runs find what tests cannot — and read the transcript, not the score

Every live session so far has found defects invisible to a fully green suite:
8, then 6, then 9. They are not found by running the pipeline and looking at
whether it passed.

A run reports `ok=False attempts=4` whether the model wrote bad code or the
harness refused good code. **These are opposite bugs and the score cannot tell
them apart.** On 2026-08-07 a batch scored 0/5 with correct implementations
throughout — every test suite opened with a capitalised `Let`, which OCaml
reads as a constructor. It was nearly recorded as a model capability result.

The benchmark keeps per-task transcripts for this reason:

```bash
scripts/bench/ocaml-batch.sh <tag>     # see scripts/bench/README.md for the corpus
```

If you change the harness and the thing it measures in the same session,
**re-run the control** or the result is about neither.

## Serving a model

```bash
llama-server -hf unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:Q3_K_M \
  -ngl 99 --cpu-moe -c 16384 -fa on -ctk q8_0 -ctv q8_0 --port 8080
```

A 30B on a 6 GB card is not a mistake — `--cpu-moe` keeps the expert tensors in
system RAM, ~3B activate per token, 1.9 GB of VRAM at 33 tok/s. `purecoder
status` prints this command and the 7B alternative. Full offload is faster and
wrong in either architecture: the embedder needs ~275 MB on the same card.

Only `/completion` accepts raw GBNF, so `client.py` applies ChatML by hand.
GGUF chat-template metadata never reaches the sampler — re-downloading weights
to fix a template does nothing.

## The branch stack

Fourteen branches, `chore/harden-ci` → `feat/13-tdd`, strictly linear, open as
stacked PRs #2–#15 each based on its predecessor. **Merge bottom-up**; each
merge retargets the next onto `main`. Nothing is on `main` yet.

New work goes on a branch off `feat/13-tdd` (or the tip of whatever has landed),
never straight onto `main`.

## Adding a language

A language is data: one `LanguageSpec` in `purecoder/languages.py`. It must
pass the five bootstrap probes — correct passes, wrong fails, empty suite
fails, broken reports an error, failing check fails — before it is believed.
`learn <name> <docs>` drafts one; OCaml needed hand-wiring after six drafting
attempts failed, and is held to the same probes.

The governing rule: **if it cannot be executed, it is not emitted.** A missing
toolchain is refused with the binary named. There is no "generated but
unchecked" tier, because that is the claim this project exists not to make.
