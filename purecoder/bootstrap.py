"""
purecoder/bootstrap.py

Drafting a language entry from the language's own documentation, and -- the
part that matters -- proving it before anything is registered.

A drafted LanguageSpec is a claim about a language nobody here has written by
hand. The probes below turn it into a fact. They are the same bar the
hand-written entries meet, run mechanically against a trivial `add(a, b)`: a
harness that cannot fail a wrong implementation is not a language entry, it is
a rubber stamp, and it would make every later run report success.
"""

import re
from dataclasses import dataclass

from .client import strip_fences
from .execute import run_candidate
from .languages import get

# Appended to a correct implementation to produce one that cannot possibly build
# or parse, in any language. Deliberately not a language-specific mistake: this
# probe asks whether an error reaches the fix loop at all.
SYNTAX_GARBAGE = "\n@@@ purecoder syntax probe @@@\n"


@dataclass(frozen=True)
class Fixture:
    """Five snippets in the target language, drafted alongside the harness.

    `empty` is not the empty string: a language whose epilogue calls
    `pc_tests()` needs that function to exist and do nothing, or the probe
    measures a compile error instead of a check that never ran.
    """

    correct: str
    wrong: str
    tests: str
    empty: str
    always_fails: str


@dataclass(frozen=True)
class Probe:
    name: str
    ok: bool
    detail: str


def probe_language(spec, fixture: Fixture, timeout: int = 60):
    """Run every mechanical probe. -> (all passed, [Probe, ...]).

    No probe trusts an exit code alone, because that is the mistake this whole
    project exists to catch.
    """
    results = []

    ok, err = run_candidate(spec, fixture.correct, fixture.tests,
                            timeout=timeout, require_checks=1)
    results.append(Probe("correct implementation passes", ok, err))

    ok, err = run_candidate(spec, fixture.wrong, fixture.tests,
                            timeout=timeout, require_checks=1)
    results.append(Probe("wrong implementation fails", not ok, err))

    ok, err = run_candidate(spec, fixture.correct, fixture.empty,
                            timeout=timeout, require_checks=1)
    results.append(Probe("a suite with no checks fails",
                         not ok and "no checks ran" in err, err))

    ok, err = run_candidate(spec, fixture.correct + SYNTAX_GARBAGE,
                            fixture.tests, timeout=timeout)
    results.append(Probe("a broken implementation reports its error",
                         not ok and bool(err.strip()), err))

    # Separate from the wrong-implementation probe on purpose: this one isolates
    # the helper. A check that cannot fail is invisible when the implementation
    # is also correct, and that combination is the false green.
    ok, err = run_candidate(spec, fixture.correct, fixture.always_fails,
                            timeout=timeout, require_checks=1)
    results.append(Probe("a failing check fails the run", not ok, err))

    return all(p.ok for p in results), results


# ---- drafting ------------------------------------------------------------
#
# Every prompt here is built from WORKED EXAMPLES, never from prose rules. That
# is a measured distinction rather than a style preference: across six models,
# explicit translation rules scored below baseline on a third of the runs, while
# translation examples improved every model above 1B parameters
# (arXiv:2501.19085). The C++, JavaScript and Rust entries are the examples.

EXAMPLE_LANGUAGES = ("c++", "javascript", "rust")


def worked_examples(field: str, names=EXAMPLE_LANGUAGES) -> str:
    """The same field, as written for languages we already run."""
    return "\n\n".join(f"--- {n} ---\n{getattr(get(n), field)}" for n in names)


def draft_preamble(pc, name: str, context: str) -> str:
    """The check helper: prints the failed expression to stderr, exits non-zero,
    and counts successes.

    The fix loop is only as good as the text it feeds back, which is why
    printing the expression is stated as a hard requirement rather than left to
    the model to think of.
    """
    system = (f"You output only {name} source code. No prose, no explanation, "
              f"no code fences.")
    user = (
        f"Here is the same test-harness helper, written for three languages we "
        f"already run:\n\n{worked_examples('preamble')}\n\n"
        f"Reference documentation for {name}:\n\n{context}\n\n"
        f"Write the equivalent for {name}. It must: declare a counter starting "
        f"at zero; define a helper named PC_CHECK taking one boolean "
        f"expression; on failure print \"CHECK FAILED: \" plus the expression "
        f"to standard error and exit with status 1; on success increment the "
        f"counter. Output only that code.")
    return strip_fences(pc.complete(system=system, user=user, grammar=None,
                                    n_predict=512)["text"])


