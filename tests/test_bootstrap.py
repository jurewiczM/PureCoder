"""The bootstrap gate.

A drafted LanguageSpec is a claim. These are the probes that turn it into a
fact -- run against a real toolchain, because a harness that compiles is not a
harness that can fail wrong code, and that difference is the whole point.
"""

import dataclasses

import pytest

from purecoder import bootstrap
from purecoder import languages as L

# The C++ entry is hand-written and known good, so it is the reference the
# probes themselves are tested against.
CPP_FIXTURE = bootstrap.Fixture(
    correct="int add(int a,int b){return a+b;}",
    wrong="int add(int a,int b){return a-b;}",
    tests=("int add(int,int);\nvoid pc_tests(){ PC_CHECK(add(1,2)==3); "
           "PC_CHECK(add(0,0)==0); PC_CHECK(add(-1,1)==0); }"),
    empty="void pc_tests(){ }",
    always_fails="void pc_tests(){ PC_CHECK(1==2); }",
)


def _cpp():
    spec = L.get("c++")
    ok, why = spec.available()
    if not ok:
        pytest.skip(why)
    return spec


def test_a_known_good_language_passes_every_probe():
    ok, results = bootstrap.probe_language(_cpp(), CPP_FIXTURE)
    assert ok, [r for r in results if not r.ok]
    assert len(results) == 5


def test_a_harness_that_cannot_fail_is_rejected():
    """The probe that matters. A check helper that prints on failure but exits
    0 passes "it compiles", "it runs" and "it reports" -- and is worthless.
    This is the false-green class the project keeps rediscovering."""
    spec = dataclasses.replace(_cpp(), preamble=(
        "#include <cstdio>\n"
        "static int pc_checks = 0;\n"
        "#define PC_CHECK(x) do { pc_checks++; } while (0)\n"))
    ok, results = bootstrap.probe_language(spec, CPP_FIXTURE)
    assert not ok
    failed = [r.name for r in results if not r.ok]
    assert "wrong implementation fails" in failed
    assert "a failing check fails the run" in failed


def test_a_harness_that_cannot_count_is_rejected():
    """No "no checks ran" tail: an empty suite exits 0 and reports success."""
    spec = dataclasses.replace(
        _cpp(), epilogue="int main() { pc_tests(); return 0; }\n")
    ok, results = bootstrap.probe_language(spec, CPP_FIXTURE)
    assert not ok
    assert "a suite with no checks fails" in [r.name for r in results if not r.ok]


def test_a_broken_implementation_must_produce_an_error_message():
    _, results = bootstrap.probe_language(_cpp(), CPP_FIXTURE)
    probe = next(r for r in results if "broken" in r.name)
    assert probe.ok and probe.detail.strip(), "no diagnostic to feed the fix loop"


def test_every_probe_carries_a_human_readable_name():
    _, results = bootstrap.probe_language(_cpp(), CPP_FIXTURE)
    assert all(r.name and " " in r.name for r in results)


# ---- drafting ------------------------------------------------------------

class Scripted:
    """Returns queued completions in order and records the prompts it saw."""

    def __init__(self, *completions):
        self.queue = list(completions)
        self.prompts = []

    def complete(self, system, user, grammar=None, **kw):
        self.prompts.append((system, user))
        text = self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]
        return {"text": text, "truncated": False, "tokens": 1, "raw": {}}


def test_worked_examples_carry_real_entries_not_descriptions():
    """Measured result: prose translation rules score BELOW baseline on some
    models, while translation examples never hurt. The prompt must show the C++
    and JavaScript harnesses, not describe them."""
    text = bootstrap.worked_examples("preamble")
    assert "PC_CHECK" in text
    assert "#include <cstdio>" in text          # the real C++ entry
    assert "process.exit(1)" in text            # the real JavaScript entry


def test_the_preamble_prompt_shows_examples_and_the_retrieved_docs():
    pc = Scripted("HELPER CODE")
    out = bootstrap.draft_preamble(pc, "zig", "DOCS ABOUT ZIG")
    assert out == "HELPER CODE"
    _system, user = pc.prompts[0]
    assert "DOCS ABOUT ZIG" in user
    assert "#include <cstdio>" in user, "no worked example in the prompt"


def test_the_check_call_is_extracted_from_a_single_line_answer():
    """Rust's invocation is `pc_check!` while its definition reads
    `macro_rules! pc_check {`, so the form has to be observed, not assumed."""
    assert bootstrap.draft_check_call(Scripted("PC_CHECK(1 == 1);"),
                                      "zig", "PRE PC_CHECK") == "PC_CHECK"
    assert bootstrap.draft_check_call(Scripted("pc_check!(1 == 1);"),
                                      "rs", "PRE pc_check") == "pc_check!"


def test_a_check_call_the_preamble_never_defines_is_refused():
    """The gate counts this token textually. If the preamble does not define
    it, every suite scores zero checks and the language silently fails."""
    with pytest.raises(ValueError, match="never defines"):
        bootstrap.draft_check_call(Scripted("VERIFY(1 == 1);"), "zig", "PRE")


