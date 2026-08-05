"""The executor and the test-quality gate -- both model-independent."""

import time

from purecoder.execute import (
    _trim,
    available_packages,
    harness_collision,
    lint_implementation,
    lint_tests,
    missing_dependency,
    missing_relation,
    public_names,
    quoted_source,
    run_python,
)
from purecoder.languages import get

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


def test_gate_accepts_a_caller_supplied_floor():
    """The floor is tunable; a caller may accept a smaller designed suite."""
    ok, err = lint_tests("assert add(1, 2) == 3\n", targets=["add"],
                         min_assertions=1)
    assert ok, err


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


# ---- what the harness already provides -----------------------------------

def test_an_implementation_that_writes_its_own_entry_point_is_named():
    """The failure the writer demand exists to prevent, seen from the other
    side: the harness's tail defines main(), the writer defines one too, and
    the toolchain reports a duplicate symbol that names neither cause."""
    spec = get("c++")
    hint = harness_collision(spec, "int add(int a,int b){return a+b;}\n"
                                   "int main() { return 0; }\n")
    assert "main" in hint
    assert "already" in hint


def test_an_implementation_that_defines_the_test_function_is_named():
    """The likelier collision in a C++-shaped harness: the tail advertises
    `pc_tests()`, so the writer helpfully defines one -- and it is the TESTS
    that already do, not the preamble or the tail."""
    tests = "int add(int,int);\nvoid pc_tests(){ PC_CHECK(add(1,2)==3); }"
    hint = harness_collision(get("c++"), "int add(int a,int b){return a+b;}\n"
                                         "void pc_tests(){ }\n", tests)
    assert "pc_tests" in hint


def test_a_forward_declaration_is_not_a_definition():
    """The C++ tests open with `int add(int,int);` -- no brace, no collision.
    Comparing against the tests must not make every implementation collide with
    its own declaration."""
    tests = "int add(int,int);\nvoid pc_tests(){ PC_CHECK(add(1,2)==3); }"
    assert harness_collision(get("c++"), "int add(int a,int b){return a+b;}\n",
                             tests) == ""


def test_an_implementation_that_touches_the_check_helper_is_named():
    """PC_CHECK belongs to the harness and to the tests. An implementation
    using it is either asserting or redefining, and both break the file."""
    hint = harness_collision(get("c++"), "int add(int a,int b){ PC_CHECK(a>0); "
                                         "return a+b; }\n")
    assert "PC_CHECK" in hint


def test_an_implementation_that_collides_with_nothing_gets_no_hint():
    """It is appended to a retry prompt, so a false positive costs context and
    points the model at code that is fine."""
    assert harness_collision(get("c++"),
                             "int add(int a,int b){ if (a>0) { return a+b; } "
                             "return b; }\n") == ""


def test_a_language_with_no_harness_never_collides():
    """Python has no preamble and no tail -- there is nothing to collide with,
    and its own duplicate definitions are legal."""
    assert harness_collision(get("python"),
                             "def main():\n    return 1\n") == ""


# ---- feedback quality ----------------------------------------------------

def test_trim_keeps_the_first_diagnostics_of_a_compiler_error():
    """A compiler puts its signal FIRST and then draws carets under the
    source. Tailing a g++ error yields "|  ^" and nothing else -- observed
    live, where the loop failed three identical times because that was the
    whole message the model received."""
    err = "\n".join(
        [f"cand.cpp:{i}:1: error: 'X' was not declared in this scope" for i in range(3)]
        + ["   21 |     PC_CHECK(add(INT_MAX, 1));", "      |              ^~~~~~~"] * 8
    )
    out = _trim(err)
    assert "was not declared" in out
    assert out.splitlines()[0].endswith("in this scope")


def test_trim_keeps_the_last_lines_of_a_python_traceback():
    """Python's signal is at the END -- the exception, not the frames."""
    err = "\n".join([f'  File "x.py", line {i}, in f' for i in range(30)]
                    + ["AssertionError: the actual failure"])
    out = _trim(err)
    assert out.splitlines()[-1] == "AssertionError: the actual failure"


def test_trim_leaves_short_errors_alone():
    assert _trim("one\ntwo") == "one\ntwo"


# ---- declared packages ---------------------------------------------------

