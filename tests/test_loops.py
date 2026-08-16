"""The write -> validate -> fix loops, driven by a scripted fake model.

These prove the loop's control flow -- that it retries on failure, feeds the
real error back, stops on success, and gives up at max_retries -- without
needing llama-server running.
"""

import json

from conftest import FakeModel

from purecoder.execute import generate_validated_python
from purecoder.scaffold import scaffold_project
from purecoder.validate import generate_validated

GOOD_TESTS = ("assert add(1, 2) == 3\n"
              "assert add(0, 0) == 0\n"
              "assert add(-1, 1) == 0\n")
GOOD_CODE = "def add(a, b):\n    return a + b\n"
BAD_CODE = "def add(a, b):\n    return a - b\n"


# ---- config loop ---------------------------------------------------------

def test_config_loop_returns_on_first_valid_output():
    pc = FakeModel(completions=["HOST=localhost\nPORT=1\n"])
    res = generate_validated(pc, "env", "a config", verbose=False)
    assert res["ok"] and res["attempts"] == 1


def test_config_loop_retries_and_feeds_the_error_back():
    pc = FakeModel(completions=["not a config line\n", "HOST=localhost\n"])
    res = generate_validated(pc, "env", "a config", verbose=False)
    assert res["ok"] and res["attempts"] == 2
    assert "failed validation" in pc.prompts[1]
    assert "not KEY=VALUE" in pc.prompts[1]


def test_config_loop_gives_up_at_max_retries():
    pc = FakeModel(completions=["still prose\n"])
    res = generate_validated(pc, "env", "a config", max_retries=3, verbose=False)
    assert not res["ok"] and res["attempts"] == 3 and res["error"]


def test_config_loop_rejects_unknown_kind():
    import pytest
    with pytest.raises(KeyError):
        generate_validated(FakeModel(), "yaml", "x", verbose=False)


# ---- execution loop ------------------------------------------------------

def test_execution_loop_passes_with_supplied_tests():
    pc = FakeModel(code_outputs=[GOOD_CODE])
    res = generate_validated_python(pc, "add two numbers", tests=GOOD_TESTS,
                                    verbose=False)
    assert res["ok"] and res["attempts"] == 1


def test_the_loop_hands_the_writer_its_language_and_that_language_demands():
    """The link that rotted: every spec declared a `writer_system` and nothing
    read it, so C#'s "no class wrapper, no Main" -- which its harness needs to
    assemble at all -- never reached a prompt.

    Run on a Python-shaped spec so the assertion is about the wiring and not
    about having a .NET SDK; that C# is the language actually making the demand
    is `test_languages.py`'s business.
    """
    import dataclasses

    from purecoder.languages import PYTHON

    spec = dataclasses.replace(PYTHON, writer_system="emit no class wrapper")
    pc = FakeModel(code_outputs=[GOOD_CODE])
    generate_validated_python(pc, "add two numbers", tests=GOOD_TESTS,
                              verbose=False, spec=spec)
    assert pc.code_kwargs[0] == {"language": "python",
                                 "writer_system": "emit no class wrapper"}


def test_the_loop_refuses_an_unvalidatable_language_without_raising():
    """The CLI screens these out, but a library caller can pass one straight in.
    It must come back as the loop's ordinary failure, not an exception: the
    scaffolder has already created the output directory by this point, and an
    exception would leave it half written instead of reporting ok=False."""
    from purecoder.languages import get

    pc = FakeModel(code_outputs=[GOOD_CODE], completions=[GOOD_TESTS])
    res = generate_validated_python(pc, "anything", verbose=False,
                                    spec=get("powerquery"))
    assert res["ok"] is False
    assert "Excel" in res["error"] or "Power BI" in res["error"]
    assert pc.prompts == [], "an unvalidatable language must cost no model call"


def test_the_scaffolder_refuses_an_unwired_language_before_writing_anything(tmp_path):
    """`go` is declared but has no runner and no ProjectSpec, so the scaffolder
    cannot even name its entry file. Refuse up front rather than create the
    directory and fail on the first attribute."""
    from purecoder.languages import get

    out = tmp_path / "proj"
    pc = FakeModel(code_outputs=[GOOD_CODE], completions=[GOOD_TESTS])
    res = scaffold_project(pc, "x", "anything", outdir=str(out),
                           spec=get("go"), use_contract=False, verbose=False)
    assert res["ok"] is False
    assert "not implemented" in res["error"]
    assert not out.exists(), "a refused scaffold must leave no directory behind"


