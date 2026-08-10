# The benchmarks

Two of them, measuring different things.

- **`batch.sh`** — ten pure functions across six languages. Breadth: what does
  the pipeline close on, and where does it stop.
- **`ocaml-batch.sh`** — five pure functions in OCaml, grounded in real
  documentation. Depth in the one language the model has little of in its
  weights, so the run depends on retrieval and on the harness rather than on
  recall. It produced the tables in `docs/live-runs/2026-08-06-*` and
  `2026-08-07-*`.

Both write a full transcript per task under `$BENCH` (default
`~/models/bench`). **Keep them.** A run reports `ok=False attempts=4` whether
the model wrote bad code or the harness refused good code, and every defect in
the 2026-08-07 write-up was found in a transcript, not in a score. An earlier
version of `ocaml-batch.sh` discarded them and a batch of correct
implementations was nearly recorded as a capability result.

---

## The ten-task cross-language corpus

```bash
scripts/bench/batch.sh python  q3-baseline      # all ten
scripts/bench/batch.sh c++     q3-baseline
TASKS="sum_list roman" scripts/bench/batch.sh c# smoke
```

Ten pure functions in `tasks.tsv`, run through one language per invocation:
`python`, `c++`, `javascript`, `rust`, `c#`, and `ocaml` only with a `STORE`
(the script refuses it otherwise — see below). Language is an argument
rather than a loop because ten tasks across six languages is a multi-hour run,
and scope should be a choice made per invocation.

`sum_list`, `rev_string`, `count_vowels`, `max_of_list`, `is_palindrome`,
`gcd`, `unique`, `insertion_sort`, `run_length_encode`, `roman` — three
trivial, four easy, three that bite. The ramp exists so the set does not
saturate: an instrument that returns 10/10 everywhere cannot compare two models
or detect a regression.

Environment: `BENCH`, `TASKS` (a subset), `STORE` (a RAG index), `RETRIES`
(default 4), `TASK_TIMEOUT` (default 1800).

### It says who refused, not just that someone did

```
[ok       attempts=1   ] python/gcd
[writer   attempts=4   ] cpp/roman         error: expected `;`
[gate     attempts=0   ] csharp/is_palindrome   tests never mention any of ['IsPalindrome']
[unknown  attempts=0   ] rust/unique       <the error, verbatim>

python: 8/10  -- writer 2 gate 0 contract 0 stuck 0 refused 0 server 0 timeout 0 unknown 0
```

`ok=False attempts=4` reads identically whether the model wrote bad code or a
gate refused good code, and those are opposite bugs. `purecoder/benchlog.py`
reads the markers `execute.py` already emits and says which: **`writer`** is
the only bucket that claims anything about the model, and it is used only where
the code never built; `gate`, `contract`, `stuck` and `refused` are the harness
stopping it; `server` and `timeout` are infrastructure.

**`suspect-tests` is a flag, not a verdict.** The loop suspected the suite,
redesigned it, and still failed a check that RAN -- so an implementation and an
expectation disagreed and nothing here can say which is wrong. That is the
boundary the test gate has never crossed: it catches structurally bad suites,
not plausible-but-wrong values. The bucket means *open this transcript*.

Both halves are required, and finding that out cost a wrong fix. Keying on the
redesign marker alone moved all seven failures of 2026-08-09 out of `writer` --
including three OCaml runs whose code genuinely did not compile, which is the
original defect with its sign flipped. A build that never produced a binary is
the writer's however many times the suite was rewritten.

**`unknown` is the load-bearing bucket.** Those markers were read out of
`execute.py`, not out of a live run — the set has never been run against a
server. A failure the classifier cannot place stays visibly `unknown` rather
than falling into `writer`, because a classifier that guessed would
manufacture exactly the false capability result this directory exists to
prevent. `unknown` is printed in the summary even at zero. If a real run
produces any, the classifier is wrong and the transcript says how.

One TSV row per task lands in `$BENCH/<tag>-results.tsv`
(`lang task verdict attempts reason`), so a `python` run and a `c++` run made
hours apart can be compared without re-parsing logs. The classifier is a
convenience over the transcript and never a gate on it: if it cannot be
imported, the runner falls back to the raw verdict line and the run still
finishes.

### Three decisions worth not re-deriving

**Every spec states its own edge cases** — empty input, ties, a single element.
An underspecified spec does not make a harder task, it makes a test designer
invent an expectation: a live run wrote `assert word_count(' ') == 1` against a
correct implementation, and the failure was about the spec rather than the
model.

**The function name is cased to the target language's convention.**
`derive_contract` is never told the language, so a spec naming `is_palindrome`
produces that target in every language — and `lint_tests` matches the target as
a literal substring. A C# suite calling `IsPalindrome` therefore fails "tests
never mention any of `['is_palindrome']`" on every attempt and the run dies at
`attempts=0` with nothing wrong with the model. `batch.sh` emits `SumList` for
C#, `sumList` for JavaScript and `sum_list` for the rest, which removes the
confound in the corpus rather than changing the gate underneath the thing being
measured. That the gate is blind to naming convention is a real defect; it is
recorded, not worked around in `execute.py` by a benchmark.

**Ungrounded by default**, which is the opposite of `ocaml-batch.sh` and for a
measured reason: `sum_list` passed on the first attempt ungrounded and failed
four attempts with the OCaml docs index attached, the retrieved tutorial text
diluting the prompt until the tester reverted to a malformation the prompt had
been suppressing. Retrieval helps where the model is ignorant; Python and C++
are not that case. `STORE` opts in.

OCaml *is* that case, which makes it the one language where the default is the
wrong one — so `batch.sh ocaml` refuses without a `STORE` rather than
documenting the trap. An ungrounded OCaml column would be low for a reason no
later reader could tell apart from a regression.

### Not covered

SQL is excluded. Its implementation is DDL and a check is a row in a table, so
it is not function-shaped and a `{fn}` substitution says nothing — it needs a
parallel corpus, not an entry in this one. `go`, `java` and `swift` are
declared but not runnable and refuse before any model call.

No task returns a map, a tuple or a record, and none indexes a list. A
word-frequency task is one line in Python and a `std::map` / `HashMap` /
`Hashtbl` exercise elsewhere; a binary search penalises OCaml for its list
rather than telling you anything about the model. Both would measure
type-system friction and be read as capability.

---

## The five-task OCaml benchmark

The index is built from ocaml.org's own docs. This exact recipe reproduced a
destroyed index to the chunk (3017), so it is worth following literally:

```bash
D=~/models/bench/ocaml-web            # NOT /tmp -- a cleanup ate the first one
mkdir -p $D && cd $D
curl -sSL -o tut.tar.gz https://github.com/ocaml/ocaml.org/archive/refs/heads/main.tar.gz
tar xzf tut.tar.gz --strip-components=3 \
    ocaml.org-main/data/tutorials ocaml.org-main/data/cookbook
rm -f tut.tar.gz
cp /usr/lib/ocaml/{list,string,option,hashtbl,printf}.mli $D/

cd <repo> && .venv/bin/python -m purecoder.cli \
    --store ~/models/bench/ocaml-idx2 ingest $D -y
```

```bash
scripts/bench/moe-probe.sh                  # find a working GPU/RAM split
scripts/bench/ocaml-batch.sh <tag>          # five tasks, transcripts kept
```
