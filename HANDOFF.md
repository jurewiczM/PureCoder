# The harness corpus

Roadmap item 1 in `docs/STATUS.md`: re-pitch the corpus, now that there is
data. Answered as **harder tasks that stress the harness**, not the model.

## What was built

`scripts/bench/harness-tasks.tsv` — six tasks — plus a `CORPUS` variable in
`batch.sh` so the ten-task set stays the default and stays byte-identical.
`tasks.tsv` is the control for every number in `docs/live-runs`; appending to
it would silently change what each historical column means.

The design rule is one sentence: **difficulty in the assertion, not in the
algorithm.** Every task is a few lines in every language the registry runs, so
a failure is a claim about the harness until the transcript says otherwise.

| task | shape it asks the assertion machinery for |
| --- | --- |
| `word_count` | a container of pairs, ordering fixed by the spec |
| `min_max` | two values out of one call |
| `mean` | a float, which must not be asserted with `==` |
| `parse_ints` | a container built out of a string |
| `safe_divide` | failure that is not a value |
| `initials` | the control |

The reason to believe the harness is where the defects are: `unique` is the
only row in `tasks.tsv` whose expected value is a container, and that one row
found three defects in three languages on 2026-08-21.

## How it was verified

Live, four languages, 24 tasks, against `main` — so every defect below is
`main`'s, not this branch's:

```
python:     5/6
javascript: 6/6
cpp:        5/6
csharp:     4/6
```

Compare the ten-task set, which returns 10/10 for three of those four. The new
set discriminates on the first run.

Four failures, classified:

| lang | task | verdict | cause |
| --- | --- | --- | --- |
| c++ | `word_count` | HARNESS_DEFECT | `PC_CHECK` macro arity — a second task reaching the defect PR #31 fixes |
| c# | `word_count` | HARNESS_DEFECT | `==` applied to `IEnumerable<(string, int)>`, which does not compile |
| c# | `min_max` | HARNESS_DEFECT (unconfirmed) | `CS1001: identifier expected` at assembled line 11 — not yet traced |
| python | `initials` | HARNESS_DEFECT | tester asserted `initials('the quick brown fox') == 'TQB'`, dropping the last word, against a correct implementation |

The c++ one is proven both ways, which is the strongest single result here:

```
# on main
cpp  word_count  writer  4  error: macro "PC_CHECK" passed N arguments

# same task, tree with PR #31 merged
[ok            attempts=1   ] cpp/word_count
```

## What it found that the design did not predict

The container-shaped tasks mostly **passed**. What failed in python was
`initials` — the control, the plainest task in the file — because the tester
wrote a wrong expected string.

That is the third instance in one day of a single class: python/`count_vowels`
(9 vowels asserted as 10), a JavaScript `sha256Hex` digest invented from
memory, and now `initials`. The tester is the oracle and cannot compute. This
corpus was aimed at assertion *shapes* and hit assertion *values* instead,
which is a better result than confirmation would have been — it points at
roadmap item 3 with three independent data points.

## Known gaps

- **Four languages, not six.** Rust and OCaml have not been run against this
  corpus. OCaml is the language that discriminates in the ten-task set and it
  needs a `STORE`, so its column costs the most and is the most interesting.
- **One run each.** The ten-task set moved a point between two runs with
  nothing relevant changed, so this instrument has at least ±1 of noise per
  column and none of the six scores above should be quoted as a capability
  number.
- **c#/`min_max` is not diagnosed.** `CS1001` at line 11 of the assembled file
  points near the preamble boundary rather than into the candidate, which is
  what makes it worth opening rather than assuming.
- **The corpus has never been run with a contract or with `--tdd`.** Both
  change what the tester is asked for, and both are in the pipeline these
  tasks are supposed to instrument.
- **`safe_divide` may be underspecified across languages.** "Signals an error"
  is idiomatic in every language and identical in none; it passed in all four
  runs, but a Rust `Result` and a C# exception are not the same assertion and
  the spec does not say which is wanted.
- **dotnet emits localised diagnostics.** On this machine the C# compiler
  reports `error CS0019: Nie można zastosować operatora` — the harness quotes
  it verbatim and the line/column parse survives, but anything that ever
  matches on English compiler text would not.
