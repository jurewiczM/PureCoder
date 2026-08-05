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

import dataclasses
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .client import strip_fences
from .execute import generate_validated_python, run_candidate
from .languages import (
    RESERVED_NAMES,
    LanguageSpec,
    ProjectSpec,
    get,
    register,
)

# Appended to a correct implementation to produce one that cannot possibly build
# or parse, in any language. Deliberately not a language-specific mistake: this
# probe asks whether an error reaches the fix loop at all.
SYNTAX_GARBAGE = "\n@@@ purecoder syntax probe @@@\n"

# English function words that are ordinary in a sentence and rare as bare
# tokens in code. `if`, `not` and `then` are deliberately absent -- they are
# OCaml.
_ENGLISH = frozenset(("the", "a", "an", "of", "this", "which", "its", "these",
                      "that", "it", "is", "are", "was", "will"))

# Punctuation a line of code almost always carries and a sentence does not.
_CODE_PUNCT = set("={};")


def _is_prose(line: str) -> bool:
    words = re.findall(r"[A-Za-z_]+", line)
    if len(words) < 10 or _CODE_PUNCT & set(line):
        return False
    return sum(1 for w in words if w.lower() in _ENGLISH) >= 3


def strip_prose(text: str) -> str:
    """Drop the explanation a drafting model wrote around its code.

    Observed live: every drafting prompt says "output only source code, no
    prose", and the model appended a paragraph beginning "This OCaml code
    declares a mutable reference..." anyway. `unfence` removes fence markers
    only, so the paragraph reached `ocamlc` as `Error: Syntax error` at line
    14. Two probes failed for it, and the redraft was handed the compiler
    complaining about the model's own English -- which it did not read as an
    instruction to stop writing English.

    The test is conservative in the direction that matters: a filter that eats
    code would fail a probe for a line nobody can see, which is far worse than
    the prose it removes. So a line must be long, carry none of `= { } ;`, and
    use several English function words that are rare as bare tokens in code.
    A comment stays -- it is short, or it carries punctuation, and either way
    the compiler does not mind it.
    """
    return "\n".join(ln for ln in text.splitlines() if not _is_prose(ln))


