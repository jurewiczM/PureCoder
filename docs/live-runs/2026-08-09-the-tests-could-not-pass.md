# The tests could not pass

_2026-08-09. Qwen3-Coder-30B-A3B-Instruct at Q3_K_M, 1864 MiB of a 6 GB card,
66 tok/s warm. The first live run of the ten-task cross-language benchmark._

The benchmark had been built, plumbed, documented and reviewed without ever
having produced a number. It produced sixty. The headline was

    python 9/10   c++ 10/10   javascript 9/10
    rust  10/10   c#   8/10   ocaml       7/10

and four of the seven failures were correct implementations that no
implementation could have saved.

## What the score said and what the transcripts said

`javascript/unique` failed all four attempts. The implementation:

```js
function unique(lst) {
    const seen = new Set();
    const result = [];
    for (const item of lst) {
        if (!seen.has(item)) { seen.add(item); result.push(item); }
    }
    return result;
}
```

That is correct. The suite judging it:

```js
PC_CHECK(unique([]) === [], 'empty list')
PC_CHECK(unique([1]) === [1], 'single element')
PC_CHECK(unique([1, 2, 3]) === [1, 2, 3], 'no duplicates')
```

`[] === []` is `false` in JavaScript. Every assertion in that suite is false
for every possible input. **The suite could not pass.** C# had the same defect
in a language that gives no warning about it -- `==` on `List<int>` is
reference equality, and the right-hand side is constructed inline:

```csharp
PC_CHECK(Unique(new List<int>()) == new List<int>(), "empty list");
```

Three of the seven failures were this, across two languages and two tasks.

A fourth was a wrong expectation rather than a wrong idiom.
`python/count_vowels` asserted

```python
assert count_vowels('AEIOUbcdEfGhIjKlMnOpQrStUvWxYz') == 10
```

The correct count is 9. Ten is the count *including Y*, and the spec handed to
the test designer says, in those words, "never counting y".

The three genuine failures were all OCaml: `unique` recursed before consing and
so kept the LAST occurrence where the spec demands the first; `is_palindrome`
wrote `return false`, which OCaml reads as an unbound value; `roman` malformed
an array literal. Corrected for the four harness failures, the run reads

    python 10/10   c++ 10/10   javascript 10/10
    rust   10/10   c#  10/10   ocaml       7/10

## The loop knew

This is the part worth keeping. In all four unpassable runs the fix loop
noticed, said so, and could do nothing:

```
[attempt 1] tests failed: CHECK FAILED: empty list -> retrying
[attempt 2] tests failed: CHECK FAILED: empty list -> retrying
[attempt 3] the same failure on different code -- suspecting the tests, redesigning them
[tests] accepted on attempt 1 (6 lines)
[attempt 4] tests failed: CHECK FAILED: empty list -> retrying
```

The no-progress rule fired, the suite was redesigned, and the redesign made the
same mistake. The information needed to attribute the failure correctly was
printed on screen and read by nothing.

## The prompt had already asked

JavaScript's `test_system` contains this sentence, and did before the run:

> For deep equality use `PC_CHECK(JSON.stringify(a) === JSON.stringify(b), 'label')`.

It was ignored across three tasks and one redesign. That is the fourth time
this project has recorded the same shape -- the `.env` rambling comment, SQL's
empty database, OCaml's capitalised `Let` -- and the fix is the same one every
time: a constraint one layer down. `test_fix` now rewrites a comparison against
a container literal into a value comparison, in both languages.

What licenses editing model-authored test code here is the rule `test_fix`
already carries: exactly one possible intent. The right-hand side is a literal
written inline, so it is a new reference by construction and the comparison is
false for every input -- the check cannot have meant what it says. The repair
is anchored on that literal, so `=== 3` and `typeof x === 'string'` are
untouched, and a suite that already obeyed the prompt has no `=== [` to match.
C#'s replacement is fully qualified, because `SequenceEqual` as an extension
method needs `using System.Linq` and `writer_system` forbids using directives.

## The control re-run, and what it turned up

| | before | after |
|---|---|---|
| javascript | 9/10 | **10/10** |
| c# | 8/10 | **9/10** |

All three previously-unpassable tasks pass at attempt 1.

