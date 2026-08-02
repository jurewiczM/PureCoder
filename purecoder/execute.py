"""
purecoder/execute.py

Execution-based validation: generate code, generate tests (code-blind),
RUN them together in a sandboxed subprocess, and feed real tracebacks back
into the fix loop. This is where "valid" (parses) becomes "correct" (works).

Key design choice, borrowed from AgentCoder: the test designer does NOT see
the code. Tests come from the SPEC. If the tester saw the buggy code it
might "confirm" the bug. Independent tests are what make the check honest.
"""

import ast
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter

from .anchors import anchor_tests, count_anchors
from .client import strip_fences
from .contract import derive_contract, render_contract, validate_contract

# a test file with fewer than this many assertions isn't a test suite,
# it's a smoke check -- regenerate rather than trust it.
MIN_ASSERTIONS = 3

# the same assertion line repeated more than this is a generation spiral.
MAX_REPEATED_ASSERT = 5

# matches `except SomeError as e: assert str(e) == "..."` style brittleness.
_MSG_ASSERT = re.compile(
    r"assert\s+(str\(|.*\.args|.*\.message)", re.IGNORECASE)


# ---- the executor (model-independent, fully testable) -------------------

def run_python(code: str, tests: str, timeout: int = 10):
    """Concatenate code + tests, run in a subprocess, return (ok, error).

    Safe-ish sandbox: separate process, temp dir as cwd, hard timeout so an
    infinite loop can't hang the pipeline. Returns the traceback on failure
    so the loop can feed it back to the model.
    """
    script = code.rstrip() + "\n\n# ---- tests ----\n" + tests.rstrip() + "\n"
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "candidate.py")
        with open(path, "w") as f:
            f.write(script)
        try:
            proc = subprocess.run(
                [sys.executable, path],
                cwd=d, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return False, (f"execution timed out after {timeout}s "
                           f"(possible infinite loop)")

    if proc.returncode == 0:
        return True, ""
    # non-zero exit: an assert failed or an exception was raised.
    # Prefer stderr (the traceback); trim to the last few lines so the
    # feedback prompt stays small on a tight context budget.
    err = proc.stderr.strip() or proc.stdout.strip() or f"exited {proc.returncode}"
    lines = err.splitlines()
    if len(lines) > 12:
        err = "\n".join(lines[-12:])
    return False, err


# ---- test-quality gate: "who tests the tester" --------------------------

def public_names(code: str):
    """Top-level function/class names defined by `code` -- what tests should call."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    return [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and not n.name.startswith("_")]


def lint_tests(tests: str, targets=None, min_assertions=MIN_ASSERTIONS):
    """Reject structurally bad tests BEFORE they get to judge code.

    Returns (ok, reason). Catches the five failure modes seen in development:
    doesn't parse, calls nothing under test, too few assertions (the floor is
    caller-tunable via min_assertions), degenerate repetition, and asserting
    on exact exception messages.

    It deliberately cannot catch a plausible-but-wrong expected value -- that
    is spec clarity's job, not this gate's. Said plainly rather than implied.
    """
    if not tests.strip():
        return False, "empty test output"

    # mode 1: doesn't parse -- a test file that can't run proves nothing.
    try:
        tree = ast.parse(tests)
    except SyntaxError as e:
        return False, f"tests do not parse: {e.msg} at line {e.lineno}"

    # mode 2: too few assertions.
    asserts = [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
    if len(asserts) < min_assertions:
        return False, (f"only {len(asserts)} assertion(s); "
                       f"need at least {min_assertions}")

    # mode 3: degenerate repetition of one assertion line.
    lines = [ln.strip() for ln in tests.splitlines()
             if ln.strip().startswith("assert")]
    if lines:
        top, count = Counter(lines).most_common(1)[0]
        if count > MAX_REPEATED_ASSERT:
            return False, (f"degenerate tests: identical assertion repeated "
                           f"{count} times -- likely a generation spiral")

    # mode 4: never calls the thing under test.
    if targets:
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        called |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        if not called & set(targets):
            return False, (f"tests never call any of {sorted(targets)} -- "
                           f"they are not testing the target")

    # mode 5: asserting on exact exception text. Messages are not the contract;
    # a correct implementation with different wording would be failed.
    for i, raw in enumerate(tests.splitlines(), 1):
        if _MSG_ASSERT.search(raw.strip()):
            return False, (f"line {i}: asserts on exception message text; "
                           f"assert the exception TYPE instead")

    return True, ""


# ---- test designer (code-blind) -----------------------------------------

TEST_SYSTEM = (
    "You write Python assert-based tests for a described function or class. "
    "Output ONLY test code: assert statements and setup. No prose, no fences. "
    "Assume the thing under test is already defined in the same file; call it "
    "directly. STRICT RULES: Only test behavior the description explicitly "
    "states. Do not invent requirements. Do not test unspecified inputs. Never "
    "assert on exact exception messages -- only assert that the correct "
    "exception TYPE is raised, using try/except/else: call it inside 'try', "
    "'except ThatError: pass', and 'else: assert False'. Never put "
    "'assert False' inside the try -- it would be caught by your own except. "
    "Respect every word of the spec "
    "(if it says 'sorted', expected output must be sorted)."
)

def generate_tests(pc, description: str, n_predict: int = 512) -> str:
    res = pc.complete(system=TEST_SYSTEM, user=description,
                      grammar=None, n_predict=n_predict)
    return strip_fences(res["text"])


def design_tests(pc, description, targets=None, max_retries=3, verbose=True,
                 n_predict=512, min_assertions=MIN_ASSERTIONS):
    """Generate tests and put them through the quality gate, regenerating with
    the reason fed back on rejection. Returns (tests, ok, reason).

    The designer stays code-blind: only the GATE ever sees names from the
    implementation, and only to check the tests call them at all.
    """
    task, tests, reason = description, "", ""
    for attempt in range(1, max_retries + 1):
        tests = generate_tests(pc, task, n_predict=n_predict)
        ok, reason = lint_tests(tests, targets=targets,
                                min_assertions=min_assertions)
        if ok:
            if verbose:
                print(f"[tests] accepted on attempt {attempt} "
                      f"({len(tests.splitlines())} lines)")
            return tests, True, ""
        if verbose:
            print(f"[tests] attempt {attempt} rejected: {reason} -> regenerating")
        task = (f"{description}\n\n"
                f"Your previous tests were rejected: {reason}\n"
                f"Output only corrected test code, nothing else.")
    return tests, False, reason


# ---- the execution fix loop ---------------------------------------------

def _designer_floor(anchors: str) -> int:
    """How many assertions the test designer still owes.

    Anchors count toward the total, so the designer is asked for the
    remainder instead of MIN_ASSERTIONS more on top. The floor never drops
    below 1: a contract must not buy a designer out of writing anything.
    """
    if not anchors:
        return MIN_ASSERTIONS
    return max(1, MIN_ASSERTIONS - count_anchors(anchors))


def generate_validated_python(pc, description, tests=None, max_retries=3,
                              timeout=10, verbose=True, *, contract=None,
                              use_contract=False, **kw):
    """Generate code, run it against (code-blind) tests, retry on failure
    with the traceback fed back.

    With use_contract, the prose is first turned into a contract that both the
    writer and the test designer read, and whose examples become mechanical
    anchor assertions. Returns {ok, text, tests, anchors, contract, attempts,
    error}.
    """
    if use_contract and contract is None:
        contract, cerr = derive_contract(pc, description,
                                         max_retries=max_retries,
                                         verbose=verbose)
        if contract is None and verbose:
            print(f"[contract] {cerr} -> continuing without one")
    elif contract is not None:
        # A caller-supplied contract skipped derivation and therefore skipped
        # validation. Degrade the same way a failed derivation does rather
        # than let a malformed dict raise out of anchor_tests.
        ok, cerr = validate_contract(contract)
        if not ok:
            if verbose:
                print(f"[contract] supplied contract rejected: {cerr} "
                      f"-> continuing without one")
            contract = None

    anchors = ""
    if contract is not None:
        anchors, dropped = anchor_tests(contract)
        if verbose:
            for reason in dropped:
                print(f"[contract] dropped {reason}")

    # Everything downstream reads the contract, never the implementation --
    # the test designer stays code-blind.
    spec = description
    if contract is not None:
        spec = f"{description}\n\n{render_contract(contract)}"

    if tests is None:
        # Anchors already assert everything the contract states, so the
        # designer is asked for what they do NOT cover, and judged on that
        # alone -- the gate never counts free anchors toward the designed
        # portion. The floor shrinks by the anchor count so the TOTAL still
        # reaches MIN_ASSERTIONS: a contract may not lower coverage.
        floor = _designer_floor(anchors)
        ask = spec
        if anchors:
            ask = (f"{spec}\n\nThese cases are already covered and must NOT be "
                   f"repeated:\n{anchors}\n\nWrite only ADDITIONAL tests.")
        designed, _, _ = design_tests(pc, ask, max_retries=max_retries,
                                      verbose=verbose, min_assertions=floor)
    else:
        designed = tests

    def _assemble(designed_src):
        return f"{anchors}\n\n{designed_src}".strip() if anchors else designed_src

    task = spec
    code, error = "", ""
    regated = False          # the target-name check runs once, after code exists

    for attempt in range(1, max_retries + 1):
        res = pc.code(task, language="python", **kw)
        code = res["text"]

        if res["truncated"]:
            error = "output was cut off (hit n_predict)"
            task = f"{spec}\n\nPrevious output was cut off. Be complete but concise."
            if verbose:
                print(f"[attempt {attempt}] truncated -> retrying")
            continue

        # Now that an implementation exists, the gate can also check the tests
        # actually call it -- the one check that needs a name from the code.
        if not regated:
            regated = True
            targets = public_names(code)
            if targets:
                floor = _designer_floor(anchors)
                gate_ok, gate_reason = lint_tests(designed, targets=targets,
                                                  min_assertions=floor)
                if not gate_ok:
                    if verbose:
                        print(f"[tests] post-code gate: {gate_reason} -> redesigning")
                    designed, _, _ = design_tests(pc, spec, targets=targets,
                                                  max_retries=max_retries,
                                                  verbose=verbose,
                                                  min_assertions=floor)

        full = _assemble(designed)
        ok, error = run_python(code, full, timeout=timeout)
        if ok:
            if verbose:
                print(f"[attempt {attempt}] all tests passed")
            return {"ok": True, "text": code, "tests": full, "anchors": anchors,
                    "contract": contract, "attempts": attempt, "error": ""}

        if verbose:
            first = error.splitlines()[-1] if error else "unknown"
            print(f"[attempt {attempt}] tests failed: {first} -> retrying")
        task = (f"{spec}\n\n"
                f"Your previous implementation failed these tests:\n{full}\n\n"
                f"With this error:\n{error}\n\n"
                f"Output only the corrected code, nothing else.")

    return {"ok": False, "text": code, "tests": _assemble(designed),
            "anchors": anchors, "contract": contract,
            "attempts": max_retries, "error": error}
