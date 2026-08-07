"""
purecoder/bench.py

Measuring the one claim the contract layer rests on.

Every other layer here is validated by a tool: a grammar parses or it does not,
a compiler accepts or it does not, a test runs or it does not. The contract
layer's claim is different in kind -- *grounding the writer and the test
designer in a shared contract reduces spec-divergence* -- and until now it was
argued rather than measured.

Spec-divergence is a specific failure, not a synonym for "wrong": code that
PASSES the tests designed for it while doing something other than what the spec
asked. That is invisible to the pipeline by construction, since the pipeline's
only witness is those same tests. So this module supplies a second witness: a
hand-written oracle per task, never shown to the model, encoding the reading a
careful person would take.

What it cannot do is measure *visibility* -- whether a wrong contract, printed
above the code, would have been caught by someone reading it. That needs a
reader. The honest report says so rather than inventing a proxy: comparing a
model-authored contract's examples against the oracle would mean either
evaluating model-authored expressions (the mistake that cost this project five
false greens and an entire deleted subsystem) or string-matching them, where
`[80, 443]` and `[80,443]` disagree for no reason.
"""

from dataclasses import dataclass

from .execute import generate_validated_python, run_candidate
from .languages import PYTHON

# An oracle failure that is not a misreading: the code does not present the
# interface the spec named, so the oracle cannot even ask it the question.
# Kept separate because folding it into the divergence count would inflate the
# only number this module exists to produce.
_UNUSABLE = ("NameError", "TypeError", "AttributeError", "SyntaxError",
             "IndentationError", "ImportError", "ModuleNotFoundError")


@dataclass(frozen=True)
class Task:
    """One deliberately ambiguous spec, and the reading its oracle enforces.

    `ambiguity` is documentation, not data: it says which phrase the divergence
    hinges on, so a result table can be read without re-deriving it. The spec
    always NAMES the function, because the ambiguity under measurement is
    behavioural -- a spec that left the name open would fill the unusable
    bucket and measure nothing about contracts.
    """

    name: str
    spec: str
    ambiguity: str
    oracle: str


# Five specs, each with one phrase a fast reader drops. Every failure mode here
# was chosen because the WRONG reading is the more natural code: `round` really
# does round halves to even, `dict | dict` really does keep the second value,
# and the mean really is what "median" means for an even count in every other
# definition. The point is not to trick the model but to make the divergence
# mechanical to detect.
TASKS = (
    Task(
        name="median",
        spec="a function median(xs) that returns the median of a list of "
             "numbers; for an even number of values return the LOWER of the "
             "two middle values, not their average",
        ambiguity="even-length input: the lower middle value, not the mean",
        oracle=("assert median([1, 2, 3, 4]) == 2\n"
                "assert median([4, 3, 2, 1]) == 2\n"
                "assert median([1, 2, 3]) == 2\n"
                "assert median([7]) == 7\n"),
    ),
    Task(
        name="merge",
        spec="a function merge(a, b) that merges two dictionaries into a new "
             "one; where both contain the same key, KEEP THE VALUE FROM a and "
             "leave both inputs unchanged",
        ambiguity="collision: the first value wins, where `a | b` keeps the second",
        oracle=("m = merge({'x': 1, 'y': 2}, {'y': 9, 'z': 3})\n"
                "assert m == {'x': 1, 'y': 2, 'z': 3}\n"
                "left = {'k': 1}\n"
                "right = {'k': 2}\n"
                "merge(left, right)\n"
                "assert left == {'k': 1} and right == {'k': 2}\n"),
    ),
    Task(
        name="truncate",
        spec="a function truncate(text, n) that shortens text so the RESULT "
             "IS NEVER LONGER THAN n characters, ending with '...' when "
             "anything was cut",
        ambiguity="the ellipsis counts towards n, rather than being added past it",
        oracle=("assert truncate('abcdefghij', 5) == 'ab...'\n"
                "assert len(truncate('abcdefghij', 4)) == 4\n"
                "assert truncate('abc', 5) == 'abc'\n"
                "assert truncate('abcde', 5) == 'abcde'\n"),
    ),
    Task(
        name="round_half_up",
        spec="a function round_half_up(x, places) that rounds a number to a "
             "number of decimal places, where a value exactly halfway ALWAYS "
             "ROUNDS AWAY FROM ZERO",
        ambiguity="halves: away from zero, where Python's round() goes to even",
        oracle=("assert round_half_up(2.5, 0) == 3\n"
                "assert round_half_up(3.5, 0) == 4\n"
                "assert round_half_up(-2.5, 0) == -3\n"
                "assert round_half_up(1.005, 2) == 1.01\n"),
    ),
    Task(
        name="parse_ports",
        spec="a function parse_ports(text) that turns a comma-separated "
             "string of port numbers into a sorted list of ints, RAISING "
             "ValueError for any port outside 1-65535 rather than skipping it",
        ambiguity="out-of-range: raise, not skip -- the failure seen live",
        oracle=("assert parse_ports('80,443') == [80, 443]\n"
                "assert parse_ports('443,80') == [80, 443]\n"
                "try:\n"
                "    parse_ports('80,70000')\n"
                "except ValueError:\n"
                "    pass\n"
                "else:\n"
                "    assert False\n"),
    ),
)