The remaining C# failure is `roman`, which had passed twice before. The writer
emitted `private static string Roman(int num)`, and `static` is CS0106 in a
file-based top-level-statement app. Sampling variance -- but it exposed a
defect worse than the one this run set out to fix.

**The fix loop is shown none of C#'s diagnostics.** `execute.py` ends a failed
run with

```python
return False, _trim(stderr.strip() or stdout.strip() or f"exited {rc}")
```

`or`, so a non-empty stderr discards stdout. For `dotnet run` the streams split
the other way from Python's:

```
stdout:  bad.cs(1,1): error CS0106: Modyfikator "private" jest nieprawidlowy dla tego elementu
stderr:  Kompilacja nie powiodla sie. Napraw bledy kompilacji i uruchom ja ponownie.
```

The second is all the loop ever sees. Every C# compile error reaches the writer
as "compilation failed", with no file, line, code or message, and `roman` spent
four attempts being asked to fix an error it was never shown. The comment on
that line reads "Prefer stderr (the traceback)", which is right for Python and
wrong here. It is locale-independent; the Polish is a smaller, separate issue,
and `error CS0106` with `file(line,col)` is the language-neutral part that
matters. `DOTNET_CLI_UI_LANGUAGE`, `VSLANG` and `LC_ALL` were each tried and
none moves the compiler's message -- the satellite pack follows the OS UI
culture.

## The attribution classifier was wrong in the way it was built to prevent

`benchlog.py` was written the day before this run to answer "which component
refused", because `ok=False attempts=4` reads the same whether the model wrote
bad code or the harness refused good code. Its design note argued that the
load-bearing part was the `unknown` bucket: the markers had been read out of
`execute.py` rather than out of a live run, so anything unrecognised had to
stay visible instead of falling into `writer`.

The run returned **zero `unknown` across sixty tasks.** That looked like
validation and was not. The failure was inside a *recognised* bucket:
`writer` -- documented as "the only bucket that claims anything about the
model" -- accused the model in four of seven failures where the code was
correct. An instrument built to prevent false capability results produced four
of them on its first run.

The marker it needs is in the transcripts already: a run that printed
`suspecting the tests, redesigning them` and still failed is a tester failure,
not a writer failure. Unfixed at time of writing.

## The instrument saturates

Corrected, five of six languages score 10/10 and only OCaml discriminates, and
OCaml only because the model is weak in it. `roman` -- the intended hardest of
the ten, and the reason for a 3/4/3 difficulty ramp -- passed first try in
every language except OCaml.

Two readings, and the difference matters. As a *pipeline* benchmark 53/60 is
not saturated: the pipeline really did fail seven times. As a *capability*
benchmark -- which is what the ramp was designed for, comparing two models or
catching a regression -- it cannot discriminate. That is the same shape as
`bench.py`'s recorded failure: an instrument that is sound and cannot see.

One corpus decision did not survive contact. Every spec states its own edge
cases, and the justification was a recorded defect where an underspecified spec
let a tester invent `assert word_count(' ') == 1`. `count_vowels` says "never
counting y" in plain words and the tester counted Y anyway. Pinning the edge
case in the spec is a prompt, and the rule above applies to it too.

## Scoreboard

Found: six. Fixed: one.

| | |
|---|---|
| JS/C# containers compared by reference | fixed, control re-run verified |
| `execute.py` discards stdout, so C# diagnostics never reach the loop | open |
| `benchlog` attributes tester failures to `writer` | open |
| `count_vowels` tester contradicts an explicit spec | open |
| the corpus saturates as a capability instrument | open |
| `learn` drafts a harness before checking a declared language's toolchain | open, descoped |

The last one was found separately: `learn ocaml` refuses correctly as reserved,
but `learn go` ingests documentation and calls the model before discovering
there is no `go` binary. For a genuinely unknown name that is unavoidable --
nothing knows which binary to look for until the draft names it -- but `go`,
`java` and `swift` are in the registry with a declared probe, so the answer was
knowable before any work happened. Same argument the reserved-name check
already makes, applied to a case it does not cover.

## Method note

Every number above came from a transcript, not from a verdict line. The score
said `javascript 9/10`; the transcripts said `10/10`. Had this run been
recorded from its summary it would have entered the record as a model
capability result, which is the failure the per-task transcripts exist to
prevent and the one this project has now avoided twice by the same means.
