"""The executor and the test-quality gate -- both model-independent."""

import re
import time

from purecoder.execute import (
    _DIAGNOSTIC,
    _failure,
    _trim,
    available_packages,
    defined_names,
    defines_target,
    harness_collision,
    lint_implementation,
    lint_tests,
    missing_dependency,
    missing_relation,
    public_names,
    quoted_source,
    red_check,
    repair_tests,
    run_python,
    stub_for,
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


def test_gate_rejects_a_count_the_tester_had_to_derive_by_hand():
    """Live twice, python/count_vowels: thirty characters asserted to hold ten
    vowels when they hold nine -- 10 being what you get by counting the 'y'
    the spec says never to count. Four correct implementations refused, while
    the same spec passed first attempt in the five other languages."""
    tests = ("assert count_vowels('') == 0\n"
             "assert count_vowels('hello') == 2\n"
             "assert count_vowels('AEIOUbcdEfGhIjKlMnOpQrStUvWxYz') == 10\n")
    ok, err = lint_tests(tests, targets=["count_vowels"])
    assert not ok
    assert "30 elements" in err


def test_gate_accepts_the_same_task_over_inputs_a_reader_can_check():
    """The bound must leave the suite the tester should have written."""
    tests = ("assert count_vowels('') == 0\n"
             "assert count_vowels('AeIoU') == 5\n"
             "assert count_vowels('xyz') == 0\n")
    ok, err = lint_tests(tests, targets=["count_vowels"])
    assert ok, err


def test_gate_bounds_only_counts_not_transcriptions():
    """A string or boolean expectation is copied, not counted, however long
    the input -- and an argument that is a call was never scanned by eye.
    Gating those would refuse `run_length_encode` and `is_palindrome`, which
    both pass live."""
    for tests, target in (
            ("assert rle('') == ''\nassert rle('aa') == 'a2'\n"
             "assert rle('aaabbbcccddddeeeee') == 'a3b3c3d4e5'\n", "rle"),
            ("assert is_pal('') == True\nassert is_pal('aba') == True\n"
             "assert is_pal('A man, a plan, a canal: Panama') == False\n",
             "is_pal"),
            ("assert sum_list([]) == 0\nassert sum_list([1]) == 1\n"
             "assert sum_list(list(range(100))) == 4950\n", "sum_list")):
        ok, err = lint_tests(tests, targets=[target])
        assert ok, f"{target}: {err}"


def test_gate_bounds_nothing_before_the_target_is_known():
    """The first design pass runs without a contract, so `targets` is empty
    and mode 6 is inert -- it fires at the post-code regate, which routes to a
    redesign rather than blaming the writer."""
    tests = ("assert count_vowels('AEIOUbcdEfGhIjKlMnOpQrStUvWxYz') == 10\n"
             "assert count_vowels('') == 0\n"
             "assert count_vowels('a') == 1\n")
    ok, err = lint_tests(tests, targets=None)
    assert ok, err


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


# ---- repairing a syntactically impossible check --------------------------

def test_a_misparenthesised_ocaml_check_is_repaired_before_the_gate():
    """The gate rejecting it was not enough: live, the designer wrote the same
    malformation on every attempt and the run ended with attempts=0, having
    never reached the writer at all.

    Repair rather than refusal, on the same argument as `./{bin}` and `{src}.ml`
    before it. `pc_check ((expr) "label")` cannot compile under ANY reading --
    it applies a string to a boolean -- so there is exactly one thing it can
    have meant, and rewriting it is meaning-preserving by construction."""
    bad = 'let () = pc_check ((sum_list [1] = 1) "one")\n'
    assert repair_tests(get("ocaml"), bad) == \
        'let () = pc_check (sum_list [1] = 1) "one"\n'


def test_an_implementation_that_never_names_its_target_is_rejected():
    """Live, asked for `rev_string` with OCaml docs in the prompt, the writer
    returned fragments of the documentation -- `curry4`, a `StringSet` module
    -- and no rev_string at all. The toolchain reported an unbound name, which
    reads like a coding mistake and is not one."""
    code = ("let curry4 f w x y z = f (w, x, y, z)\n"
            "module StringSet = Set.Make(String)\n")
    ok, reason = defines_target(code, "rev_string")
    assert not ok
    assert "rev_string" in reason


def test_an_implementation_naming_its_target_passes():
    ok, reason = defines_target("let rev_string s = s\n", "rev_string")
    assert ok, reason


def test_a_substring_of_another_name_does_not_count():
    """`rev_string_helper` is not `rev_string`; the check is word-anchored."""
    ok, _ = defines_target("let rev_stringify s = s\n", "rev_string")
    assert not ok


def test_a_language_whose_tests_never_name_the_target_is_exempt():
    """SQL has no functions: the implementation is DDL and the checks are rows
    in a table, so the contract's name appears in neither. Demanding it would
    have failed every correct SQL run. The rule is "provide what the tests
    call", which stays true in a language without functions."""
    sql = "CREATE TABLE t (id INTEGER);\nINSERT INTO t VALUES (1);\n"
    checks = "INSERT INTO pc_checks VALUES ((SELECT count(*) FROM t) = 1, 'one');\n"
    ok, _ = defines_target(sql, "add_row", checks)
    assert ok


def test_the_demand_still_holds_when_the_tests_do_call_it():
    ok, _ = defines_target("let curry4 f w x y z = f (w, x, y, z)\n",
                           "rev_string",
                           'let () = pc_check (rev_string "ab" = "ba") "r"\n')
    assert not ok


def test_no_target_means_no_opinion():
    """Without a contract there is no name to require, and the check abstains
    rather than guessing one."""
    ok, _ = defines_target("anything at all", "")
    assert ok


def test_an_ocaml_suite_that_tests_the_stdlib_instead_of_the_target():
    """Measured: asked for insertion_sort, the designer wrote

        let () = pc_check ((List.sort compare ["c";"ab"] = ["ab";"c"])) "sorts"

    -- the standard library's sort, three checks of it, and the implementation
    under test never called. The gate held the rule already; what it never
    received for a non-Python language was a target, because the only source
    of one parsed Python and returned [] for everything else."""
    tests = ('let () = pc_check (List.sort compare [2;1] = [1;2]) "sorts"\n'
             'let () = pc_check (List.sort compare [] = []) "empty"\n'
             'let () = pc_check (List.sort compare [1] = [1]) "single"\n')
    ok, reason = lint_tests(tests, targets=["insertion_sort"],
                            spec=get("ocaml"))
    assert not ok
    assert "insertion_sort" in reason


def test_a_suite_that_mostly_tests_something_else_is_rejected():
    """Measured: a 17-check suite for rev_string, accepted, whose checks were
    mostly about a `StringSet` module that does not exist -- retrieved
    documentation answered instead of used. One conforming check satisfied
    "any", and the rest failed the build on behalf of correct code."""
    tests = ('let () = pc_check (rev_string "ab" = "ba") "reverses"\n'
             'let () = pc_check (StringSet.empty = StringSet.empty) "empty"\n'
             'let () = pc_check (StringSet.cardinal StringSet.empty = 0) "size"\n'
             'let () = pc_check (StringSet.mem "a" StringSet.empty = false) "mem"\n')
    ok, reason = lint_tests(tests, targets=["rev_string"], spec=get("ocaml"),
                            strict_targets=True)
    assert not ok
    assert "1 of 4" in reason


def test_one_incidental_check_does_not_condemn_a_suite():
    """A sanity check that touches nothing under test is ordinary; only a
    minority aimed at the target is refused."""
    tests = ('let () = pc_check (rev_string "ab" = "ba") "reverses"\n'
             'let () = pc_check (rev_string "" = "") "empty"\n'
             'let () = pc_check (String.length "abc" = 3) "sanity"\n')
    ok, reason = lint_tests(tests, targets=["rev_string"], spec=get("ocaml"),
                            strict_targets=True)
    assert ok, reason


def test_a_contract_name_alone_does_not_trigger_the_minority_rule():
    """A name from a contract is a weaker claim than a name read out of the
    code. On the scaffold path -- where `project` derives a contract by
    default -- a C# suite builds `new Counter()` in a setup line and then
    checks `c.Add(1)`, so no check names the class and every one of them is
    testing it. Refusing that regenerates the suite until the gate gives up:
    attempts=0, the failure this kind of rule exists to prevent."""
    tests = ("var c = new Counter();\n"
             "PC_CHECK(c.Add(1) == 1);\n"
             "PC_CHECK(c.Add(2) == 3);\n"
             "PC_CHECK(c.Total() == 3);\n")
    ok, reason = lint_tests(tests, targets=["Counter"], spec=get("c#"))
    assert ok, reason


def test_ocaml_definitions_are_read_from_the_code():
    """Without this the gate had no target at all outside Python or a
    contract, and `code` derives no contract by default."""
    code = ("let rev_string s =\n"
            "  let rec aux acc i = if i < 0 then acc else aux acc (i - 1) in\n"
            "  aux \"\" (String.length s - 1)\n")
    assert defined_names(get("ocaml"), code) == ["rev_string"], \
        "a nested `let rec` is an implementation detail, not the target"


def test_a_language_that_cannot_say_what_it_defines_is_left_permissive():
    assert defined_names(get("c++"), "int add(int a, int b) { return a + b; }") == []


def test_an_ocaml_suite_that_does_test_the_target_passes():
    tests = ('let () = pc_check (insertion_sort [2;1] = [1;2]) "sorts"\n'
             'let () = pc_check (insertion_sort [] = []) "empty"\n'
             'let () = pc_check (insertion_sort [1] = [1]) "single"\n')
    ok, reason = lint_tests(tests, targets=["insertion_sort"],
                            spec=get("ocaml"))
    assert ok, reason


def test_a_doubly_parenthesised_check_is_not_mistaken_for_the_malformation():
    """`pc_check ((expr) = expr) "label"` is correct OCaml, and it is the shape
    a test for a string-returning function naturally takes. The gate used to
    anchor on the opening `pc_check ((` and rejected it, which is why
    `rev_string` was the unstable task in every arm ever measured -- the
    designer was regenerating a suite that had been right the first time."""
    ok = 'let () = pc_check ((rev_string "abc") = "cba") "reverses"\n'
    assert repair_tests(get("ocaml"), ok) == ok
    for pattern, _ in get("ocaml").test_lint:
        assert not re.search(pattern, ok)


def test_the_malformation_is_still_caught_with_nesting_inside():
    """Anchored on the tail, so the label's position decides -- not how many
    parentheses the expression happens to open with."""
    bad = 'let () = pc_check ((rev_string "abc" = "cba") "reverses")\n'
    assert repair_tests(get("ocaml"), bad) == \
        'let () = pc_check (rev_string "abc" = "cba") "reverses"\n'


def test_a_check_with_no_label_is_left_alone_not_mangled():
    """The repair claims to be meaning-preserving. A first version was not: on
    a check written with no label it took the expression as group 1, read the
    final string as the label, and emitted `pc_check rev_string "ab" = "ba"` --
    parentheses gone. A missing label is a real error, but it is the gate's to
    report, not the repair's to invent an answer for."""
    bare = 'let () = pc_check (rev_string "ab" = "ba")\n'
    assert repair_tests(get("ocaml"), bare) == bare


def test_a_capitalised_let_is_repaired_before_the_gate():
    """Measured, not imagined: a 30B model opened its first check with `Let ()`
    -- a line begun the way a sentence is begun -- and OCaml read `Let` as a
    constructor. All five tasks of a batch died on it while the implementations
    it wrote were correct, so the score said 0/5 and meant nothing.

    Meaning-preserving for the same reason as the case above: `Let ()  = ...`
    has no valid reading, since a constructor cannot be applied to unit and
    bound with `=` at the head of a statement."""
    bad = 'Let () = pc_check (sum_list [1] = 1) "one"\n'
    assert repair_tests(get("ocaml"), bad) == \
        'let () = pc_check (sum_list [1] = 1) "one"\n'


def test_only_the_leading_keyword_is_lowered():
    """The anchor is narrow on purpose. A constructor genuinely named `Let`,
    anywhere but at the head of a `Let () =` statement, is left alone."""
    kept = 'let () = pc_check (parse "x" = Let ()) "constructor survives"\n'
    assert repair_tests(get("ocaml"), kept) == kept


def test_a_correct_check_is_left_exactly_alone():
    good = 'let () = pc_check (sum_list [1] = 1) "one"\n'
    assert repair_tests(get("ocaml"), good) == good


def test_a_language_with_no_repair_declared_is_untouched():
    cpp = 'void pc_tests(){ PC_CHECK((add(1, 2)) == 3); }'
    assert repair_tests(get("c++"), cpp) == cpp


def test_a_csharp_style_diagnostic_is_understood():
    """`candidate.cs(12,5): error CS0103` names its line in parentheses, which
    the other two shapes do not cover -- so C# was the one wired language whose
    diagnostics quoted nothing."""
    code = "int Add(int a, int b) => a + b;"
    out = quoted_source(get("c#"), code, "PC_CHECK(true, \"x\");",
                        "candidate.cs(3,5): error CS0103: no such name")
    assert ">>    3 |" in out, out


def test_only_a_few_regions_are_quoted():
    """A compiler can emit a dozen diagnostics, and five lines of context each
    would spend the context budget this project is built around."""
    code = "\n".join(f"let v{i} = {i}" for i in range(60))
    error = "\n".join(f'File "x.ml", line {i}, characters 1-2:' for i in range(1, 13))
    out = quoted_source(get("ocaml"), code, "", error)
    assert out.count(">>") <= 3, out.count(">>")


# ---- TDD: watch the tests fail before trusting them ----------------------

def test_a_stub_is_a_function_that_exists_and_does_nothing():
    """Not an empty file. An empty implementation makes Python raise NameError
    and makes C++ fail to compile -- in both cases the run "fails" without a
    single assertion having executed, which is no evidence at all."""
    assert stub_for(get("python"), "parse_ports") == \
        "def parse_ports(*a, **kw):\n    return None\n"


def test_a_language_that_cannot_be_stubbed_says_so():
    """A stub needs a real signature in C++, Rust or OCaml, and the registry's
    idiom is to refuse with the reason rather than approximate."""
    assert stub_for(get("c++"), "add") == ""
    assert stub_for(get("ocaml"), "add") == ""


def test_tests_that_catch_a_do_nothing_implementation_are_red():
    tests = ("assert add(1, 2) == 3\n"
             "assert add(0, 0) == 0\n"
             "assert add(-1, 1) == 0\n")
    red, reason = red_check(get("python"), tests, "add")
    assert red, reason


def test_tests_that_pass_against_a_stub_are_not_red():
    """The whole point. A suite that a do-nothing implementation satisfies has
    demonstrated nothing about the behaviour that was asked for, and the static
    gate cannot see it."""
    red, reason = red_check(get("python"), "assert True\nassert 1 == 1\n"
                                           "assert add is not None\n", "add")
    assert not red
    assert "does nothing" in reason


def test_a_suite_that_never_reaches_an_assertion_is_not_red_either():
    """Red has to mean "a check ran and failed". A suite that dies before any
    assertion executes proves only that a name is undefined, which is what the
    gate's target check already covers -- and it must not be mistaken for
    evidence."""
    red, reason = red_check(get("python"), "helper_that_does_not_exist()\n",
                            "add")
    assert not red
    assert "no check" in reason.lower()


def test_the_expected_exception_idiom_still_counts_as_red():
    """try/except/else is how this project asks for a raises test, and its
    success path runs no assert at all. Against a stub that returns None the
    `else: assert False` fires, which is a check running and failing."""
    tests = ("try:\n    parse('')\n    assert False\nexcept ValueError:\n"
             "    pass\nassert parse('1') == [1]\nassert parse('1,2') == [1, 2]\n")
    red, reason = red_check(get("python"), tests, "parse")
    assert red, reason


# ---- the diagnostics the fix loop is actually shown ---------------------------
#
# Measured 2026-08-09. `dotnet run` writes `bad.cs(1,1): error CS0106: ...` to
# STDOUT and a content-free "compilation failed" to STDERR. run_candidate
# returned `stderr or stdout`, so the writer was asked to fix an error it was
# never shown, and one live task burned four attempts doing exactly that.

def test_a_diagnostic_on_stdout_is_not_discarded_by_a_summary_on_stderr():
    """The C# case, and the reason this changed at all."""
    out = _failure(1, stdout="bad.cs(1,1): error CS0106: modifier is invalid",
                   stderr="Compilation failed. Fix the build errors.")
    assert "CS0106" in out
    assert "Compilation failed" in out


def test_a_python_traceback_still_leads():
    """stderr first, so the traceback keeps the position it had. Appending
    stdout must not bury it."""
    out = _failure(1, stdout="some printed output",
                   stderr="Traceback (most recent call last):\nAssertionError")
    assert out.index("Traceback") < out.index("some printed output")


def test_stdout_alone_is_still_used():
    out = _failure(1, stdout="only here", stderr="")
    assert out == "only here"


def test_nothing_on_either_stream_names_the_exit_code():
    assert _failure(3, stdout="", stderr="") == "exited 3"


def test_each_stream_is_trimmed_separately_so_neither_evicts_the_other():
    """Merging first and trimming after would let a chatty stdout push a
    traceback out of the window entirely."""
    out = _failure(1, stdout="\n".join(f"line {i}" for i in range(40)),
                   stderr="Traceback (most recent call last):\nValueError: x")
    assert "ValueError" in out
    assert len(out.splitlines()) <= 26


def test_the_msbuild_diagnostic_format_is_recognised():
    """`path(line,col): error CSxxxx:` -- parentheses, not colons, so the
    pattern written for gcc did not match it and _trim fell back to tailing,
    which is the wrong end of a compiler's output."""
    assert _DIAGNOSTIC.search("bad.cs(1,1): error CS0106: modifier is invalid")


def test_the_ocaml_location_line_is_recognised():
    """OCaml puts the location on one line and `Error:` on the next. Only the
    second matched, so a trimmed OCaml failure could name the error without
    saying where it was."""
    assert _DIAGNOSTIC.search('File "candidate.ml", line 14, characters 6-12:')


def test_a_plain_line_is_not_mistaken_for_a_diagnostic():
    for line in ("just some output", "the value was 3", "warnings are off"):
        assert not _DIAGNOSTIC.search(line)


def test_a_python_traceback_frame_is_not_an_ocaml_location():
    """Both spell it `File "x", line N`. OCaml's carries `characters N-M` and
    Python's carries `, in <name>` -- and matching Python's turns a traceback
    into thirty diagnostics, so `_trim` keeps the first frames and drops the
    exception. Caught by the existing trim test, which is why it is here."""
    assert not _DIAGNOSTIC.search('File "x.py", line 3, in f')
    assert _DIAGNOSTIC.search('File "candidate.ml", line 14, characters 6-12:')