def draft_check_call(pc, name: str, preamble: str) -> str:
    """How the helper is INVOKED, which is not always how it is named.

    Rust's is `pc_check!` while its definition reads `macro_rules! pc_check {`.
    The gate counts this token textually, so getting it wrong makes every suite
    score zero checks -- a silent failure, hence the check against the preamble.
    """
    system = "You output one line of code and nothing else."
    user = (f"This helper is already defined in a {name} file:\n\n{preamble}\n\n"
            f"Write a single line that uses it to check that 1 equals 1.")
    line = strip_fences(pc.complete(system=system, user=user, grammar=None,
                                    n_predict=64)["text"]).strip()

    match = re.search(r"[A-Za-z_][A-Za-z0-9_]*!?", line)
    if not match:
        raise ValueError(f"no call form found in {line!r}")
    call = match.group(0)
    if call.rstrip("!") not in preamble:
        raise ValueError(f"the drafted call {call!r} names a helper the "
                         f"preamble never defines")
    return call


def draft_epilogue(pc, name: str, preamble: str, context: str) -> str:
    """The tail that fails the run when nothing was checked.

    Without it an empty suite exits 0 and the pipeline reports success on
    unverified code -- the exact false green the Python path shipped for months.
    """
    system = (f"You output only {name} source code. No prose, no explanation, "
              f"no code fences.")
    user = (
        f"Here is the same harness tail, written for three languages we already "
        f"run:\n\n{worked_examples('epilogue')}\n\n"
        f"Reference documentation for {name}:\n\n{context}\n\n"
        f"This helper is already defined above it:\n\n{preamble}\n\n"
        f"Write the equivalent tail for {name}. It must run the tests, then, if "
        f"the counter is still zero, print \"no checks ran\" to standard error "
        f"and exit with status 2. Output only that code.")
    return strip_fences(pc.complete(system=system, user=user, grammar=None,
                                    n_predict=512)["text"])


_SECTIONS = ("WRONG", "TESTS", "EMPTY", "ALWAYS_FAILS")


def draft_fixture(pc, name: str, preamble: str, check_call: str) -> Fixture:
    """The five snippets the probes run.

    Delimited rather than parsed: we do not have a parser for this language and
    are not about to write one.
    """
    system = (f"You output only {name} source code and the exact separator "
              f"lines you are given. No prose, no explanation, no fences.")
    user = (
        f"A test harness for {name} defines this:\n\n{preamble}\n\n"
        f"Checks are written {check_call}(expression).\n\n"
        f"Write five snippets, separated by the exact lines shown:\n"
        f"1. a correct `add` function returning the sum of two integers\n"
        f"@@WRONG@@\n"
        f"2. the same function, but returning the difference instead\n"
        f"@@TESTS@@\n"
        f"3. a test body containing exactly three checks: add(1,2) is 3, "
        f"add(0,0) is 0, add(-1,1) is 0\n"
        f"@@EMPTY@@\n"
        f"4. the same test body with no checks in it at all\n"
        f"@@ALWAYS_FAILS@@\n"
        f"5. a test body containing exactly one check that must fail\n\n"
        f"Output the five snippets in that order, with the separator lines "
        f"between them and nothing else.")
    text = strip_fences(pc.complete(system=system, user=user, grammar=None,
                                    n_predict=768)["text"])

    parts = [text]
    for marker in _SECTIONS:
        head, sep, tail = parts[-1].partition(f"@@{marker}@@")
        if not sep:
            raise ValueError(f"the drafted fixture has no @@{marker}@@ section")
        parts[-1] = head
        parts.append(tail)
    return Fixture(*(p.strip() for p in parts))


def test_system_for(name: str, check_call: str) -> str:
    """The tester prompt, filled in rather than asked for.

    This is PureCoder's own discipline -- code-blind tests, no framework, no
    assertions on message text -- and a model writing its own instructions is
    the technique that measured worst. Templated, so it cannot drift.
    """
    return (
        f"You write {name} tests for a described function. Output ONLY the test "
        f"body, in the same form as the example you were shown: no main "
        f"function, no imports, no test framework, no prose, no fences. Assert "
        f"with {check_call}(expr), which is already defined: e.g. "
        f"{check_call}(add(1, 2) == 3). Use {check_call} and nothing else. "
        f"Assume the thing under test is already defined in the same file.")