def test_a_package_the_sandbox_has_is_reported_present():
    """The sandbox runs `sys.executable`, so it inherits this environment --
    numpy is importable here and therefore validatable, which the old boundary
    text denied."""
    ok, missing = available_packages(("numpy",))
    assert ok and missing == []


def test_a_package_the_sandbox_lacks_is_named():
    ok, missing = available_packages(("numpy", "definitely_not_a_real_pkg"))
    assert not ok
    assert missing == ["definitely_not_a_real_pkg"]


def test_nothing_declared_needs_no_subprocess():
    assert available_packages(()) == (True, [])


def test_a_missing_table_is_named_as_something_the_code_should_create():
    """Live finding, twice over. Asked for a view over `orders`, the writer
    emitted a correct view and no table; `no such table: main.orders` went back
    three times unchanged and the loop finally blamed the tests. A stronger
    writer prompt did not fix it -- the same lesson as the .env comment, where
    the system prompt was ignored and the mechanical constraint worked."""
    hint = missing_relation(get("sql"), "sqlite3.OperationalError: no such "
                                        "table: main.orders")
    assert "orders" in hint
    assert "CREATE TABLE" in hint


def test_a_missing_view_is_named_too():
    hint = missing_relation(get("sql"), "sqlite3.OperationalError: no such "
                                        "view: summary")
    assert "summary" in hint


def test_a_missing_column_is_not_answered_with_a_new_table():
    """The first version of this hint told the model to `CREATE TABLE id` in
    answer to `no such column: id`, and it did exactly that. Observed on the
    live run that was verifying the hint itself."""
    hint = missing_relation(get("sql"), "sqlite3.OperationalError: no such "
                                        "column: id")
    assert "CREATE TABLE id" not in hint
    assert "column" in hint


def test_an_unrelated_sql_error_gets_no_schema_hint():
    assert missing_relation(get("sql"), "sqlite3.OperationalError: near "
                                        "\"SELEKT\": syntax error") == ""


def test_no_schema_hint_for_a_language_without_a_schema():
    assert missing_relation(get("python"), "no such table: orders") == ""


# ---- a diagnostic that names a line nobody was shown ---------------------

OCAML_ERROR = ('File "/tmp/tmpx/candidate.ml", line 4, characters 39-40:\n'
               '4 |         else swap_if_needed (x :: acc) y :: rest\n'
               '                                           ^\n'
               "Error: This expression has type 'a but an expression was "
               "expected of type 'a list")

GCC_ERROR = ("/tmp/tmpx/candidate.cpp:3:14: error: 'foo' was not declared "
             "in this scope")


def test_the_offending_line_is_quoted_back_to_the_writer():
    """The fix loop showed the model an error saying `line 4, characters
    39-40` and never showed it line 4. Observed live on an OCaml bubble sort:
    three attempts, three type errors, no convergence -- the model was being
    asked to fix source it could not see."""
    code = "let rec bubble x = x\nlet other = 1\n"
    tests = 'let () = pc_check (bubble 1 = 1) "b"'
    out = quoted_source(get("ocaml"), code, tests, OCAML_ERROR)
    assert ">>    4 |" in out, out
    assert "the file the toolchain compiled" in out


def test_the_quote_covers_the_assembled_file_not_just_the_implementation():
    """Line numbers are the ASSEMBLED file's -- harness, code, tests, tail --
    so quoting the implementation alone would point at the wrong line."""
    code = "let add a b = a + b\n"
    tests = 'let () = pc_check (add 1 2 = 3) "add"'
    out = quoted_source(get("ocaml"), code, tests,
                        'File "x.ml", line 1, characters 0-3:\nError: nope')
    assert "let pc_checks" in out, "line 1 is the harness, not the code"


def test_an_error_naming_no_line_adds_nothing():
    assert quoted_source(get("ocaml"), "let x = 1", "", "Error: something") == ""


def test_a_line_outside_the_file_is_ignored():
    out = quoted_source(get("ocaml"), "let x = 1", "",
                        'File "x.ml", line 9999, characters 0-1:\nError: nope')
    assert out == ""


def test_a_gcc_style_diagnostic_is_understood_too():
    code = "int add(int a,int b){ return foo(a,b); }"
    out = quoted_source(get("c++"), code, "void pc_tests(){}", GCC_ERROR)
    assert ">>    3 |" in out, out
