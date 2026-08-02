"""The executor and the test-quality gate -- both model-independent."""

from purecoder.execute import lint_tests, public_names, run_python

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
