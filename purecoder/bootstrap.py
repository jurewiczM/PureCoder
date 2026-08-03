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
import shlex
from dataclasses import dataclass

from .client import strip_fences
from .execute import generate_validated_python, run_candidate
from .languages import BUILTIN_NAMES, LanguageSpec, get, register

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


# ---- the trust boundary --------------------------------------------------
#
# Every hand-written entry's build and run commands were written by a person.
# These are written by a local model and then handed to subprocess.Popen, which
# is a different trust category from anything else in this codebase. Three
# things follow: argv rather than a shell string, structural checks that the
# command actually names the candidate, and explicit confirmation before the
# first execution.

# Braces are deliberately absent: `{src}` and `{bin}` are the executor's own
# placeholders and must survive. Everything here is shell grammar, which argv
# does not have.
_SHELL_METACHARACTERS = set(";|&$`><()\n")


def _parse_command(label: str, line: str) -> tuple:
    """One drafted line to argv, or a refusal."""
    if line.strip().lower() in ("none", "-", ""):
        return ()
    # Checked on the raw line, before shlex: quoting must not be a way to smuggle
    # a pipeline past the guard.
    found = sorted(set(line) & _SHELL_METACHARACTERS)
    if found:
        raise ValueError(f"the {label} command uses shell syntax ({''.join(found)}) "
                         f"and argv is not a shell: {line!r}")
    try:
        return tuple(shlex.split(line))
    except ValueError as e:
        raise ValueError(f"the {label} command does not parse: {e}") from e


def draft_commands(pc, name: str, extension: str, context: str):
    """How this language compiles and runs ONE file. -> (build, run, toolchain).

    Placeholders are `{src}`, `{bin}` and `{python}`, filled in by the executor.
    The toolchain binary is asked for separately because it cannot be inferred:
    a compiled language runs `{bin}`, so there is nothing in `run` to probe, and
    without it `available()` would return True on a machine with no compiler.
    """
    system = "You output exactly three lines and nothing else."
    user = (
        f"Reference documentation for {name}:\n\n{context}\n\n"
        f"A single source file `candidate{extension}` must be compiled (if the "
        f"language needs it) and run. Write exactly three lines:\n"
        f"BUILD: the compile command, using {{src}} for the source path and "
        f"{{bin}} for the output binary -- or the word none if this language "
        f"needs no compilation step\n"
        f"RUN: the command that runs it, using {{bin}} if you compiled one, "
        f"otherwise {{src}}\n"
        f"TOOLCHAIN: the name of the binary that must be installed for those "
        f"commands to work, on its own\n"
        f"Use no shell features: no pipes, no redirection, no &&.")
    text = strip_fences(pc.complete(system=system, user=user, grammar=None,
                                    n_predict=128)["text"])

    lines = {}
    for raw in text.splitlines():
        key, sep, value = raw.partition(":")
        if sep and key.strip().upper() in ("BUILD", "RUN", "TOOLCHAIN"):
            lines[key.strip().upper()] = value.strip()
    if "RUN" not in lines:
        raise ValueError(f"no RUN command in the draft: {text!r}")

    build = _parse_command("build", lines.get("BUILD", ""))
    run = _parse_command("run", lines["RUN"])

    toolchain = lines.get("TOOLCHAIN", "").strip()
    if not toolchain or len(toolchain.split()) != 1:
        raise ValueError("the draft names no single toolchain binary, so the "
                         "language could not be probed for on this machine")
    if build and not any("{bin}" in a for a in build):
        raise ValueError("the build command never writes to {bin}, so the run "
                         "command would have no binary to execute")
    if not any("{src}" in a or "{bin}" in a for a in run):
        raise ValueError("the run command names neither {src} nor {bin}, so it "
                         "would not run the candidate at all")
    return build, run, toolchain


