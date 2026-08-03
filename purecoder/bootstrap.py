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
from .languages import RESERVED_NAMES, LanguageSpec, get, register

# Appended to a correct implementation to produce one that cannot possibly build
# or parse, in any language. Deliberately not a language-specific mistake: this
# probe asks whether an error reaches the fix loop at all.
SYNTAX_GARBAGE = "\n@@@ purecoder syntax probe @@@\n"

# A fence marker with its optional language tag, wherever it appears.
_FENCE = re.compile(r"```[A-Za-z0-9_+-]*")


def unfence(text: str) -> str:
    """Remove every fence marker, not only a well-formed surrounding pair.

    `strip_fences` handles the normal case: a fence alone on the first and last
    lines. Drafting hits three shapes it cannot -- a fence per section inside a
    multi-part answer, a closing fence welded to the last statement (`;;` and
    the backticks on one line), and an opening tag with no close. All three were
    observed live on OCaml, and each one reached the compiler as a syntax error
    pointing at the fence rather than at anything the model got wrong.

    Safe because a triple backtick is not valid syntax in any language the
    executor can run -- if it appears, it is markup.
    """
    return _FENCE.sub("", text).strip()


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


def worked_examples(field: str) -> str:
    """The same field, as written for languages we already run."""
    return "\n\n".join(f"--- {n} ---\n{getattr(get(n), field)}"
                       for n in EXAMPLE_LANGUAGES)


