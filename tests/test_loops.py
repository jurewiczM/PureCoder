"""The write -> validate -> fix loops, driven by a scripted fake model.

These prove the loop's control flow -- that it retries on failure, feeds the
real error back, stops on success, and gives up at max_retries -- without
needing llama-server running.
"""

import json

from purecoder.execute import generate_validated_python
from purecoder.scaffold import scaffold_project
from purecoder.validate import generate_validated


class FakeModel:
    """Returns queued responses in order; records the prompts it was given."""

    def __init__(self, code_outputs=None, completions=None):
        self.code_outputs = list(code_outputs or [])
        self.completions = list(completions or [])
        self.prompts = []

    def _next(self, queue):
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def complete(self, system, user, grammar=None, **kw):
        self.prompts.append(user)
        return {"text": self._next(self.completions), "truncated": False,
                "tokens": 1, "raw": {}}

    def code(self, description, language="python", **kw):
        self.prompts.append(description)
        return {"text": self._next(self.code_outputs), "truncated": False,
                "tokens": 1, "raw": {}}

    def env_file(self, description, **kw):
        return self.complete("", description)

    def makefile(self, description, **kw):
        return self.complete("", description)


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


def test_contract_is_derived_and_its_anchors_reach_the_executor():
    pc = FakeModel(code_outputs=[GOOD_CODE],
                   completions=[json.dumps(CONTRACT), GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", use_contract=True,
                                    verbose=False)
    assert res["ok"]
    assert res["contract"]["name"] == "add"
    assert "assert add(1, 2) == (3)" in res["anchors"]
    assert res["anchors"] in res["tests"]


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

    A malformed dict used to raise KeyError out of anchor_tests. Graceful
    degradation is the invariant, so it falls back to the plain path.
    """
    pc = FakeModel(code_outputs=[GOOD_CODE], completions=[GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers",
                                    contract={"name": "add"},   # missing keys
                                    verbose=False)
    assert res["ok"]
    assert res["contract"] is None
    assert res["anchors"] == ""


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
    assert res["anchors"] == ""


def test_contract_off_by_default_changes_nothing():
    pc = FakeModel(code_outputs=[GOOD_CODE], completions=[GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", verbose=False)
    assert res["ok"]
    assert res["contract"] is None
    assert res["anchors"] == ""
    assert not any("Contract for" in p for p in pc.prompts)


def test_gate_sees_only_the_designed_portion():
    """Free anchors must not satisfy the gate on a lazy tester's behalf.

    The contract yields two anchors. If the gate counted the combined source
    it would see 2 assertions and pass a designer that wrote none. Counting
    the designed portion alone sees 0 and rejects.
    """
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