def confirm_commands(build, run, ask=input) -> bool:
    """Show the drafted commands and require an explicit yes.

    This is the only place a local model's output becomes a process on the
    user's machine. It is shown in full, and silence is a no.
    """
    print("\nThese commands were drafted from the documentation and will be "
          "run on your machine:")
    print(f"  build : {' '.join(build) if build else '(none)'}")
    print(f"  run   : {' '.join(run)}")
    return ask("Run these? [y/N] ").strip().lower() in ("y", "yes")


# ---- the orchestrator ----------------------------------------------------

# Asked of the docs, once each. Retrieval is per-question rather than one broad
# query because the answers live on different pages: how a language prints to
# stderr is rarely documented beside how to compile it.
QUERIES = {
    "helper": "print to standard error, exit with a status code, define a "
              "macro or function taking a boolean",
    "entry": "program entry point, main function, top-level statements",
    "commands": "compile and run a single source file from the command line",
}

BUBBLE_SORT = ("a function that takes an array of integers and returns them "
               "sorted in ascending order using bubble sort")


def _failed(error, probes=()):
    return {"ok": False, "spec": None, "probes": list(probes), "error": error}


def learn_language(pc, name: str, extension: str, docs_dir, *, retrieve,
                   confirm=confirm_commands, verbose=True, live_check=True,
                   timeout=60):
    """Draft a language entry, prove it, and save it.

    -> {ok, spec, probes, error}. Nothing is registered unless every probe
    passes: a drafted spec is a claim until the toolchain says otherwise.

    `retrieve` takes a query and returns context, injected so the drafting path
    is testable without an embedding model.
    """
    from .langstore import save

    def log(message):
        if verbose:
            print(message)

    if name in BUILTIN_NAMES:
        return _failed(f"{name!r} is a built-in language -- a drafted spec may "
                       f"not replace a hand-written one")

    log(f"[learn] drafting a {name} harness")
    try:
        preamble = draft_preamble(pc, name, retrieve(QUERIES["helper"]))
        check_call = draft_check_call(pc, name, preamble)
        epilogue = draft_epilogue(pc, name, preamble, retrieve(QUERIES["entry"]))
        fixture = draft_fixture(pc, name, preamble, check_call)
        build, run, toolchain = draft_commands(pc, name, extension,
                                               retrieve(QUERIES["commands"]))
    except ValueError as e:
        return _failed(f"drafting failed: {e}")

    if not confirm(build, run):
        return _failed("declined: the drafted commands were not confirmed, so "
                       "nothing was run and nothing was saved")

    spec = LanguageSpec(
        name=name, extension=extension, probe=(toolchain, "--version"),
        build=build, run=run, preamble=preamble, epilogue=epilogue,
        test_system=test_system_for(name, check_call), check_call=check_call,
    )

    log("[learn] probing the candidate")
    ok, probes = probe_language(spec, fixture, timeout=timeout)
    for probe in probes:
        log(f"[learn]   {'pass' if probe.ok else 'FAIL'}  {probe.name}")
    if not ok:
        failed = ", ".join(p.name for p in probes if not p.ok)
        return _failed(f"the candidate failed a probe: {failed}", probes=probes)

    # The probes prove the harness can fail wrong code. This proves the writer
    # and the tester can actually work inside it, which is a different claim.
    if live_check:
        log("[learn] one live round: bubble sort")
        # The same timeout the probes used. Defaulting to 10 here would reject a
        # spec that had just passed five probes at 60, on a slow toolchain.
        result = generate_validated_python(pc, BUBBLE_SORT, spec=spec,
                                           verbose=verbose, timeout=timeout)
        if not result["ok"]:
            return _failed(f"the harness runs, but the writer and tester could "
                           f"not work inside it: {result['error']}",
                           probes=probes)

    path = save(spec, docs_dir=str(docs_dir) if docs_dir else "")
    register(spec)
    log(f"[learn] registered {name} -> {path}")
    return {"ok": True, "spec": spec, "probes": probes, "error": ""}