def test_the_fixture_comes_back_as_five_labelled_snippets():
    pc = Scripted("CORRECT\n@@WRONG@@\nWRONG\n@@TESTS@@\nTESTS\n"
                  "@@EMPTY@@\nEMPTY\n@@ALWAYS_FAILS@@\nFAILS")
    fx = bootstrap.draft_fixture(pc, "zig", "PRE", "PC_CHECK")
    assert fx.correct == "CORRECT"
    assert fx.wrong == "WRONG"
    assert fx.tests == "TESTS"
    assert fx.empty == "EMPTY"
    assert fx.always_fails == "FAILS"


def test_a_fixture_missing_a_section_is_refused():
    """The refusal names the FIRST missing section, so a draft that stops early
    reports where it stopped rather than where it was going."""
    with pytest.raises(ValueError, match="ALWAYS_FAILS"):
        bootstrap.draft_fixture(
            Scripted("C\n@@WRONG@@\nW\n@@TESTS@@\nT\n@@EMPTY@@\nE"),
            "zig", "PRE", "PC_CHECK")

    with pytest.raises(ValueError, match="TESTS"):
        bootstrap.draft_fixture(Scripted("C\n@@WRONG@@\nW"), "zig",
                                "PRE", "PC_CHECK")


def test_the_tester_prompt_is_templated_not_drafted():
    """Prose rules the model writes for itself measured below baseline. The
    tester prompt is the project's discipline, so it is filled in, not asked
    for -- and no model call is made to produce it."""
    text = bootstrap.test_system_for("zig", "PC_CHECK")
    assert "zig" in text
    assert "PC_CHECK(expr)" in text
    assert "no prose, no fences" in text.lower()


# ---- the trust boundary --------------------------------------------------

def test_commands_are_parsed_into_argv_not_a_shell_string():
    """These reach subprocess.Popen. A string would mean a shell, and a shell
    means the model can write a pipeline."""
    pc = Scripted("BUILD: zigc -o {bin} {src}\nRUN: {bin}\nTOOLCHAIN: zigc")
    build, run, toolchain = bootstrap.draft_commands(pc, "zig", ".zig", "DOCS")
    assert build == ("zigc", "-o", "{bin}", "{src}")
    assert run == ("{bin}",)
    assert toolchain == "zigc"


def test_an_interpreted_language_needs_no_build():
    pc = Scripted("BUILD: none\nRUN: zig run {src}\nTOOLCHAIN: zig")
    build, run, _ = bootstrap.draft_commands(pc, "zig", ".zig", "DOCS")
    assert build == ()
    assert run == ("zig", "run", "{src}")


def test_the_toolchain_binary_is_required():
    """Without it a compiled language gets no availability probe: run[0] is
    {bin}, so available() would return True on a machine with no compiler and
    --lang zig would be accepted anywhere."""
    with pytest.raises(ValueError, match="toolchain"):
        bootstrap.draft_commands(Scripted("BUILD: none\nRUN: zig run {src}"),
                                 "zig", ".zig", "DOCS")


def test_a_run_command_that_never_names_the_source_is_refused():
    """Without {src} or {bin} the command ignores the candidate entirely and
    every probe would measure whatever it does run."""
    with pytest.raises(ValueError, match="src"):
        bootstrap.draft_commands(
            Scripted("BUILD: none\nRUN: zig version\nTOOLCHAIN: zig"),
            "zig", ".zig", "DOCS")


def test_a_build_command_that_produces_no_binary_is_refused():
    with pytest.raises(ValueError, match="bin"):
        bootstrap.draft_commands(
            Scripted("BUILD: zigc {src}\nRUN: {bin}\nTOOLCHAIN: zigc"),
            "zig", ".zig", "DOCS")


@pytest.mark.parametrize("line", [
    "sh -c 'cat {src} | zig run -'",
    "zig run {src} > /tmp/out",
    "zig build {src} && ./a.out",
    "zig run $(echo {src})",
])
def test_shell_metacharacters_are_refused(line):
    """Not a sandbox, just a closed door: argv is not a shell, so a pipeline
    here means the draft misunderstood the format."""
    with pytest.raises(ValueError, match="shell"):
        bootstrap.draft_commands(
            Scripted(f"BUILD: none\nRUN: {line}\nTOOLCHAIN: zig"),
            "zig", ".zig", "DOCS")


def test_commands_are_shown_in_full_before_confirmation(capsys):
    ok = bootstrap.confirm_commands(("zigc", "{src}"), ("{bin}",),
                                    ask=lambda _: "y")
    assert ok
    out = capsys.readouterr().out
    assert "zigc {src}" in out
    assert "{bin}" in out


@pytest.mark.parametrize("answer", ["", "n", "no", "later", "Y E S"])
def test_anything_but_yes_declines(answer):
    assert bootstrap.confirm_commands(("a",), ("b",),
                                      ask=lambda _: answer) is False


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES", " yes "])
def test_yes_in_any_case_confirms(answer):
    assert bootstrap.confirm_commands(("a",), ("b",),
                                      ask=lambda _: answer) is True
