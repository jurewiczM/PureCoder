"""The executor and the test-quality gate -- both model-independent."""

import time

from purecoder.execute import (
    MIN_ASSERTIONS,
    _designer_floor,
    lint_implementation,
    lint_tests,
    missing_dependency,
    public_names,
    run_python,
)

CODE = "def add(a, b):\n    return a + b\n"


# ---- executor ------------------------------------------------------------

def test_run_python_passes_correct_code():
    ok, err = run_python(CODE, "assert add(1, 2) == 3\n")
    assert ok, err


def test_run_python_reports_failed_assertion():
    ok, err = run_python(CODE, "assert add(1, 2) == 4\n")
    assert not ok
    assert "AssertionError" in err


def test_run_python_reports_exception_traceback():
    ok, err = run_python(CODE, "add(1)\n")
    assert not ok
    assert "TypeError" in err


def test_run_python_reports_syntax_error_in_code():
    ok, err = run_python("def add(a, b:\n    return a\n", "assert True\n")
    assert not ok
    assert "SyntaxError" in err


def test_run_python_times_out_on_infinite_loop():
    ok, err = run_python("def spin():\n    while True:\n        pass\n",
                         "spin()\n", timeout=2)
    assert not ok
    assert "timed out" in err


def test_run_python_trims_long_tracebacks():
    """Feedback must stay small on a tight context budget."""
    noisy = "import sys\n" + "".join(f'print("line {i}", file=sys.stderr)\n'
                                     for i in range(100))
    ok, err = run_python(noisy, "raise SystemExit(1)\n")
    assert not ok
    assert len(err.splitlines()) <= 12


# ---- target extraction ---------------------------------------------------

def test_public_names_finds_functions_and_classes():
    src = "def foo():\n    pass\n\nclass Bar:\n    pass\n\ndef _hidden():\n    pass\n"
    assert public_names(src) == ["foo", "Bar"]


def test_public_names_survives_unparseable_code():
    assert public_names("def broken(:\n") == []


# ---- the test-quality gate: one case per observed failure mode ----------

def test_gate_accepts_good_tests():
    tests = ("assert add(1, 2) == 3\n"
             "assert add(0, 0) == 0\n"
             "assert add(-1, 1) == 0\n")
    ok, err = lint_tests(tests, targets=["add"])
    assert ok, err


def test_gate_rejects_unparseable_tests():
    ok, err = lint_tests("assert add(1, 2 == 3\nassert x\nassert y\n")
    assert not ok
    assert "do not parse" in err


def test_gate_rejects_too_few_assertions():
    ok, err = lint_tests("assert add(1, 2) == 3\n", targets=["add"])
    assert not ok
    assert "assertion" in err


def test_gate_rejects_degenerate_repetition():
    ok, err = lint_tests("assert add(1, 2) == 3\n" * 20, targets=["add"])
    assert not ok
    assert "degenerate" in err


def test_gate_rejects_tests_that_never_call_the_target():
    tests = ("assert 1 + 1 == 2\n"
             "assert sorted([2, 1]) == [1, 2]\n"
             "assert len('abc') == 3\n")
    ok, err = lint_tests(tests, targets=["add"])
    assert not ok
    assert "never call" in err


def test_gate_rejects_assertions_on_exception_message_text():
    tests = ("assert add(1, 2) == 3\n"
             "assert add(0, 0) == 0\n"
             "try:\n"
             "    add('a', 1)\n"
             "except TypeError as e:\n"
             "    assert str(e) == 'unsupported operand'\n")
    ok, err = lint_tests(tests, targets=["add"])
    assert not ok
    assert "exception message" in err


def test_gate_rejects_empty_output():
    ok, err = lint_tests("   \n")
    assert not ok


def test_gate_accepts_type_only_exception_tests():
    """The style the prompt asks for must survive the gate."""
    tests = ("assert add(1, 2) == 3\n"
             "assert add(0, 0) == 0\n"
             "try:\n"
             "    add('a', 1)\n"
             "    assert False\n"
             "except TypeError:\n"
             "    pass\n")
    ok, err = lint_tests(tests, targets=["add"])
    assert ok, err


def test_gate_counts_method_calls_as_reaching_the_target():
    """A target reached only as an attribute (obj.parse()) still counts."""
    tests = ("obj = build()\n"
             "assert obj.parse('1') == [1]\n"
             "assert obj.parse('') == []\n"
             "assert obj is not None\n")
    ok, err = lint_tests(tests, targets=["parse"])
    assert ok, err


def test_gate_accepts_a_lower_assertion_floor():
    """With anchors carrying the spec, the designed portion can be smaller."""
    ok, err = lint_tests("assert add(1, 2) == 3\n", targets=["add"],
                         min_assertions=1)
    assert ok, err


# ---- the assertion floor -------------------------------------------------

def test_designer_floor_shrinks_by_the_anchor_count():
    """Anchors count toward the total, so enabling contracts cannot lower it.

    Two anchors leave the designer owing one; the total is still
    MIN_ASSERTIONS. With no anchors the designer owes the whole floor.
    """
    two = "assert add(1, 2) == (3)\nassert add(0, 0) == (0)\n"
    assert _designer_floor(two) == MIN_ASSERTIONS - 2
    assert _designer_floor(two) + 2 == MIN_ASSERTIONS
    assert _designer_floor("") == MIN_ASSERTIONS


def test_designer_floor_never_reaches_zero():
    """However many anchors there are, the designer still owes one test."""
    many = "\n".join(f"assert add({i}, 0) == ({i})" for i in range(10))
    assert _designer_floor(many) == 1