def test_execution_loop_feeds_the_traceback_back_and_converges():
    pc = FakeModel(code_outputs=[BAD_CODE, GOOD_CODE])
    res = generate_validated_python(pc, "add two numbers", tests=GOOD_TESTS,
                                    verbose=False)
    assert res["ok"] and res["attempts"] == 2
    assert "AssertionError" in pc.prompts[1]
    assert "failed these tests" in pc.prompts[1]


def test_execution_loop_gives_up_and_reports_the_error():
    pc = FakeModel(code_outputs=[BAD_CODE])
    res = generate_validated_python(pc, "add two numbers", tests=GOOD_TESTS,
                                    max_retries=2, verbose=False)
    assert not res["ok"] and res["attempts"] == 2
    assert "AssertionError" in res["error"]


def test_an_error_hint_reaches_the_retry_prompt():
    pc = FakeModel(code_outputs=[BAD_CODE, GOOD_CODE])
    res = generate_validated_python(
        pc, "add two numbers", tests=GOOD_TESTS, verbose=False,
        error_hint=lambda err: "the documentation defines List.fold_left")
    assert res["ok"]
    assert "List.fold_left" in pc.prompts[1]


def test_an_error_hint_cannot_change_a_verdict():
    """It is consulted only after a run has already failed, and its text goes
    to the prompt rather than to `error` -- so a hint can neither fail a
    passing run nor pass a failing one."""
    said = []
    pc = FakeModel(code_outputs=[GOOD_CODE])
    res = generate_validated_python(
        pc, "add two numbers", tests=GOOD_TESTS, verbose=False,
        error_hint=lambda err: said.append(err) or "invented nonsense")
    assert res["ok"] and said == [], "a passing run must not consult the docs"


def test_a_hint_does_not_disturb_the_no_progress_signal():
    """The stop-when-stuck check compares the toolchain's last line across
    attempts. Enriching `error` would make an unchanging failure look like a
    moving one, and the loop would keep burning calls."""
    pc = FakeModel(code_outputs=[BAD_CODE])
    res = generate_validated_python(
        pc, "add two numbers", tests=GOOD_TESTS, max_retries=6, verbose=False,
        error_hint=lambda err: f"hint about attempt {len(pc.prompts)}")
    assert not res["ok"]
    assert "identical failures" in res["error"]
    assert "hint about" not in res["error"]