def draft_preamble(pc, name: str, context: str, feedback: str = "") -> str:
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
        f"counter. Output only that code.{feedback}")
    return unfence(pc.complete(system=system, user=user, grammar=None,
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

    # The first identifier on the line is not necessarily the call: OCaml came
    # back with `if pc_check (1 = 1) then ...` and the leading token was `if`.
    # Nor does "appears in the preamble" separate them -- `if` occurs in the
    # preamble too, as part of the helper's own body.
    #
    # What does separate them is that the drafting prompt DICTATED the name. So
    # the helper is recognised by name first, in whatever case and with whatever
    # sigil the language attaches (Rust's is `pc_check!`), and the preamble is
    # the fallback for a model that renamed it anyway.
    candidates = [m.group(0) for m in
                  re.finditer(r"[A-Za-z_][A-Za-z0-9_]*!?", line)]
    for call in candidates:
        if re.fullmatch(r"pc_?check!?", call, re.IGNORECASE):
            return call
    for call in candidates:
        if re.search(rf"\b{re.escape(call.rstrip('!'))}\b", preamble):
            return call
    raise ValueError(f"no call in {line!r} names a helper the preamble "
                     f"defines -- the harness and its invocation disagree")


def draft_epilogue(pc, name: str, preamble: str, context: str,
                   feedback: str = "") -> str:
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
        f"Write the equivalent tail for {name}. The tests are already placed "
        f"between that helper and your tail. If {name} needs an entry point "
        f"before any code runs, your tail must provide it and call the tests; "
        f"if {name} runs top-level statements in order, the tests have already "
        f"run by the time your tail is reached, so do not call them again. "
        f"Then, if the counter is still zero, print \"no checks ran\" to "
        f"standard error and exit with status 2. Use fully qualified names for "
        f"anything you did not define. Output only that code.{feedback}")
    return unfence(pc.complete(system=system, user=user, grammar=None,
                               n_predict=512)["text"])


_SECTIONS = ("WRONG", "TESTS", "EMPTY", "ALWAYS_FAILS")


def draft_fixture(pc, name: str, preamble: str, epilogue: str,
                  check_call: str, feedback: str = "") -> Fixture:
    """The five snippets the probes run.

    Delimited rather than parsed: we do not have a parser for this language and
    are not about to write one.

    The epilogue is shown, not just the preamble. It is the code that RUNS the
    tests, and the two have to agree on shape -- observed live on OCaml, where
    the tail called `pc_tests ()` while the tests were written as top-level
    statements, so the harness could not compile no matter how good either half
    was on its own.
    """
    system = (f"You output only {name} source code and the exact separator "
              f"lines you are given. No prose, no explanation, no fences.")
    user = (
        f"A test harness for {name} defines this:\n\n{preamble}\n\n"
        f"and ends with this, which runs the tests:\n\n{epilogue}\n\n"
        f"Checks are written {check_call}(expression). Your snippets sit "
        f"between those two parts, so they must define whatever the ending "
        f"above calls.\n\n"
        f"Write five snippets, separated by the exact lines shown:\n"
        f"1. a correct `add` function returning the sum of two integers\n"
        f"@@WRONG@@\n"
        f"2. the same function, but returning the difference instead\n"
        f"@@TESTS@@\n"
        f"3. a test body containing exactly three checks: add(1,2) is 3, "
        f"add(0,0) is 0, add(-1,1) is 0\n"
        f"@@EMPTY@@\n"
        f"4. the same test body with no checks in it at all -- still valid "
        f"code that the ending above can call, just doing nothing\n"
        f"@@ALWAYS_FAILS@@\n"
        f"5. a test body containing exactly one check that must fail\n\n"
        f"Output the five snippets in that order, with the separator lines "
        f"between them and nothing else.{feedback}")
    text = pc.complete(system=system, user=user, grammar=None,
                       n_predict=768)["text"]

    parts = [text]
    for marker in _SECTIONS:
        head, sep, tail = parts[-1].partition(f"@@{marker}@@")
        if not sep:
            raise ValueError(f"the drafted fixture has no @@{marker}@@ section")
        parts[-1] = head
        parts.append(tail)
    # Per section, not once over the whole response. The model fences each
    # snippet separately, so stripping the outermost pair leaves the inner ```
    # markers embedded mid-fixture -- observed live, where OCaml then failed to
    # parse and the diagnostic pointed at the syntax error rather than the cause.
    return Fixture(*(unfence(p) for p in parts))


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
    return _strip_dot_slash(build), _strip_dot_slash(run), toolchain


def _strip_dot_slash(argv: tuple) -> tuple:
    """`./{bin}` -> `{bin}`.

    The habit is near-universal and the placeholder expands to an absolute
    path, so the `./` resolves it against the working directory instead and the
    run fails. Refusing would be the wrong call for something with exactly one
    correct reading -- observed live, where two of four OCaml drafts wrote it.
    """
    return tuple(a[2:] if a.startswith("./{") else a for a in argv)


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


# ---- redrafting ----------------------------------------------------------

@dataclass(frozen=True)
class Harness:
    """The four drafted pieces that make one candidate, kept together because
    they are drafted, redrafted and probed as a unit."""

    preamble: str
    check_call: str
    epilogue: str
    fixture: Fixture


def draft_harness(pc, name: str, retrieve, feedback: str = "") -> Harness:
    """All four pieces, in dependency order.

    Redrafted whole rather than in part: a compile error on the first probe
    could be the helper, the tail or the fixture, and there is no way to
    attribute it without a parser for the language -- which is the per-language
    surface the registry exists to avoid.
    """
    preamble = draft_preamble(pc, name, retrieve(QUERIES["helper"]), feedback)
    check_call = draft_check_call(pc, name, preamble)
    epilogue = draft_epilogue(pc, name, preamble, retrieve(QUERIES["entry"]),
                              feedback)
    fixture = draft_fixture(pc, name, preamble, epilogue, check_call, feedback)
    return Harness(preamble, check_call, epilogue, fixture)


def probe_feedback(probes, max_lines: int = 8) -> str:
    """What the toolchain said, shaped for the next drafting prompt.

    The probes assemble harness + implementation + tests + tail into one file,
    so the diagnostic is about the whole assembly. Saying that plainly is the
    difference between the model fixing the helper and it rewriting `add`.
    """
    failures = [p for p in probes if not p.ok]
    if not failures:
        return ""

    lines = ["\n\nYour previous attempt was rejected. The harness, an "
             "implementation and its tests are assembled into ONE file and "
             "built, and these checks failed:"]
    for probe in failures:
        lines.append(f"\n- {probe.name}")
        detail = probe.detail.strip()
        if detail:
            lines.extend("  " + ln for ln in
                         detail.splitlines()[:max_lines])
        else:
            lines.append("  (the run succeeded when it should have failed)")
    lines.append("\nFix the cause and output only the corrected code.")
    return "\n".join(lines)


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
    return {"ok": False, "probes": list(probes), "error": error}


def learn_language(pc, name: str, extension: str, docs_dir, *, retrieve,
                   confirm=confirm_commands, verbose=True, live_check=True,
                   timeout=60, max_retries=2):
    """Draft a language entry, prove it, and save it.

    -> {ok, probes, error}. Nothing is registered unless every probe passes:
    a drafted spec is a claim until the toolchain says otherwise. On success the
    spec is in the registry, so it is not repeated here.

    `retrieve` takes a query and returns context, injected so the drafting path
    is testable without an embedding model.
    """
    from .langstore import save

    def log(message):
        if verbose:
            print(message)

    if name in RESERVED_NAMES:
        return _failed(f"{name!r} is a reserved language -- it is either wired "
                       f"already or refused on purpose, and a drafted spec may "
                       f"not replace either")

    log(f"[learn] drafting a {name} harness")
    try:
        harness = draft_harness(pc, name, retrieve)
        build, run, toolchain = draft_commands(pc, name, extension,
                                               retrieve(QUERIES["commands"]))
    except ValueError as e:
        return _failed(f"drafting failed: {e}")

    # Asked once, outside the retry loop. It prompts on stdin, and the commands
    # are not what a probe failure implicates -- the build ran.
    if not confirm(build, run):
        return _failed("declined: the drafted commands were not confirmed, so "
                       "nothing was run and nothing was saved")

    probes = []
    for attempt in range(1, max_retries + 1):
        spec = LanguageSpec(
            name=name, extension=extension, probe=(toolchain, "--version"),
            build=build, run=run, preamble=harness.preamble,
            epilogue=harness.epilogue, check_call=harness.check_call,
            test_system=test_system_for(name, harness.check_call),
        )

        log(f"[learn] probing the candidate (attempt {attempt})")
        ok, probes = probe_language(spec, harness.fixture, timeout=timeout)
        for probe in probes:
            log(f"[learn]   {'pass' if probe.ok else 'FAIL'}  {probe.name}")
        if ok:
            break

        failed = ", ".join(p.name for p in probes if not p.ok)
        if attempt == max_retries:
            return _failed(f"the candidate failed a probe: {failed}",
                           probes=probes)

        # Every other layer in this pipeline feeds its error back and tries
        # again; this one used to refuse on the first bad draft. A live OCaml
        # run reached four of five probes on a single malformed snippet.
        log(f"[learn] {failed} -> redrafting with the diagnostic")
        try:
            harness = draft_harness(pc, name, retrieve, probe_feedback(probes))
        except ValueError as e:
            return _failed(f"redrafting failed: {e}", probes=probes)

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
    return {"ok": True, "probes": probes, "error": ""}
