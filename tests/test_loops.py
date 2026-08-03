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