# ---- proof that checks actually ran --------------------------------------

def test_assertions_inside_an_uncalled_function_do_not_count_as_passing():
    """The false green found by the first live run: the designer wrapped its
    asserts in a def nothing calls, the module exited 0, and the loop reported
    success against an implementation returning garbage."""
    code = 'def parse_ports(s):\n    return "garbage"\n'
    tests = 'def test_it():\n    assert parse_ports("80") == [80]\n'

    assert run_python(code, tests)[0] is True          # exit code alone lies
    ok, err = run_python(code, tests, require_checks=1)
    assert not ok
    assert "no checks ran" in err


def test_an_empty_suite_does_not_count_as_passing():
    ok, err = run_python("def f():\n    pass\n", "", require_checks=1)
    assert not ok
    assert "no checks ran" in err


def test_a_raises_only_suite_still_passes():
    """Its success path runs no assert at all -- entering the except handler
    is the check. Counting only asserts would fail correct tests."""
    code = 'def f(x):\n    raise ValueError("nope")\n'
    tests = "try:\n    f(1)\nexcept ValueError:\n    pass\nelse:\n    assert False\n"
    assert run_python(code, tests, require_checks=1)[0], "raises-only suite rejected"


def test_a_raises_only_suite_still_catches_a_wrong_implementation():
    code = "def f(x):\n    return 1\n"
    tests = "try:\n    f(1)\nexcept ValueError:\n    pass\nelse:\n    assert False\n"
    assert not run_python(code, tests, require_checks=1)[0]


def test_instrumentation_leaves_a_normal_passing_suite_passing():
    code = "def add(a, b):\n    return a + b\n"
    tests = "assert add(1, 2) == 3\nassert add(0, 0) == 0\n"
    assert run_python(code, tests, require_checks=1)[0]


def test_instrumentation_still_reports_a_real_failure():
    code = "def add(a, b):\n    return a - b\n"
    ok, err = run_python(code, "assert add(1, 2) == 3\n", require_checks=1)
    assert not ok
    assert "AssertionError" in err
    assert "no checks ran" not in err


def test_unparseable_tests_survive_instrumentation():
    """Instrumentation must not swallow a SyntaxError the executor should report."""
    ok, err = run_python("def f():\n    pass\n", "assert (1 ==\n",
                         require_checks=1)
    assert not ok
    assert "SyntaxError" in err


def test_require_checks_defaults_off():
    """Existing callers keep the old behaviour."""
    code = 'def f():\n    return 1\n'
    assert run_python(code, "def t():\n    assert f() == 999\n")[0] is True


# ---- missing dependencies ------------------------------------------------

def test_missing_dependency_is_identified():
    _, err = run_python("import flask_nonexistent_pkg\n", "assert True\n")
    assert missing_dependency(err) == "flask_nonexistent_pkg"


def test_missing_dependency_returns_none_on_an_ordinary_failure():
    _, err = run_python("def f():\n    return 1\n", "assert f() == 2\n")
    assert missing_dependency(err) is None


# ---- implementations must not carry their own tests ----------------------

def test_implementation_lint_rejects_module_level_asserts():
    """Observed live: the writer copied the tests it was shown into main.py."""
    ok, err = lint_implementation(
        "def f(x):\n    return x\n\nassert f(1) == 1\n")
    assert not ok
    assert "module level" in err


def test_implementation_lint_rejects_a_module_level_test_block():
    code = ("def f(x):\n    return x\n\n"
            "try:\n    f(-1)\nexcept ValueError:\n    pass\nelse:\n    assert False\n")
    ok, err = lint_implementation(code)
    assert not ok
    assert "try/except test block" in err


def test_implementation_lint_allows_asserts_inside_functions():
    ok, err = lint_implementation(
        "def f(x):\n    assert x > 0\n    return x\n")
    assert ok, err


def test_implementation_lint_allows_ordinary_code():
    ok, err = lint_implementation(
        "import random\n\ndef f(n):\n    return [random.random() for _ in range(n)]\n")
    assert ok, err


def test_implementation_lint_defers_syntax_errors_to_the_executor():
    ok, _ = lint_implementation("def broken(:\n")
    assert ok, "the executor reports SyntaxError with a real traceback"


# ---- sandbox hygiene -----------------------------------------------------

def test_a_spawned_child_does_not_outlive_the_timeout(tmp_path):
    """Generated code may spawn servers or workers. Killing only the direct
    child leaves them holding ports, and the NEXT attempt then fails with
    "Address already in use" -- fed back to the model as if its code were
    wrong. Observed live during a scaffold run.

    The child here writes a marker shortly after the parent is killed; if the
    process group was reaped the marker never appears.
    """
    marker = tmp_path / "orphan-was-alive"
    child = f"import time; time.sleep(2); open({str(marker)!r}, 'w').write('x')"
    code = (
        "import subprocess, sys\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        "import time; time.sleep(10)\n"
    )

    run_python(code, "assert True\n", timeout=1)
    time.sleep(3)                       # well past the child's own delay
    assert not marker.exists(), "a spawned child outlived the sandbox"


def test_the_timeout_message_names_the_server_case():
    """A generated web server hangs the executor; the message should say so
    rather than only guessing at an infinite loop."""
    server = ("import socketserver, http.server\n"
              "with socketserver.TCPServer(('127.0.0.1', 0),\n"
              "        http.server.SimpleHTTPRequestHandler) as s:\n"
              "    s.serve_forever()\n")
    ok, err = run_python(server, "assert True\n", timeout=2)
    assert not ok
    assert "timed out" in err
    assert "never returns" in err
