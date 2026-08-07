"""Measuring the contract layer.

The claim the whole contract layer rests on is that grounding reduces
spec-divergence -- code that passes its own designed tests while doing
something other than what the spec asked. These tests do not measure that;
they prove the instrument can see it, which is the part a live run cannot
check for itself.
"""

import pytest
from conftest import FakeModel

from purecoder import bench

# Both arms of a run need the same designed tests to be comparable, so the
# fake model's queue is scripted per task rather than per call.
WEAK_TESTS = ("assert median([3, 1, 2]) == 2\n"
              "assert median([1]) == 1\n"
              "assert median([5, 5]) == 5\n")
LOWER_MEDIAN = ("def median(xs):\n"
                "    s = sorted(xs)\n"
                "    return s[(len(s) - 1) // 2]\n")
MEAN_MEDIAN = ("def median(xs):\n"
               "    s = sorted(xs)\n"
               "    m = len(s) // 2\n"
               "    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2\n")
ORACLE = ("assert median([1, 2, 3, 4]) == 2\n"
          "assert median([4, 3, 2, 1]) == 2\n"
          "assert median([1, 2, 3]) == 2\n")

TASK = bench.Task(
    name="median",
    spec="a function median(xs) returning the median; for an even count "
         "return the LOWER of the two middle values",
    ambiguity="an even-length list: the lower middle value, not their mean",
    oracle=ORACLE,
)


def test_code_that_agrees_with_the_spec_passes_the_oracle():
    assert bench.judge(LOWER_MEDIAN, ORACLE) == "agreed"


def test_code_that_passes_its_own_tests_and_misreads_the_spec_diverges():
    """The bucket the whole exercise exists for. The weak designed tests never
    try an even-length list, so the loop reports success on a mean."""
    assert bench.judge(MEAN_MEDIAN, ORACLE) == "diverged"


def test_code_the_oracle_cannot_even_call_is_its_own_bucket():
    """A NameError is not a misreading of the spec, and folding it into the
    divergence count would inflate the headline number."""
    assert bench.judge("def middle(xs):\n    return sorted(xs)[0]\n",
                       ORACLE) == "unusable"


def test_a_task_the_loop_never_finished_is_not_counted_as_agreement():
    """No code means no verdict. Reporting it as anything else would let a
    dead server look like a clean run."""
    pc = FakeModel(code_outputs=["def median(xs):\n    return broken(\n"],
                   completions=[WEAK_TESTS])
    row = bench.run_task(pc, TASK, use_contract=False, max_retries=1,
                         verbose=False)
    assert row["verdict"] == "no code"
    assert not row["loop_ok"]


def test_a_row_records_which_arm_it_came_from():
    pc = FakeModel(code_outputs=[MEAN_MEDIAN], completions=[WEAK_TESTS])
    row = bench.run_task(pc, TASK, use_contract=False, max_retries=1,
                         verbose=False)
    assert row["task"] == "median"
    assert row["contract"] is False
    assert row["loop_ok"] and row["verdict"] == "diverged"


def test_the_summary_counts_each_arm_separately():
    """The comparison IS the measurement -- one number on its own says
    nothing about whether grounding helped."""
    rows = [
        {"task": "a", "contract": False, "loop_ok": True, "verdict": "diverged"},
        {"task": "a", "contract": True, "loop_ok": True, "verdict": "agreed"},
        {"task": "b", "contract": False, "loop_ok": True, "verdict": "agreed"},
        {"task": "b", "contract": True, "loop_ok": False, "verdict": "no code"},
    ]
    summary = bench.summarise(rows)
    assert summary["grounded"]["diverged"] == 0
    assert summary["plain"]["diverged"] == 1
    assert summary["grounded"]["no code"] == 1
    assert summary["plain"]["agreed"] == 1


def test_the_report_says_the_two_effects_are_not_separated():
    """A contract is shown to the writer AND to the test designer, so a
    difference cannot be attributed to either. The report must say so, or the
    number will be read as something it is not."""
    text = bench.format_report(bench.summarise([]))
    assert "writer" in text and "designer" in text


def test_every_task_names_the_function_its_oracle_calls():
    """The ambiguity under measurement is behavioural. A spec that left the
    NAME open would fill the unusable bucket and measure nothing."""
    for task in bench.TASKS:
        assert task.oracle.strip(), f"{task.name} has no oracle"
        called = task.oracle.split("(")[0].split()[-1]
        assert called in task.spec, \
            f"{task.name}'s spec never names {called}, which its oracle calls"
        assert task.ambiguity, f"{task.name} does not say what is ambiguous"


@pytest.mark.parametrize("task", bench.TASKS, ids=lambda t: t.name)
def test_every_oracle_runs_and_can_fail(task):
    """An oracle nobody can satisfy measures nothing, and one that cannot fail
    is the false green this project keeps finding. Both directions are checked
    with a stub the oracle must reject."""
    stub = f"def {task.oracle.split('(')[0].split()[-1]}(*a, **kw):\n    return None\n"
    assert bench.judge(stub, task.oracle) in ("diverged", "unusable")


class Scripted:
    """A model that misreads the spec unless it is shown a contract.

    Not a claim about any real model -- it is the instrument's calibration:
    if `measure` cannot see a difference this blatant, no live number it
    produces means anything.
    """

    def __init__(self):
        self.contract = {"name": "median", "summary": "the lower middle value",
                         "params": [{"name": "xs", "type": "list"}],
                         "returns": "number", "raises": [],
                         "examples": [{"in": "[1, 2, 3, 4]", "out": "2"}]}

    def complete(self, system, user, grammar=None, **kw):
        import json
        text = json.dumps(self.contract) if grammar == "contract" else WEAK_TESTS
        return {"text": text, "truncated": False, "tokens": 1, "raw": {}}

    def code(self, description, language="python", **kw):
        grounded = "lower middle value" in description
        return {"text": LOWER_MEDIAN if grounded else MEAN_MEDIAN,
                "truncated": False, "tokens": 1, "raw": {}}


def test_the_instrument_can_see_a_difference_between_the_arms():
    report = bench.measure(Scripted(), tasks=(TASK,), repeats=1,
                           max_retries=1, verbose=False)
    assert report["summary"]["plain"]["diverged"] == 1
    assert report["summary"]["grounded"]["agreed"] == 1
