# Attributing a build failure by whose lines it names

## What was built

A run that exhausted its retries on a toolchain diagnostic was bucketed
`writer` whatever caused it. Now the executor states whose lines the
diagnostic named, and the classifier keys on that:

- `LanguageSpec.regions(code, tests)` — the span of each part of the assembled
  file, derived from the same `_parts` that `assemble` joins.
- `diagnostic_origin` / `origin_note` in `execute.py` — the origin, appended to
  the failure text, because only the executor holds the assembled file.
- `tester-build` and `harness-build` in `benchlog.py`, beside `writer`.

**Conservative in one direction.** If any named line falls in the
implementation, the answer is the implementation however many others do not.
Moving blame off the model requires that nothing points at its code. A
diagnostic naming no line attributes nothing.

## How it was verified

Suite: 664 passed, ruff clean. Each new test fails on the tree without the fix.

Live, both directions, against the running server:

```
# the misattributed case -- this branch has the attribution but NOT the c++ fixes,
# so the failure is identical to the one recorded twice as `writer`
[tester-build  attempts=4   ] cpp/unique   candidate.cpp:44:50: error: macro "PC_CHECK" passed 4 arguments, but takes just 1
cpp: 0/1  -- writer 0 tester-build 1 harness-build 0 ...

# the control: a genuine model error must not be exonerated
[writer        attempts=4   ] ocaml/is_palindrome   Fatal error: exception Invalid_argument("index out of bounds")
ocaml: 0/1  -- writer 1 tester-build 0 harness-build 0 ...
```

## Known gaps

- **The live control is weaker than it looks.** OCaml's failure there is a
  *runtime* error, which names no line, so it attributes nothing and falls
  through to `writer`. That is the right outcome by a path that does not
  exercise region matching. The compile-error-in-candidate-code case is covered
  by `test_the_ocaml_case_that_really_was_the_writers`, not by a live run.
- **Only the last error is attributed.** The loop keeps one `error` across
  attempts; a run whose first three attempts were the writer's and whose fourth
  was the suite's is recorded as the suite's.
- **Nothing reclassifies old transcripts.** The marker is emitted at failure
  time, and a transcript never kept the assembled file, so the misattributions
  already in `~/models/bench` cannot be corrected in place — only re-run.
- **`_LINE_REF` is the ceiling.** A toolchain whose diagnostics that regex does
  not parse attributes nothing and still lands on `writer`. It covers
  gcc/rustc/node/ocaml and the C# parenthesised form; it has not been checked
  against every language in the registry.
- **dotnet emits localised diagnostics on this machine** (`error CS0019: Nie
  można zastosować operatora`). The line/column form still parses, so
  attribution works, but anything matching English compiler prose would not.
- **`suspect-tests` is untouched.** It was misread twice in one day as a model
  verdict, including in a write-up about that misreading. This change does not
  address it; the bucket means *open this transcript* and only its name says so.
