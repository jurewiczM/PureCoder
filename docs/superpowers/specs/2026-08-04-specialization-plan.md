# Specialization: pruning Qwen2.5-Coder for a 6 GB card — Plan

_2026-08-04_

**Status: unbuilt, deliberately.** This is a design, not a stub. Everything
below needs weights that are not on this machine (~4.7 GB for the GGUF, ~15 GB
for the fp16 checkpoint that pruning actually operates on) and GPU hours the
project has not spent. Writing the code without running it would produce
exactly the artifact this project refuses everywhere else: something that has
never been executed. The plan is written so the work can start cold, and so the
go/no-go decision is fixed *before* anyone is invested in a result.

## Problem

Every design decision in PureCoder is shaped by 6 GB of VRAM. Qwen2.5-Coder-7B
at Q4_K_M leaves roughly 1 GB of headroom, which is what forces the two rules
the rest of the system lives under: minimal context per task, and one artifact
per model call. Two of the project's own findings say where that headroom would
go if it existed —

- **The tester is the weak link** (finding 1). It fails where it needs the most
  context: the harness idiom, the target's public names, the spec, and — for a
  learned language — the retrieved documentation, all at once.
- **Context is double-edged** (finding 4). Feeding code forward for coherence
  triggered degeneration. That is partly a small-model property and partly a
  budget one, and only the budget half is buyable.

So the goal is not "a smaller model". It is **the same competence in less
memory, and the reclaimed memory spent on context**.

## Approach

Flab-Pruner-style structural pruning, in three stages, each independently
revertible:

1. **Vocabulary trim.** Qwen's tokenizer carries ~152k entries, the great
   majority of which never appear in this workload — the model is used for
   code, English prose, and a handful of grammar-constrained formats. The
   embedding and LM-head matrices are `vocab × hidden`, so trimming to the
   tokens that actually occur is the cheapest real saving available and the
   least likely to change behaviour. *Measure first:* tokenize the entire
   corpus this project generates and consumes (its own source, the docs it
   ingests, every prompt template, the five languages' harnesses) and keep the
   union plus all single-byte fallbacks. A token that never occurs cannot be
   emitted; the risk is a token that occurs rarely and matters (an identifier
   in a user's own docs), which is why byte fallback must survive intact.
2. **Layer pruning.** Drop whole transformer blocks chosen by a similarity
   criterion — a block whose input and output hidden states are nearly
   identical is doing little. This is where the memory actually is, and where
   the damage actually is.
3. **Healing.** A short fine-tune (LoRA, merged) on the outputs of the
   unpruned model over this project's own task distribution, to recover what
   step 2 broke.

Steps are applied in that order because the cheap, low-risk one comes first: if
step 1 alone frees enough context to matter, steps 2 and 3 need not happen.

## What must be measured, and with what

The project already owns three instruments. Specialization is the first work
that needs all of them at once, and **no new evaluation harness should be
written for it** — a bespoke benchmark would be exactly the "we measured it
ourselves and it looked fine" that the contract layer spent a session avoiding.

| Question | Instrument | Bar |
|---|---|---|
| Did generation get worse? | the 15-task baseline used in Phase 1 (~93% pass) | no more than one task lost |
| Did spec-divergence get worse? | `purecoder measure` (bench.py), both arms | grounded-arm divergence not worse than the unpruned baseline |
| Can it still bootstrap a language? | `learn` against OCaml docs, five probes plus the live round | all five probes pass, first attempt |
| Did the tester get worse? | test-design gate rejection rate over the baseline set | rejection rate not worse |
| What was actually bought? | resident VRAM at the same `-c`, then max `-c` that fits | ≥ 1.5 GB freed, or the attempt is not worth keeping |

The fourth row is the one that matters most and is easiest to forget. The
tester is already the weakest component; a pruned model that writes the same
code and worse tests is a *regression* in this pipeline even if a code
benchmark says otherwise, because tests are what the whole design trusts.

## Go / no-go, decided in advance

Keep the pruned model only if **all** hold:

- ≥ 1.5 GB VRAM freed at equal context, or ≥ 2× context at equal VRAM.
- Baseline pass rate within one task of the unpruned model.
- Test-gate rejection rate no worse.
- OCaml bootstrap still succeeds on the first attempt.

Any single failure means revert. Half a win — "it fits in 4 GB and only fails
one more task" — is a trade this project should refuse, since the tests it
fails are the evidence everything else rests on.

## Cost and prerequisites

- ~15 GB disk for the fp16 checkpoint, plus the GGUF for comparison runs.
- Pruning and healing do not fit in 6 GB. Either rented GPU time, or CPU-offload
  runs measured in days.
- `transformers`, `torch`, `peft`, `datasets` — none currently installed, and
  all far outside the base install. They belong in a separate extra, or better,
  outside this package entirely: the pruning pipeline is a *producer* of a
  model artifact, and PureCoder is a consumer of one. Nothing in `purecoder/`
  should import torch.

## Risks

- **The eval set is small.** Fifteen tasks and five bench specs cannot separate
  a 2% regression from sampling noise. Treat the bars as filters for large
  damage, not as evidence of equivalence, and say so in whatever writeup
  follows.
- **Healing on the teacher's outputs bakes in the teacher's failure modes.**
  The unpruned model's tests are already the weak link; distilling them
  faithfully reproduces that weakness. If healing data is generated, it should
  be *gate-filtered* — only suites the test-quality gate accepted, only code
  that passed execution validation.
- **A pruned model is a fork.** Every finding in `docs/STATUS.md` was measured
  against stock Qwen2.5-Coder-7B. Replacing it silently would invalidate them
  without anyone noticing; the model identity belongs in the status output and
  beside any result the project quotes.

## Not doing

- Quantization experiments below Q4_K_M. Perplexity cliffs there are well
  documented and the failure mode is subtle degradation, which is the worst
  kind for this pipeline.
- Training a code model from scratch, or fine-tuning for capability rather than
  recovery. Out of scope by a wide margin.