def judge(code: str, oracle: str, timeout: int = 10) -> str:
    """Run the hidden oracle against generated code. -> the verdict.

    "agreed" -- the code does what the spec asked.
    "diverged" -- an oracle assertion failed: the code works, differently.
    "unusable" -- the oracle could not call it at all, which says nothing
    about the reading and is counted apart.
    """
    ok, err = run_candidate(PYTHON, code, oracle, timeout=timeout,
                            require_checks=1)
    if ok:
        return "agreed"
    return "unusable" if any(e in err for e in _UNUSABLE) else "diverged"


def run_task(pc, task: Task, use_contract: bool, max_retries: int = 3,
             timeout: int = 10, verbose: bool = True) -> dict:
    """One task through one arm of the pipeline. -> a row.

    The oracle is run only if the loop reported success. A task the loop never
    finished has produced no claim to check, and scoring it as agreement or as
    divergence would both be wrong -- a dead server would read as a clean run.
    """
    try:
        result = generate_validated_python(
            pc, task.spec, max_retries=max_retries, timeout=timeout,
            verbose=verbose, use_contract=use_contract)
    except RuntimeError as e:
        # A dead server raises out of the test designer, which is fine for a
        # single generation and fatal for a run of forty. The row records the
        # failure and the next task is attempted -- half a table beats a
        # traceback and nothing.
        return {"task": task.name, "contract": use_contract, "loop_ok": False,
                "verdict": "no code", "error": str(e)}

    verdict = (judge(result["text"], task.oracle, timeout=timeout)
               if result["ok"] else "no code")
    return {"task": task.name, "contract": use_contract,
            "loop_ok": bool(result["ok"]), "verdict": verdict,
            "error": result["error"]}


VERDICTS = ("agreed", "diverged", "unusable", "no code")


def summarise(rows) -> dict:
    """Count each verdict per arm. The comparison IS the measurement -- one
    arm's divergence rate on its own says nothing about grounding."""
    summary = {arm: dict.fromkeys(VERDICTS, 0)
               for arm in ("plain", "grounded")}
    for row in rows:
        arm = "grounded" if row["contract"] else "plain"
        summary[arm][row["verdict"]] = summary[arm].get(row["verdict"], 0) + 1
    return summary


def measure(pc, tasks=TASKS, repeats: int = 1, max_retries: int = 3,
            timeout: int = 10, verbose: bool = True) -> dict:
    """Every task through both arms, `repeats` times. -> {rows, summary}.

    Both arms are run in the same process against the same server so that
    anything but the contract is held constant. Repeats exist because the model
    is sampled, not deterministic: a single pass over five tasks is an anecdote,
    and the report is careful to call it one.
    """
    rows = []
    for _ in range(max(1, repeats)):
        for task in tasks:
            for use_contract in (False, True):
                if verbose:
                    arm = "grounded" if use_contract else "plain"
                    print(f"[bench] {task.name} ({arm})")
                rows.append(run_task(pc, task, use_contract,
                                     max_retries=max_retries, timeout=timeout,
                                     verbose=verbose))
    return {"rows": rows, "summary": summarise(rows)}


def format_report(summary: dict) -> str:
    """The table, and the caveats that keep it from being over-read."""
    lines = ["", "  arm       " + "".join(f"{v:>10}" for v in VERDICTS)]
    for arm in ("plain", "grounded"):
        counts = summary.get(arm, dict.fromkeys(VERDICTS, 0))
        lines.append(f"  {arm:<10}"
                     + "".join(f"{counts.get(v, 0):>10}" for v in VERDICTS))
    lines += [
        "",
        "  diverged = the loop reported success and the hidden oracle "
        "disagreed.",
        "  Read with three caveats:",
        "  - a contract is shown both to the writer and to the test designer,",
        "    so a difference between the arms cannot be attributed to either.",
        "  - whether a wrong contract is VISIBLE to a reader is not measured "
        "here;",
        "    that needs a person, and no proxy for it is honest.",
        "  - the model is sampled. Few tasks and few repeats make this "
        "directional,",
        "    not significant.",
    ]
    return "\n".join(lines)
