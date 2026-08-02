"""The write -> validate -> fix loops, driven by a scripted fake model.

These prove the loop's control flow -- that it retries on failure, feeds the
real error back, stops on success, and gives up at max_retries -- without
needing llama-server running.
"""

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