def test_execution_loop_designs_tests_when_none_are_given():
    pc = FakeModel(code_outputs=[GOOD_CODE], completions=[GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", verbose=False)
    assert res["ok"]
    assert res["tests"] == GOOD_TESTS.strip()   # fences stripped


def test_execution_loop_redesigns_tests_rejected_by_the_gate():
    """Gate sees `add` is never called, so the tests get regenerated."""
    off_target = "assert 1 + 1 == 2\nassert len('ab') == 2\nassert True\n"
    pc = FakeModel(code_outputs=[GOOD_CODE], completions=[off_target, GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", verbose=False)
    assert res["ok"]
    assert res["tests"] == GOOD_TESTS.strip()


# ---- scaffolder ----------------------------------------------------------

MAKEFILE = ".PHONY: run\n\nrun:\n\tpython main.py\n"


def test_scaffold_writes_every_artifact(tmp_path):
    pc = FakeModel(
        code_outputs=[GOOD_CODE],
        completions=[GOOD_TESTS, MAKEFILE, "KEY=value\n", "# readme\n"],
    )
    out = tmp_path / "proj"
    res = scaffold_project(pc, "proj", "a project", outdir=str(out),
                           verbose=False)
    for name in ["main.py", "Makefile", ".env", "README.md"]:
        assert (out / name).exists(), f"{name} was not written"
        assert name in res["report"]


def test_scaffold_reports_failure_when_an_artifact_never_validates(tmp_path):
    pc = FakeModel(
        code_outputs=[GOOD_CODE],
        # the .env response never becomes valid, no matter how often it retries
        completions=[GOOD_TESTS, MAKEFILE, "not a config line at all\n"],
    )
    res = scaffold_project(pc, "proj", "a project", outdir=str(tmp_path / "p"),
                           max_retries=2, verbose=False)
    assert not res["ok"]
    assert res["report"][".env"] is False


# ---- contracts -----------------------------------------------------------

CONTRACT = {
    "name": "add",
    "summary": "add two numbers",
    "params": [{"name": "a", "type": "int"}, {"name": "b", "type": "int"}],
    "returns": "int",
    "raises": [],
    "examples": [
        {"in": "1, 2", "out": "3"},
        {"in": "0, 0", "out": "0"},
    ],
}


def test_a_contract_is_derived_and_returned():
    pc = FakeModel(code_outputs=[GOOD_CODE],
                   completions=[json.dumps(CONTRACT), GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", use_contract=True,
                                    verbose=False)
    assert res["ok"]
    assert res["contract"]["name"] == "add"
    assert res["tests"] == GOOD_TESTS.strip()


def test_contract_reaches_the_prompts_alongside_the_prose():
    pc = FakeModel(code_outputs=[GOOD_CODE],
                   completions=[json.dumps(CONTRACT), GOOD_TESTS])
    generate_validated_python(pc, "add two numbers", use_contract=True,
                              verbose=False)
    grounded = [p for p in pc.prompts if "Contract for" in p]
    assert grounded, "no prompt carried the rendered contract"
    assert all("add two numbers" in p for p in grounded)


def test_supplied_contract_skips_derivation():
    pc = FakeModel(code_outputs=[GOOD_CODE], completions=[GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", contract=CONTRACT,
                                    use_contract=True, verbose=False)
    assert res["ok"]
    assert res["contract"] is CONTRACT


def test_a_supplied_contract_is_validated_like_a_derived_one():
    """Passing `contract=` skips derivation; it must not skip validation.

    A malformed dict used to raise KeyError out of render_contract. Graceful
    degradation is the invariant, so it falls back to the plain path.
    """
    pc = FakeModel(code_outputs=[GOOD_CODE], completions=[GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers",
                                    contract={"name": "add"},   # missing keys
                                    verbose=False)
    assert res["ok"]
    assert res["contract"] is None


def test_positional_arguments_still_bind_to_max_retries():
    """`contract`/`use_contract` are keyword-only, so the fourth positional
    argument is max_retries as it always was."""
    pc = FakeModel(code_outputs=[BAD_CODE])
    res = generate_validated_python(pc, "add two numbers", GOOD_TESTS, 2,
                                    verbose=False)
    assert not res["ok"] and res["attempts"] == 2


def test_failed_derivation_falls_back_to_the_plain_path():
    """A dead or unhelpful contract call must never make the tool worse."""
    pc = FakeModel(code_outputs=[GOOD_CODE],
                   completions=["{not json", GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", use_contract=True,
                                    max_retries=1, verbose=False)
    assert res["ok"]
    assert res["contract"] is None


def test_contract_off_by_default_changes_nothing():
    pc = FakeModel(code_outputs=[GOOD_CODE], completions=[GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", verbose=False)
    assert res["ok"]
    assert res["contract"] is None
    assert not any("Contract for" in p for p in pc.prompts)


def test_a_suite_with_no_assertions_is_rejected():
    """The gate's floor applies to whatever the designer wrote. Nothing else
    contributes assertions now, so a suite of none must be sent back."""
    lazy = "x = 1\n"                          # no assertions at all
    pc = FakeModel(code_outputs=[GOOD_CODE],
                   completions=[json.dumps(CONTRACT), lazy, GOOD_TESTS])
    generate_validated_python(pc, "add two numbers", use_contract=True,
                              verbose=False)
    assert any("rejected" in p for p in pc.prompts)


def test_scaffold_grounds_the_code_artifact_in_a_contract(tmp_path):
    pc = FakeModel(
        code_outputs=[GOOD_CODE],
        completions=[json.dumps(CONTRACT), GOOD_TESTS, MAKEFILE,
                     "KEY=value\n", "# readme\n"],
    )
    out = tmp_path / "proj"
    res = scaffold_project(pc, "proj", "a project", outdir=str(out),
                           verbose=False)
    assert res["ok"]
    assert any("Contract for" in p for p in pc.prompts)


def test_scaffold_can_run_without_a_contract(tmp_path):
    pc = FakeModel(
        code_outputs=[GOOD_CODE],
        completions=[GOOD_TESTS, MAKEFILE, "KEY=value\n", "# readme\n"],
    )
    res = scaffold_project(pc, "proj", "a project", outdir=str(tmp_path / "p"),
                           use_contract=False, verbose=False)
    assert res["ok"]
    assert not any("Contract for" in p for p in pc.prompts)


# ---- the loop must not paper over a failed gate --------------------------

def test_a_suite_the_gate_never_accepted_is_not_used():
    """Observed live: the designer was rejected six times, ended with zero
    assertions, and the loop used that suite anyway. An importable module plus
    zero assertions exits 0, so it would have reported success."""
    useless = "x = 1\n"                      # no assertions, ever
    pc = FakeModel(code_outputs=[GOOD_CODE], completions=[useless])
    res = generate_validated_python(pc, "add two numbers", max_retries=2,
                                    verbose=False)
    assert not res["ok"]
    assert "quality gate" in res["error"]


def test_a_missing_dependency_asks_for_stdlib_once_then_stops():
    """Regenerating blind cannot install a package, but the sandbox CAN run
    stdlib code -- so ask once for a stdlib rewrite, then stop rather than
    spend the whole budget on a verdict the executor can never reach."""
    importer = "import definitely_not_a_real_module_xyz\n\ndef add(a, b):\n    return a + b\n"
    pc = FakeModel(code_outputs=[importer], completions=[GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", max_retries=5,
                                    verbose=False)
    assert not res["ok"]
    assert res["attempts"] == 2, "one stdlib retry, then stop"
    assert "definitely_not_a_real_module_xyz" in res["error"]
    assert "cannot validate" in res["error"]
    assert any("standard library" in p for p in pc.prompts), \
        "the retry must actually carry the stdlib constraint"


def test_a_stdlib_rewrite_after_a_missing_dependency_can_succeed():
    """The point of the retry: the second attempt drops the import and passes."""
    importer = "import definitely_not_a_real_module_xyz\n\ndef add(a, b):\n    return a + b\n"
    pc = FakeModel(code_outputs=[importer, GOOD_CODE], completions=[GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", max_retries=5,
                                    verbose=False)
    assert res["ok"]
    assert res["attempts"] == 2


def test_scaffold_shows_the_contract_it_derived(tmp_path, capsys):
    """`project` derives a contract by default -- it must also display it."""
    pc = FakeModel(
        code_outputs=[GOOD_CODE],
        completions=[json.dumps(CONTRACT), GOOD_TESTS, MAKEFILE,
                     "KEY=value\n", "# readme\n"],
    )
    scaffold_project(pc, "proj", "a project", outdir=str(tmp_path / "p"),
                     verbose=True)
    assert "Contract for" in capsys.readouterr().out


def test_the_stdlib_constraint_survives_a_later_unrelated_failure():
    """Observed live: attempt 2 failed on a syntax error, the prompt was
    rebuilt from the bare spec, the constraint was lost, and attempt 3
    re-imported the same missing package."""
    importer = "import definitely_not_a_real_module_xyz\n\ndef add(a, b):\n    return a + b\n"
    broken = "def add(a, b:\n    return a + b\n"          # syntax error
    pc = FakeModel(code_outputs=[importer, broken, GOOD_CODE],
                   completions=[GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", max_retries=4,
                                    verbose=False)
    assert res["ok"], res["error"]
    # every prompt after the nudge must still carry it
    after = pc.prompts[pc.prompts.index(
        next(p for p in pc.prompts if "HARD CONSTRAINT" in p)):]
    assert all("HARD CONSTRAINT" in p for p in after), \
        "the constraint was dropped by a later rebuild"


def test_a_duplicate_entry_point_is_explained_not_just_reported():
    """The other half of the writer demand. A linker saying "multiple
    definition of `main'" is about the assembled file, and the writer only ever
    sees its own output -- so the retry prompt names what the harness already
    provides rather than leaving the model to infer it."""
    import pytest

    from purecoder.languages import get

    spec = get("c++")
    ok, why = spec.available()
    if not ok:
        pytest.skip(why)

    tests = ("int add(int,int);\nvoid pc_tests(){ PC_CHECK(add(1,2)==3); }")
    with_main = ("int add(int a,int b){return a+b;}\n"
                 "int main() { return 0; }\n")
    clean = "int add(int a,int b){return a+b;}\n"
    pc = FakeModel(code_outputs=[with_main, clean], completions=[tests])
    res = generate_validated_python(pc, "add two numbers", spec=spec,
                                    tests=tests, max_retries=3, verbose=False,
                                    timeout=60)
    assert res["ok"], res["error"]
    assert "main" in pc.prompts[-1]
    assert "already provides" in pc.prompts[-1]


def test_the_loop_stops_when_the_same_failure_repeats():
    """Observed live: 5x "API endpoint is unreachable", 4x an identical
    AttributeError, 3x an identical KeyError -- twelve model calls spent after
    the outcome was already decided."""
    pc = FakeModel(code_outputs=[BAD_CODE], completions=[GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", tests=GOOD_TESTS,
                                    max_retries=10, verbose=False)
    assert not res["ok"]
    assert res["attempts"] < 10, "should have stopped short of the budget"
    assert "identical failures" in res["error"]


def test_a_loop_that_makes_progress_is_not_cut_short():
    """Different errors each time means the feedback is landing; keep going."""
    wrong_a = "def add(a, b):\n    return a - b\n"        # AssertionError
    wrong_b = "def add(a, b):\n    return undefined_name\n"  # NameError
    pc = FakeModel(code_outputs=[wrong_a, wrong_b, GOOD_CODE],
                   completions=[GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", tests=GOOD_TESTS,
                                    max_retries=5, verbose=False)
    assert res["ok"], res["error"]
    assert res["attempts"] == 3


def test_a_failure_that_survives_new_code_is_blamed_on_the_tests():
    """The tests do not change between attempts, so an identical failure across
    DIFFERENT generated code is evidence the tests are at fault. Observed live
    on OCaml: the tester emitted invalid source, the compiler said "Syntax
    error", and that went to the writer three times while the writer's own
    output was fine each time."""
    broken_tests = "assert add(1, 2) == 3\nassert add(0 0) == 0\n"   # will not parse
    pc = FakeModel(
        code_outputs=[GOOD_CODE, "def add(a, b):\n    return b + a\n"],
        completions=[broken_tests, GOOD_TESTS],
    )
    res = generate_validated_python(pc, "add two numbers", verbose=False)
    assert res["ok"], res["error"]
    assert res["tests"] == GOOD_TESTS.strip(), "the tests were never redesigned"


def test_the_tests_get_one_second_chance_not_a_loop():
    """A redesign that does not help must not restart the cycle -- that would
    turn a decided outcome into an unbounded spend."""
    broken_tests = "assert add(1, 2 == 3\n"
    pc = FakeModel(code_outputs=[GOOD_CODE], completions=[broken_tests])
    res = generate_validated_python(pc, "add two numbers", max_retries=6,
                                    verbose=False)
    assert not res["ok"]
    # 6 attempts were allowed; the run stops well short of spending them.
    assert res["attempts"] < 6


def test_supplied_tests_that_fail_the_gate_are_reported_not_replaced():
    """A caller who passed tests in owns them. The post-code gate used to
    redesign them, discarding the one thing they asked the code to be checked
    against and then reporting success against tests they never wrote."""
    thin = "assert add(1, 2) == 3\n"          # one assertion; the floor is three
    pc = FakeModel(code_outputs=[GOOD_CODE], completions=[GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", tests=thin,
                                    max_retries=4, verbose=False)
    assert not res["ok"]
    assert "tests you supplied" in res["error"]
    assert res["tests"] == thin, "the caller's tests were swapped out"


def test_supplied_tests_that_pass_the_gate_are_used_as_given():
    pc = FakeModel(code_outputs=[GOOD_CODE])
    res = generate_validated_python(pc, "add two numbers", tests=GOOD_TESTS,
                                    verbose=False)
    assert res["ok"]
    assert res["tests"] == GOOD_TESTS


# ---- declared packages ---------------------------------------------------

def test_a_package_the_sandbox_lacks_is_refused_before_any_model_call():
    """Three attempts of correct code against a package that is not installed
    is the most expensive route to "cannot validate". The refusal names the
    package and the command that would fix it."""
    pc = FakeModel()          # an empty queue: any model call would raise
    res = generate_validated_python(pc, "read a CSV", verbose=False,
                                    packages=("definitely_not_a_real_pkg",))
    assert not res["ok"]
    assert "definitely_not_a_real_pkg" in res["error"]
    assert "pip install" in res["error"]
    assert pc.prompts == [], "the model was asked before the sandbox was checked"


def test_a_declared_package_reaches_the_writer_and_the_tester():
    """A writer allowed numpy and a tester that is not produces assertions
    that cannot run. Both prompts carry the same permission or neither does."""
    code = "import numpy\n\ndef mean(xs):\n    return float(numpy.mean(xs))\n"
    tests = ("assert mean([1, 2, 3]) == 2.0\n"
             "assert mean([0, 4]) == 2.0\n"
             "assert mean([5]) == 5.0\n")
    pc = FakeModel(code_outputs=[code], completions=[tests])
    res = generate_validated_python(pc, "the mean of a list", verbose=False,
                                    packages=("numpy",))
    assert res["ok"], res["error"]
    designer_prompt, writer_prompt = pc.prompts[0], pc.prompts[1]
    assert "numpy" in designer_prompt
    assert "numpy" in writer_prompt


def test_the_stdlib_nudge_keeps_the_declared_packages():
    """Observed shape: a numpy run that hits a DIFFERENT missing import used to
    be told to use only the standard library, dropping the package the caller
    explicitly allowed."""
    importer = ("import numpy, definitely_not_a_real_pkg\n\n"
                "def mean(xs):\n    return 1.0\n")
    fixed = "import numpy\n\ndef mean(xs):\n    return float(numpy.mean(xs))\n"
    tests = ("assert mean([1, 2, 3]) == 2.0\n"
             "assert mean([0, 4]) == 2.0\n"
             "assert mean([5]) == 5.0\n")
    pc = FakeModel(code_outputs=[importer, fixed], completions=[tests])
    res = generate_validated_python(pc, "the mean of a list", max_retries=3,
                                    verbose=False, packages=("numpy",))
    assert res["ok"], res["error"]
    nudge = next(p for p in pc.prompts if "HARD CONSTRAINT" in p)
    assert "numpy" in nudge, "the nudge dropped the package the caller allowed"


def test_declaring_a_package_for_a_language_that_has_no_such_notion_is_refused():
    """`--with numpy` for C++ is a request the pipeline cannot honour. Silently
    ignoring it would generate code under a permission that was never real."""
    from purecoder.languages import get

    res = generate_validated_python(FakeModel(), "add two numbers",
                                    verbose=False, spec=get("c++"),
                                    packages=("numpy",))
    assert not res["ok"]
    assert "c++" in res["error"]


def test_the_retry_shows_the_writer_what_it_wrote_last_time():
    """A fix loop that cannot see what it is fixing is a regeneration loop.
    Observed live on an OCaml bubble sort: four attempts, each starting from
    the spec alone, repeating variants of the same bug -- the failing check
    named the case (`single_element`) but nothing showed the code that failed
    it."""
    pc = FakeModel(code_outputs=[BAD_CODE, GOOD_CODE])
    res = generate_validated_python(pc, "add two numbers", tests=GOOD_TESTS,
                                    verbose=False)
    assert res["ok"]
    assert BAD_CODE.strip() in pc.prompts[1], "the previous attempt was not shown"
    assert "your previous implementation" in pc.prompts[1].lower()


def test_a_previous_attempt_too_large_to_show_is_left_out():
    """Context is the scarce resource on this card, and the project's own
    finding is that feeding full code forward triggers degeneration. A huge
    implementation is summarised by its error alone rather than pasted."""
    huge = "def add(a, b):\n" + "    x = 1\n" * 800 + "    return a - b\n"
    pc = FakeModel(code_outputs=[huge, GOOD_CODE])
    res = generate_validated_python(pc, "add two numbers", tests=GOOD_TESTS,
                                    verbose=False)
    assert res["ok"]
    assert "x = 1\n    x = 1" not in pc.prompts[1]


def test_documentation_reaches_the_writer_and_not_the_test_designer():
    """The two were one string, docs first. Live, asked for `rev_string` with
    OCaml documentation in front of the request, four consecutive designs never
    mentioned rev_string at all -- they tested a StringSet module the docs
    happened to describe, and the run ended having judged no implementation.

    Tests come from the SPEC. Documentation is how the writer learns an
    unfamiliar idiom; it is not part of what was asked for."""
    docs = "Relevant documentation:\nStringSet.of_list builds a set."
    pc = FakeModel(code_outputs=[GOOD_CODE], completions=[GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", context=docs,
                                    verbose=False)
    assert res["ok"], res["error"]

    designer, writer = pc.prompts[0], pc.prompts[1]
    assert "StringSet" not in designer, "the designer was handed the docs"
    assert "add two numbers" in designer
    assert "StringSet" in writer, "the writer lost its grounding"


# ---- TDD mode -------------------------------------------------------------

# Every line calls `add` and asserts something true of any return value at
# all, so a stub returning None satisfies the suite entire. It has to CALL the
# target: the static gate now requires that, and a suite failing to would be
# rejected before the red step ever ran -- proving the wrong thing here.
TAUTOLOGY = ("assert add(1, 2) == add(1, 2)\n"
             "assert add(0, 0) == add(0, 0)\n"
             "assert add(2, 3) is add(2, 3)\n")


def test_tdd_rejects_a_suite_a_do_nothing_implementation_satisfies():
    """The static gate cannot see this: it parses, is not degenerate, and
    calls the target three times. Only running it against a stub does."""
    contract = {"name": "add", "summary": "adds two numbers",
                "params": [{"name": "a", "type": "int"},
                           {"name": "b", "type": "int"}],
                "returns": "int", "raises": [],
                "examples": [{"in": "1, 2", "out": "3"}]}
    pc = FakeModel(code_outputs=[GOOD_CODE],
                   completions=[json.dumps(contract), TAUTOLOGY, GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", verbose=False,
                                    use_contract=True, tdd=True)
    assert res["ok"], res["error"]
    assert res["tests"].strip() == GOOD_TESTS.strip(), "the tautology was used"
    assert any("does nothing" in p for p in pc.prompts), \
        "the designer was never told why its suite was rejected"


def test_tdd_needs_a_contract_to_know_what_to_stub():
    """The stub is `def <name>(*a, **kw)`, and the name comes from the
    contract. Without one there is nothing to stub, and TDD mode says so
    instead of quietly generating the ordinary way."""
    pc = FakeModel(code_outputs=[GOOD_CODE], completions=[GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", verbose=False,
                                    use_contract=False, tdd=True)
    assert not res["ok"]
    assert "contract" in res["error"]


def test_tdd_refuses_a_language_it_cannot_stub():
    from purecoder.languages import get

    res = generate_validated_python(FakeModel(), "add two numbers",
                                    verbose=False, spec=get("c++"), tdd=True)
    assert not res["ok"]
    assert "stub" in res["error"]


def test_the_red_evidence_reaches_the_caller_for_confirmation():
    """"Based on the user's need" means the user sees the tests, and sees them
    failing, before any implementation exists. The loop hands both to a
    callback; the CLI decides how to ask."""
    contract = {"name": "add", "summary": "adds", "params": [],
                "returns": "int", "raises": [],
                "examples": [{"in": "", "out": "3"}]}
    seen = {}

    def confirm(tests, evidence):
        seen["tests"], seen["evidence"] = tests, evidence
        return True

    pc = FakeModel(code_outputs=[GOOD_CODE],
                   completions=[json.dumps(contract), GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", verbose=False,
                                    use_contract=True, tdd=True,
                                    confirm_tests=confirm)
    assert res["ok"], res["error"]
    assert "assert add(1, 2) == 3" in seen["tests"]
    assert "AssertionError" in seen["evidence"], seen["evidence"]


def test_declining_the_tests_stops_before_any_code_is_written():
    contract = {"name": "add", "summary": "adds", "params": [],
                "returns": "int", "raises": [],
                "examples": [{"in": "", "out": "3"}]}
    pc = FakeModel(code_outputs=[GOOD_CODE],
                   completions=[json.dumps(contract), GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", verbose=False,
                                    use_contract=True, tdd=True,
                                    confirm_tests=lambda t, e: False)
    assert not res["ok"]
    assert "declined" in res["error"]
    assert pc.code_kwargs == [], "code was written after the tests were declined"


# ---- retrieval's second pass, keyed on the failure -----------------------

def test_documentation_is_retrieved_again_on_the_error():
    """The first attempt is grounded in what the USER asked for; a retry is
    grounded in what the TOOLCHAIN objected to. The second is the better query
    of the two -- an unbound name states the gap exactly, where prose only
    describes the goal."""
    asked = []

    def error_docs(error):
        asked.append(error)
        return "Documentation for that error:\n\n# doc: lists.md\nList.fold_left"

    pc = FakeModel(code_outputs=[BAD_CODE, GOOD_CODE])
    res = generate_validated_python(
        pc, "add two numbers", tests=GOOD_TESTS, verbose=False,
        error_docs=error_docs)
    assert res["ok"]
    assert asked, "the failure was never used as a query"
    assert "AssertionError" in asked[0], asked[0]
    assert "List.fold_left" in pc.prompts[1]


def test_a_passing_run_retrieves_only_once():
    """The cost is paid where there is evidence it was needed. A run that
    passes first time must not spend an embedding call proving it."""
    asked = []
    pc = FakeModel(code_outputs=[GOOD_CODE])
    res = generate_validated_python(
        pc, "add two numbers", tests=GOOD_TESTS, verbose=False,
        error_docs=lambda err: asked.append(err) or "docs")
    assert res["ok"] and asked == []


def test_retrieved_documentation_stays_out_of_the_verdict():
    """Same rule the did-you-mean hint follows: the text reaches the prompt,
    never `error`. The no-progress check reads the toolchain's last line, and
    enriching it would make an unchanging failure look like a moving one."""
    pc = FakeModel(code_outputs=[BAD_CODE])
    res = generate_validated_python(
        pc, "add two numbers", tests=GOOD_TESTS, max_retries=6, verbose=False,
        error_docs=lambda err: f"docs for attempt {len(pc.prompts)}")
    assert not res["ok"]
    assert "identical failures" in res["error"]
    assert "docs for attempt" not in res["error"]


# ---- attribution, recorded rather than guessed ---------------------------

def test_a_passing_run_records_who_did_the_work():
    pc = FakeModel(code_outputs=[GOOD_CODE])
    res = generate_validated_python(
        pc, "add two numbers", tests=GOOD_TESTS, verbose=False)
    roles = {r["name"]: r for r in res["agents"]["roles"]}
    assert res["agents"]["stopped_on"] == ""
    assert roles["writer"]["attempts"] == 1 and roles["writer"]["accepted"]


def test_the_two_failures_a_verdict_cannot_tell_apart_are_distinguishable():
    """This is the whole point of the ledger.

    Both runs below end `ok=False`, and the score is identical. One is the
    model writing code that does not pass; the other is the harness refusing a
    suite before any code was written. Reading the transcript was the only way
    to tell them apart, which is why `benchlog.py` exists and why it got four
    of seven wrong. The ledger states it.
    """
    # The writer spends every attempt and never passes.
    pc = FakeModel(code_outputs=[BAD_CODE])
    bad_code = generate_validated_python(
        pc, "add two numbers", tests=GOOD_TESTS, max_retries=2, verbose=False)

    # The tester never clears the gate, so the writer is never reached.
    pc2 = FakeModel(code_outputs=[GOOD_CODE], completions=["assert True\n"])
    refused = generate_validated_python(
        pc2, "add two numbers", max_retries=2, verbose=False)

    assert not bad_code["ok"] and not refused["ok"]
    assert bad_code["agents"]["stopped_on"] == "writer"
    assert refused["agents"]["stopped_on"] == "tester"
    # And the one that never reached the writer says so by omission.
    assert "writer" not in {r["name"] for r in refused["agents"]["roles"]}


def test_a_refusal_before_any_role_runs_blames_nobody():
    """An unavailable language is refused before a single model call. There is
    no role to attribute that to, and inventing one would be a lie the UI would
    then display."""
    from purecoder import languages

    res = generate_validated_python(
        None, "anything", spec=languages.get("go"), verbose=False)
    assert not res["ok"]
    assert res["agents"] == {"stopped_on": "", "roles": []}


def test_every_event_names_the_role_that_produced_it():
    seen = []
    pc = FakeModel(code_outputs=[BAD_CODE, GOOD_CODE])
    generate_validated_python(
        pc, "add two numbers", tests=GOOD_TESTS, verbose=False,
        on_event=seen.append)
    assert seen, "the run narrated nothing"
    assert all("agent" in e for e in seen)