def strip_echo(text: str, prompt: str) -> str:
    """Drop lines the model copied out of its own instructions.

    Observed live, after the prose filter was already in place: a fixture came
    back containing "and ends with this, which runs the tests:" -- a line of the
    drafting prompt -- and ocamlc failed on it. Eight words, no code
    punctuation, so it sits under the prose filter's threshold, and lowering
    that threshold would start eating real comments.

    This test is exact instead of statistical: the line was in the request. It
    only fires on lines that also look like instructions rather than code, so
    the worked examples -- which are real harness source, quoted in the prompt
    precisely so the model can copy them -- survive.
    """
    request = prompt.lower()
    keep = []
    for line in text.splitlines():
        stripped = line.strip()
        wordy = len(re.findall(r"[A-Za-z_]+", stripped)) >= 5
        if (len(stripped) >= 20 and wordy and not _CODE_PUNCT & set(stripped)
                and stripped.lower() in request):
            continue
        keep.append(line)
    return "\n".join(keep).strip()


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

    It also removes an explanation paragraph, for the same reason and from the
    same live run: markup and prose both reach the compiler as a syntax error
    about something the model did not get wrong. Everything that cleans a
    drafted snippet goes through here, so a new drafting call cannot forget one
    of the two.
    """
    return strip_prose(_FENCE.sub("", text)).strip()


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
        f"counter.\n"
        # The name is a request, not a rule, and saying so is the fix for a
        # live failure: OCaml reserves capitalised identifiers for
        # constructors, so `let PC_CHECK cond =` is `Unbound constructor
        # PC_CHECK`, and four drafting attempts failed on an instruction that
        # could not be followed. `draft_check_call` matches the name case
        # -insensitively, so nothing downstream ever needed the capitals.
        f"If {name}'s naming rules forbid that spelling -- some languages "
        f"reserve capitalised identifiers -- use the closest legal form, such "
        f"as pc_check, and keep it consistent.\n"
        f"Output only that code.{feedback}")
    return strip_echo(unfence(pc.complete(system=system, user=user,
                                          grammar=None,
                                          n_predict=512)["text"]), user)


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
    return strip_echo(unfence(pc.complete(system=system, user=user,
                                          grammar=None,
                                          n_predict=512)["text"]), user)


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
    text = strip_echo(pc.complete(system=system, user=user, grammar=None,
                                  n_predict=768)["text"], user)

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


def writer_system_for(check_call: str, tail_entry: bool) -> str:
    """The writer's extra demand, filled in from what the probes proved.

    Templated for the same reason the tester prompt is, and asymmetric with the
    hand-written entries on purpose. A built-in leaves this empty when a person
    read the harness and judged nothing extra was needed -- C# is the one that
    needed it. A drafted entry has had nobody read anything, and the failure it
    guards is silent: the writer emits a wrapper or a second entry point, the
    assembled file does not build, and the fix loop is handed a linker error
    about `main` for code whose logic was correct.

    Both facts here come from artifacts the probes already ran: `check_call` is
    the helper `draft_check_call` matched against the preamble, and `tail_entry`
    is read off the drafted tail. The concatenation claim is not a claim about
    the language at all -- `LanguageSpec.assemble` really does paste four pieces
    into one file, in that order.
    """
    tail = ("and, below it, the entry point that runs the tests"
            if tail_entry else
            "and runs its statements in order at top level")
    return (f"Your implementation is pasted into a file that already defines "
            f"{check_call} {tail} -- write neither, and put no wrapper class or "
            f"module around your code")


def tail_provides_the_entry_point(harness) -> bool:
    """Whether the drafted tail is the thing that runs the tests.

    Two harness shapes, the same distinction `dangling_calls` draws: a tail
    that calls a name the test snippets define is an entry point (C++'s
    `int main() { pc_tests(); ... }`), and one that calls nothing of theirs is
    reached after the tests have already run (JavaScript's counter check).

    Read from the drafted text rather than asked of the model, because the
    answer is already written down by the time this is needed.
    """
    called = {name for name, opens_block in _APPLIED.findall(harness.epilogue)
              if not opens_block}
    defined = harness.fixture.tests + "\n" + harness.fixture.empty
    return any(re.search(rf"\b{re.escape(name)}\b", defined) for name in called)


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
    # `{src}.ml` passes every other check -- it names {src}, and a build using
    # it still writes to {bin} -- and then asks the toolchain for
    # `candidate.ml.ml`, because the executor's file already carries the
    # extension. Observed live: two probes failed with `I/O error: no such file
    # or directory`, which tells a redrafting model nothing about the cause.
    # Normalised rather than refused, like `./{bin}` already is: the model
    # produced `{src}.ml` on three redrafts running, and a refusal it cannot
    # act on is just a slower failure. Dropping the suffix is safe because the
    # placeholder is a complete path -- there is no reading of `{src}.ml` that
    # is correct.
    line = re.sub(r"(\{(?:src|bin)\})\.[A-Za-z0-9]+", r"\1", line)
    try:
        return tuple(shlex.split(line))
    except ValueError as e:
        raise ValueError(f"the {label} command does not parse: {e}") from e


def draft_commands_with_retry(pc, name: str, extension: str, context: str,
                              max_retries: int = 2):
    """`draft_commands`, with the refusal fed back. -> (build, run, toolchain).

    The harness has been redrafted with its probe diagnostics since the first
    live run; the commands were not, and a single bad sample ended the whole
    run. Observed live: `ocamlc {src}` with no `-o {bin}` -- rejected correctly
    by a check that exists because a build writing nowhere leaves the run
    command with no binary -- on a machine where the previous run had drafted
    the same command correctly. The checks are strict on purpose, which is
    exactly why one sample should not be the verdict.

    The last refusal is raised unchanged when the attempts run out, so a
    language that genuinely cannot be described still fails with the reason.
    """
    feedback = ""
    for attempt in range(1, max(1, max_retries) + 1):
        try:
            return draft_commands(pc, name, extension, context, feedback)
        except ValueError as e:
            if attempt >= max_retries:
                raise
            feedback = (f"\n\nYour previous answer was rejected: {e}. "
                        f"Correct it and output the three lines again.")


def draft_commands(pc, name: str, extension: str, context: str,
                   feedback: str = ""):
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
        f"Use no shell features: no pipes, no redirection, no &&.{feedback}")
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


def confirm_commands(build, run, project=None, ask=input) -> bool:
    """Show the drafted commands and require an explicit yes.

    This is the only place a local model's output becomes a process on the
    user's machine. It is shown in full, and silence is a no. The project
    recipes are shown here too: they end up in a generated Makefile, and the
    layout probe runs `make test` against them.
    """
    print("\nThese commands were drafted from the documentation and will be "
          "run on your machine:")
    print(f"  build : {' '.join(build) if build else '(none)'}")
    print(f"  run   : {' '.join(run)}")
    if project is not None:
        print(f"  and a project of one file, {project.entry}:")
        for target in ("run", "test", "clean"):
            print(f"    make {target:<8}{getattr(project, target)}")
        # Never executed here, so never probed either -- see probe_project.
        print(f"    make install  {project.install}   (never run by purecoder)")
    return ask("Run these? [y/N] ").strip().lower() in ("y", "yes")


# ---- the project layout --------------------------------------------------

# A make recipe IS a shell line -- `g++ ... && ./main` needs `&&`, so the argv
# discipline `_parse_command` enforces cannot apply here. That makes this the
# one place drafted output reaches a shell, so the shell's other powers are
# denied by name. Calibrated against the five hand-written ProjectSpecs: `&&`
# is the only metacharacter any of them uses, plus a `*` glob in one `clean`.
_RECIPE_FORBIDDEN = frozenset("|;<>$`\n")


def _check_recipe(label: str, recipe: str, entry: str = ""):
    """A drafted make recipe, or a refusal naming what it did."""
    found = sorted(set(recipe) & _RECIPE_FORBIDDEN)
    if found:
        raise ValueError(f"the {label} recipe uses shell features "
                         f"({''.join(found)}) that a project of one file has no "
                         f"reason to need: {recipe!r}")
    # `&&` chains two commands and is unavoidable; a single `&` backgrounds one,
    # which would make `make test` exit before the program had run.
    if recipe.replace("&&", "").count("&"):
        raise ValueError(f"the {label} recipe backgrounds a command, so its "
                         f"result would not be waited for: {recipe!r}")
    if entry and entry not in recipe:
        raise ValueError(f"the {label} recipe never names {entry}, so it is not "
                         f"building or running the project at all: {recipe!r}")
    return recipe


def draft_project(pc, name: str, extension: str, context: str):
    """How a one-file project of this language is laid out. -> ProjectSpec.

    Worked examples, like every other prompt here. Three of them, chosen to
    span the axis that matters: Python needs no build, C++ needs one and an
    entry point, JavaScript needs neither but has a real install step.

    Every recipe is checked before it can reach a shell -- see `_check_recipe`,
    and `probe_project` for why `install` is never run at all.
    """
    system = "You output exactly five lines and nothing else."
    user = (
        f"Reference documentation for {name}:\n\n{context}\n\n"
        f"A project in this language is exactly ONE source file. Describe how "
        f"it is laid out and driven by a Makefile.\n\n"
        f"For Python:\n"
        f"ENTRY: main.py\n"
        f"INSTALL: pip install -r requirements.txt\n"
        f"RUN: python main.py\n"
        f"TEST: python main.py\n"
        f"CLEAN: rm -rf __pycache__\n\n"
        f"For C++:\n"
        f"ENTRY: main.cpp\n"
        f"INSTALL: @echo nothing to install\n"
        f"RUN: g++ -std=c++17 main.cpp -o main && ./main\n"
        f"TEST: g++ -std=c++17 main.cpp -o main && ./main\n"
        f"CLEAN: rm -f main\n\n"
        f"For JavaScript:\n"
        f"ENTRY: main.js\n"
        f"INSTALL: npm install\n"
        f"RUN: node main.js\n"
        f"TEST: node main.js\n"
        f"CLEAN: rm -rf node_modules\n\n"
        f"Now for {name}, whose source files end in {extension}. TEST builds "
        f"and runs the single file -- there is no separate test file to find. "
        f"Write exactly those five lines and nothing else.")
    text = strip_fences(pc.complete(system=system, user=user, grammar=None,
                                    n_predict=192)["text"])

    fields = {}
    for raw in text.splitlines():
        key, sep, value = raw.partition(":")
        key = key.strip().upper()
        if sep and key in ("ENTRY", "INSTALL", "RUN", "TEST", "CLEAN"):
            fields.setdefault(key, value.strip())

    missing = [k for k in ("ENTRY", "RUN", "TEST") if not fields.get(k)]
    if missing:
        raise ValueError(f"the project draft has no {', '.join(missing)}: {text!r}")

    entry = fields["ENTRY"]
    # The scaffolder writes this name; a path would escape the project
    # directory, and the wrong suffix would mean the toolchain never sees it.
    if os.path.basename(entry) != entry or entry.startswith("."):
        raise ValueError(f"the entry filename is a path, not a name: {entry!r}")
    if not entry.endswith(extension):
        raise ValueError(f"the entry file {entry!r} does not end in "
                         f"{extension}, so the toolchain would not read it")
    # `run` and `test` additionally have to name the file: a recipe that never
    # touches the entry cannot be building it, and `test` is the one thing here
    # that purecoder itself executes.
    return ProjectSpec(
        entry=entry,
        install=_check_recipe("install",
                              fields.get("INSTALL") or "@echo nothing to install"),
        run=_check_recipe("run", fields["RUN"], entry),
        test=_check_recipe("test", fields["TEST"], entry),
        clean=_check_recipe("clean",
                            fields.get("CLEAN") or "@echo nothing to clean"),
    )


def draft_entry_stub(pc, name: str, context: str, project) -> str:
    """What a built program of this language needs beyond the code itself.

    C++ is why this exists: a scaffolded project compiled clean during
    validation and then failed `make test` with "undefined reference to
    `main`", because the sandbox harness supplies an entry point and the file
    written to disk does not. Observed live.
    """
    system = f"You output only {name} code, or the single word none."
    user = (
        f"Reference documentation for {name}:\n\n{context}\n\n"
        f"A file `{project.entry}` holds one function and nothing else. It is "
        f"then run with: {project.run}\n\n"
        f"If this language needs an entry point for that to work, output it and "
        f"nothing else -- in C++ that is `int main() {{ return 0; }}`. If a "
        f"file of plain definitions runs as it is, as in Python or JavaScript, "
        f"output the single word none.")
    text = unfence(pc.complete(system=system, user=user, grammar=None,
                               n_predict=128)["text"]).strip()
    if not text or text.strip().lower().rstrip(".") == "none":
        return ""
    return "\n\n" + text + "\n"


def _makefile(project) -> str:
    """The four targets, tab-indented, written here rather than by the model.

    The probe is asking whether the RECIPES work. Generating the Makefile with
    the model would put its Makefile-writing between the question and the
    answer.
    """
    return "".join(f"{t}:\n\t{getattr(project, t)}\n"
                   for t in ("install", "run", "test", "clean"))


def probe_project(spec, fixture: Fixture, timeout: int = 60):
    """Does a project of this layout actually build and run? -> (ok, [Probe]).

    Two-sided, like every other probe here: a correct file must make
    `make test` succeed, and a file that cannot possibly parse must make it
    fail. The second is the one that matters -- a `test` recipe that never
    touches the source passes the first probe and proves nothing.

    `make install` is deliberately never run. It installs software, and a
    drafted command is not a good enough reason to do that on someone's
    machine. So the layout is proven to build and run; its install step is
    shown to the user and taken on trust.

    What this does NOT prove is that `make test` runs any tests. For a
    single-file project it builds and runs that file -- which is exactly what
    the hand-written C++ and JavaScript entries do too.
    """
    if not shutil.which("make"):
        return False, [Probe("make is available", False,
                             "make is not installed, so a project layout "
                             "cannot be proven here")]

    results = []
    for label, source, want_ok in (
            ("a project of correct code builds and runs", fixture.correct, True),
            ("a project of broken code fails", fixture.correct + SYNTAX_GARBAGE,
             False)):
        with tempfile.TemporaryDirectory(prefix="pc-project-") as tmp:
            Path(tmp, spec.project.entry).write_text(
                source.rstrip() + spec.project.entry_stub + "\n")
            Path(tmp, "Makefile").write_text(_makefile(spec.project))
            try:
                proc = subprocess.run(["make", "test"], cwd=tmp, timeout=timeout,
                                      capture_output=True, text=True)
                ok, detail = proc.returncode == 0, (proc.stderr or proc.stdout)
            except subprocess.TimeoutExpired:
                ok, detail = False, f"`make test` did not finish in {timeout}s"
        results.append(Probe(label, ok is want_ok, detail[-800:]))
    return all(p.ok for p in results), results


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


# An identifier applied to arguments, with whatever follows the closing paren.
# A definition is followed by a block (`int main() {`, `fn main() {`); a call is
# not (`pc_tests ();`). Without that distinction the tail's own entry point
# looks like something it failed to define.
_APPLIED = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\([^()]*\)\s*(\{)?")


def dangling_calls(harness) -> list:
    """Names the tail calls that nothing else defines.

    Two harness shapes exist. A language needing an entry point has the tail
    provide it and call the tests; a language that runs top-level statements in
    order has the tests already run by the time the tail is reached. Two of the
    three worked examples are the first kind, so the model generalises that
    shape onto languages of the second -- OCaml got a tail calling `pc_tests ()`
    with the tests written as bare statements, three drafts running.

    Feeding back "Unbound value pc_tests" was not enough: the model reads it as
    "define pc_tests" rather than "stop calling it". Naming the contradiction
    is what makes the choice explicit.
    """
    defined = harness.preamble + harness.fixture.tests + harness.fixture.empty
    return sorted({
        name for name, opens_block in _APPLIED.findall(harness.epilogue)
        if not opens_block and not re.search(rf"\b{re.escape(name)}\b", defined)
    })


def shape_feedback(harness) -> str:
    """The hint that turns a repeated dangling call into a decision."""
    dangling = dangling_calls(harness)
    if not dangling:
        return ""
    names = ", ".join(repr(n) for n in dangling)
    return (f"\n\nSpecifically: your ending calls {names}, which nothing else "
            f"defines. Either the test snippets must define it, or -- if this "
            f"language runs top-level statements in order, so the tests have "
            f"already run before the ending is reached -- the ending must not "
            f"call it at all. Choose one and be consistent.")


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
    "layout": "project layout, build and run a program with a Makefile, "
              "install dependencies, clean build output",
}

BUBBLE_SORT = ("a function that takes an array of integers and returns them "
               "sorted in ascending order using bubble sort")


def _failed(error, probes=()):
    return {"ok": False, "probes": list(probes), "error": error}


def learn_language(pc, name: str, extension: str, docs_dir, *, retrieve,
                   confirm=confirm_commands, verbose=True, live_check=True,
                   timeout=60, max_retries=2, docs_store="",
                   want_project=True):
    """Draft a language entry, prove it, and save it.

    -> {ok, probes, error}. Nothing is registered unless every probe passes:
    a drafted spec is a claim until the toolchain says otherwise. On success the
    spec is in the registry, so it is not repeated here.

    `retrieve` takes a query and returns context, injected so the drafting path
    is testable without an embedding model.

    `docs_store` names an index of the same documentation, so generating in
    this language later can read what it was learned from. Recorded on the spec
    rather than assumed from the name: the caller owns the index, and a run
    that never built one must not leave a spec pointing at nothing.

    `want_project` also drafts a one-file project layout. It is proven
    separately and its failure is not the language's: a layout that does not
    build is dropped, and the entry the harness probes earned is still saved.
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
        build, run, toolchain = draft_commands_with_retry(
            pc, name, extension, retrieve(QUERIES["commands"]),
            max_retries=max_retries)
    except ValueError as e:
        return _failed(f"drafting failed: {e}")

    # Drafted before the confirmation so its recipes are shown alongside the
    # build and run commands -- they become a Makefile the user runs, and the
    # layout probe runs `make test` against them.
    #
    # A failure here is not fatal. The layout is a SEPARATE claim from "this
    # language can be generated and validated", and losing the second because
    # the first could not be drafted would throw away work the probes proved.
    project = None
    if want_project:
        # Its own query. "compile one file" and "lay a project out" are
        # different questions, and reusing the first context here would be
        # convenience rather than design.
        context = retrieve(QUERIES["layout"])
        try:
            project = draft_project(pc, name, extension, context)
            project = dataclasses.replace(
                project, entry_stub=draft_entry_stub(pc, name, context, project))
        except ValueError as e:
            log(f"[learn] no project layout drafted ({e}) -- the language can "
                f"still be generated and validated")
            project = None

    # Asked once, outside the retry loop. It prompts on stdin, and the commands
    # are not what a probe failure implicates -- the build ran.
    if not confirm(build, run, project):
        return _failed("declined: the drafted commands were not confirmed, so "
                       "nothing was run and nothing was saved")

    probes = []
    for attempt in range(1, max_retries + 1):
        spec = LanguageSpec(
            name=name, extension=extension, probe=(toolchain, "--version"),
            build=build, run=run, preamble=harness.preamble,
            epilogue=harness.epilogue, check_call=harness.check_call,
            test_system=test_system_for(name, harness.check_call),
            writer_system=writer_system_for(
                harness.check_call, tail_provides_the_entry_point(harness)),
            docs_store=docs_store,
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
            harness = draft_harness(
                pc, name, retrieve,
                probe_feedback(probes) + shape_feedback(harness))
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

    # Attached only once it has been proven, and its failure costs only itself.
    # `project` refusing a language is a smaller loss than `code` refusing it,
    # so a layout that does not build is dropped rather than allowed to sink
    # the entry the harness probes already earned.
    if project is not None:
        candidate = dataclasses.replace(spec, project=project)
        log("[learn] probing the project layout")
        ok, layout_probes = probe_project(candidate, harness.fixture,
                                          timeout=timeout)
        probes += layout_probes
        for probe in layout_probes:
            log(f"[learn]   {'pass' if probe.ok else 'FAIL'}  {probe.name}")
        if ok:
            spec = candidate
        else:
            log(f"[learn] the drafted layout does not build -- registering "
                f"{name} without one, so `project` will refuse it and `code` "
                f"will not")

    path = save(spec, docs_dir=str(docs_dir) if docs_dir else "")
    register(spec)
    log(f"[learn] registered {name} -> {path}")
    return {"ok": True, "probes": probes, "error": ""}
