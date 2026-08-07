"""The language registry: what we claim to support, and what we can prove.

These run anywhere -- availability is checked against the real machine only in
the tests marked as such, and every per-language execution test is skipped when
its toolchain is absent so CI stays green on a bare runner.
"""

import shutil

import pytest

from purecoder import languages as L
from purecoder.execute import lint_tests, run_candidate
from purecoder.languages import PYTHON, LanguageSpec
from purecoder.scaffold import scaffold_project

# ---- resolution ----------------------------------------------------------

def test_python_is_registered_and_is_the_default_shape():
    assert L.get("python") is PYTHON
    assert PYTHON.extension == ".py"


def test_aliases_resolve():
    assert L.get("cpp").name == "c++"
    assert L.get("js").name == "javascript"
    assert L.get("py").name == "python"


def test_resolution_is_case_and_space_insensitive():
    assert L.get("  C++  ").name == "c++"


def test_an_unknown_language_names_the_alternatives():
    with pytest.raises(KeyError) as e:
        L.get("cobol")
    assert "python" in str(e.value)


def test_names_lists_every_entry_available_or_not():
    names = L.names()
    assert {"python", "c++", "javascript", "rust", "c#"} <= set(names)
    # unavailable ones must still resolve, so a refusal can explain itself
    assert {"go", "powerquery"} <= set(names)


# ---- every entry is coherent ---------------------------------------------

# Iterates the built-ins, not L.names(): once a learned entry can take a
# placeholder's name, names() depends on what happens to be in the developer's
# store, and the suite would collect a different set on every machine.
@pytest.mark.parametrize("name", sorted(L.BUILTIN_NAMES))
def test_every_spec_is_internally_consistent(name):
    spec = L.get(name)
    assert spec.name == name
    assert spec.extension.startswith(".")
    if spec.unvalidatable:
        return                       # a permanent refusal needs nothing else
    if not spec.run:
        return                       # declared but not implemented yet
    # a wired language must be able to say how its tests assert, and -- unless
    # it is Python, which is counted by AST -- name the helper it counts.
    assert spec.test_system, f"{name} has a runner but no test idiom"
    if name != "python":
        assert spec.check_call, f"{name} must name its check helper"
        # check_call is the INVOCATION form, which is what the gate counts.
        # Rust's is `pc_check!`, while the definition reads `macro_rules!
        # pc_check {`; SQL's is `INSERT INTO pc_checks`, and what the preamble
        # defines is the table at the end of it -- so compare on the last bare
        # name either way.
        helper = spec.check_call.rstrip("!").split()[-1]
        assert helper in spec.preamble, \
            f"{name} names a check helper its preamble never defines"
        # The proof that a check ran must be in the SPEC. For four languages
        # that is the tail; for SQL the tail cannot express it and the runner
        # does, which is legitimate because that runner is ours.
        assert "no checks ran" in spec.epilogue + " ".join(spec.run), \
            f"{name} cannot prove a check ran"


@pytest.mark.parametrize("name", ["c++", "javascript", "rust", "c#", "ocaml"])
def test_a_wired_language_fails_the_run_when_no_check_executes(name):
    """Every non-Python harness must carry the 'no checks ran' tail. That is
    the guarantee that stops exit code 0 being mistaken for evidence."""
    assert "no checks ran" in L.get(name).epilogue


# ---- availability --------------------------------------------------------

def test_csharp_demands_the_shape_its_harness_can_assemble():
    """The C# preamble is a .NET file-based app: top-level statements and local
    functions. A class wrapper or a Main method makes the assembled file fail
    to build, so the demand has to be on the spec AND reach the writer."""
    spec = L.get("c#")
    assert "no class wrapper" in spec.writer_system
    assert "no Main method" in spec.writer_system


def test_a_language_needing_nothing_extra_says_nothing_extra():
    """`writer_system` is for demands the writer prompt does not already make.
    Repeating "output only python code" there is noise the model pays for."""
    for name in ("python", "c++", "javascript", "rust"):
        assert L.get(name).writer_system == "", \
            f"{name} restates what the writer prompt already says"


def test_sql_proves_a_check_ran_from_its_runner_not_its_tail():
    """The one wired language whose proof is not in the epilogue. SQLite's
    RAISE takes a literal, so a failing check cannot name itself from inside
    SQL, and there is no statement that reliably ends a script non-zero. The
    driver is ours -- unlike g++ or node -- so the verdict lives there, and the
    invariant is that the SPEC proves it, not that the tail does."""
    spec = L.get("sql")
    assert "no checks ran" not in spec.epilogue
    assert "no checks ran" in " ".join(spec.run)


def test_a_failing_sql_check_says_which_one():
    """A verdict with no label is a fix loop with nothing to act on -- the
    reason the check table carries one."""
    spec = _skip_unless("sql")
    ok, err = run_candidate(
        spec, "CREATE VIEW added AS SELECT 1 + 2 AS total;",
        "INSERT INTO pc_checks VALUES ((SELECT total FROM added) = 3, 'sum');\n"
        "INSERT INTO pc_checks VALUES ((SELECT total FROM added) = 9, 'wrong');",
        timeout=30, require_checks=1)
    assert not ok
    assert "wrong" in err
    assert "sum" not in err, "a passing check must not be reported as failing"


def test_sql_can_be_generated_but_not_scaffolded():
    """Two claims, and only the first holds. A one-file SQL "project" would
    need a Makefile recipe that reproduces the driver, so `project` refuses
    with a reason rather than writing a layout nothing proves."""
    assert L.get("sql").project is None
    assert L.get("sql").available()[0]


def test_sql_tells_the_writer_whose_table_that_is():
    """The harness creates `pc_checks` and the tests insert into it. An
    implementation that creates or drops it breaks every check in the file."""
    assert "pc_checks" in L.get("sql").writer_system


def test_a_permanently_unvalidatable_language_refuses_with_a_reason():
    ok, why = L.get("powerquery").available()
    assert not ok
    assert "Excel" in why or "Power BI" in why


def test_only_wired_and_refused_names_are_reserved():
    """`learn` may not replace a wired entry or a standing refusal. It MAY take
    a placeholder's name -- reserving those meant the feature refused the exact
    four languages it exists to enable."""
    assert {"python", "c++", "javascript", "rust", "c#"} <= L.RESERVED_NAMES
    assert "powerquery" in L.RESERVED_NAMES, "a standing refusal must stay one"
    # ocaml joined them: it is wired now, so `learn ocaml` is refused the way
    # `learn python` always was.
    assert "ocaml" in L.RESERVED_NAMES
    assert not ({"go", "java", "swift"} & L.RESERVED_NAMES)


def test_a_declared_but_unimplemented_language_refuses():
    """`go` is installed on some machines. Having the binary is not the same as
    being wired, and reporting available would trade a clear refusal for a
    confusing runtime failure. (This was OCaml's test until OCaml was wired.)"""
    ok, why = L.get("go").available()
    assert not ok
    assert "not implemented" in why


def test_a_missing_toolchain_names_the_binary():
    spec = LanguageSpec(name="fake", extension=".fk",
                        probe=("definitely-not-a-real-binary", "--version"),
                        run=("x",), test_system="x", check_call="X",
                        preamble="X", epilogue="X")
    ok, why = spec.available()
    assert not ok
    assert "definitely-not-a-real-binary" in why


def test_python_is_available_without_a_probe():
    ok, why = PYTHON.available()
    assert ok, why


@pytest.mark.parametrize("name,binary", [
    ("c++", "g++"), ("javascript", "node"), ("rust", "rustc"), ("c#", "dotnet"),
    ("ocaml", "ocamlc"),
])
def test_availability_matches_the_machine(name, binary):
    ok, _ = L.get(name).available()
    assert ok is (shutil.which(binary) is not None)


# ---- assembly ------------------------------------------------------------

def test_assemble_orders_harness_code_tests_then_tail():
    spec = LanguageSpec(name="x", extension=".x", preamble="PRE",
                        epilogue="POST", run=("x",), test_system="t")
    out = spec.assemble("CODE", "TESTS")
    assert out.index("PRE") < out.index("CODE") < out.index("TESTS") < out.index("POST")


def test_assemble_skips_empty_sections():
    out = PYTHON.assemble("code = 1", "assert code == 1")
    assert out.strip().splitlines() == ["code = 1", "", "assert code == 1"]


def test_assemble_always_ends_with_a_newline():
    assert PYTHON.assemble("a = 1", "assert a").endswith("\n")


# ---- real execution, skipped when the toolchain is absent ----------------

CASES = {
    "c++": (
        "int add(int a,int b){return a+b;}",
        "int add(int a,int b){return a-b;}",
        "int add(int,int);\nvoid pc_tests(){ PC_CHECK(add(1,2)==3); }",
        "void pc_tests(){ }",
    ),
    "javascript": (
        "function add(a,b){return a+b;}",
        "function add(a,b){return a-b;}",
        "PC_CHECK(add(1,2)===3,'add');",
        "",
    ),
    "rust": (
        "fn add(a:i32,b:i32)->i32{a+b}",
        "fn add(a:i32,b:i32)->i32{a-b}",
        "fn pc_tests(){ pc_check!(add(1,2)==3); }",
        "fn pc_tests(){ }",
    ),
    # OCaml runs top-level statements in order, so a check is a statement and
    # the tests need no wrapper function -- the shape JavaScript and C# use.
    "ocaml": (
        "let add a b = a + b",
        "let add a b = a - b",
        'let () = pc_check (add 1 2 = 3) "add"\n'
        'let () = pc_check (add 0 0 = 0) "zero"\n'
        'let () = pc_check (add (-1) 1 = 0) "negative"',
        "",
    ),
    # SQL has no function to call, so the thing under test is a view. The
    # checks are rows: a boolean and a label, inserted into a table the
    # harness created.
    "sql": (
        "CREATE VIEW added AS SELECT 1 + 2 AS total;",
        "CREATE VIEW added AS SELECT 1 - 2 AS total;",
        "INSERT INTO pc_checks VALUES ((SELECT total FROM added) = 3, 'add');",
        "",
    ),
}


def _skip_unless(name):
    ok, why = L.get(name).available()
    if not ok:
        pytest.skip(why)
    return L.get(name)


@pytest.mark.parametrize("name", list(CASES))
def test_a_correct_implementation_passes(name):
    spec = _skip_unless(name)
    good, _, tests, _ = CASES[name]
    ok, err = run_candidate(spec, good, tests, timeout=60, require_checks=1)
    assert ok, err


@pytest.mark.parametrize("name", list(CASES))
def test_a_wrong_implementation_fails(name):
    """The guarantee that matters. This session found five false greens in the
    Python path that a passing suite never revealed."""
    spec = _skip_unless(name)
    _, bad, tests, _ = CASES[name]
    ok, err = run_candidate(spec, bad, tests, timeout=60, require_checks=1)
    assert not ok
    assert "CHECK FAILED" in err


@pytest.mark.parametrize("name", list(CASES))
def test_a_suite_where_no_check_runs_fails(name):
    """Exit code 0 is not evidence, in any language."""
    spec = _skip_unless(name)
    good, _, _, empty = CASES[name]
    ok, err = run_candidate(spec, good, empty, timeout=60, require_checks=1)
    assert not ok
    assert "no checks ran" in err


def test_a_compile_error_is_reported_not_swallowed():
    """A compile failure is ordinary fix-loop feedback -- exactly what the
    writer needs to see."""
    spec = _skip_unless("c++")
    ok, err = run_candidate(spec, "int add(int a,int b){return a+",
                            "void pc_tests(){ PC_CHECK(1==1); }", timeout=60)
    assert not ok
    assert "error" in err.lower()


def test_a_runaway_is_killed_by_the_timeout():
    spec = _skip_unless("c++")
    ok, err = run_candidate(spec, "void spin(){ while(true){} }",
                            "void spin();\nvoid pc_tests(){ spin(); }", timeout=5)
    assert not ok
    assert "timed out" in err


# ---- the gate, for languages we do not parse ----------------------------

def test_the_textual_gate_counts_helper_calls():
    tests = "void pc_tests(){ PC_CHECK(a==1); PC_CHECK(b==2); PC_CHECK(c==3); }"
    ok, err = lint_tests(tests, min_assertions=3, spec=L.get("c++"))
    assert ok, err


def test_the_textual_gate_rejects_too_few_checks():
    ok, err = lint_tests("void pc_tests(){ PC_CHECK(a==1); }",
                         min_assertions=3, spec=L.get("c++"))
    assert not ok
    assert "PC_CHECK" in err


def test_the_textual_gate_rejects_a_spiral():
    tests = "void pc_tests(){\n" + "  PC_CHECK(a==1);\n" * 20 + "}"
    ok, err = lint_tests(tests, min_assertions=3, spec=L.get("c++"))
    assert not ok
    assert "degenerate" in err


def test_the_textual_gate_wants_the_target_mentioned():
    tests = "void pc_tests(){ PC_CHECK(1==1); PC_CHECK(2==2); PC_CHECK(3==3); }"
    ok, err = lint_tests(tests, targets=["add"], min_assertions=3,
                         spec=L.get("c++"))
    assert not ok
    assert "never mention" in err


def test_python_still_uses_the_ast_gate():
    """Routing must not quietly downgrade Python to the textual path."""
    ok, err = lint_tests("assert add(1, 2 == 3\n", spec=PYTHON)
    assert not ok
    assert "do not parse" in err


# ---- what lands on disk must actually build -----------------------------

def test_a_scaffolded_compiled_project_builds_standalone(tmp_path):
    """The sandbox supplies main(); the file written to disk does not. A
    validated C++ project used to compile clean in the harness and then fail
    `make test` with "undefined reference to `main`". Observed live -- so this
    runs the real compiler on the real artifact."""
    import subprocess
    import sys as _sys

    _sys.path.insert(0, "tests")
    from test_loops import MAKEFILE, FakeModel

    spec = _skip_unless("c++")
    code = "int multiply(int a,int b){return a*b;}"
    tests = ("int multiply(int,int);\nvoid pc_tests(){ "
             "PC_CHECK(multiply(2,3)==6); PC_CHECK(multiply(0,5)==0); "
             "PC_CHECK(multiply(-1,-5)==5); }")
    pc = FakeModel(code_outputs=[code],
                   completions=[tests, MAKEFILE, "K=v\n", "# readme"])
    scaffold_project(pc, "calc", "multiply two ints", outdir=str(tmp_path),
                     spec=spec, use_contract=False, verbose=False)

    src = tmp_path / "main.cpp"
    assert src.exists()
    out = subprocess.run(["g++", "-std=c++17", str(src), "-o",
                          str(tmp_path / "main")], capture_output=True, text=True)
    assert out.returncode == 0, f"the shipped artifact does not build:\n{out.stderr}"


def test_an_interpreted_language_needs_no_entry_stub():
    """Python and JavaScript run a module as-is; only linked languages need
    an entry point added to the file on disk."""
    assert L.get("python").project.entry_stub == ""
    assert L.get("javascript").project.entry_stub == ""
    assert "main" in L.get("c++").project.entry_stub


def test_sql_tells_the_writer_the_database_starts_empty():
    """Live finding. Asked for "a view over a table orders", the writer emitted
    a correct view and no table, the run died on `no such table: main.orders`
    three attempts running, and the loop then blamed the tests. Every other
    language hands the writer an environment that already exists; SQL hands it
    an empty database, and nothing said so."""
    demand = L.get("sql").writer_system
    assert "empty" in demand.lower()
    assert "CREATE TABLE" in demand


def test_the_python_tester_is_told_not_to_wrap_its_asserts():
    """Live finding, and an old one seen again: the designer wrapped every
    assertion in `def test_mean_of():` that nothing calls, so the run exited 0
    with no check executed. The runtime instrumentation caught it and refused
    honestly -- three times, at full generation cost. The four non-Python
    prompts all forbid a wrapper; Python's never did."""
    text = L.PYTHON.test_system
    assert "no test function" in text.lower() or "do not wrap" in text.lower()


def test_the_ocaml_gate_rejects_a_misparenthesised_check():
    """The dominant OCaml tester failure, live: `pc_check ((expr) "label")`
    applies the label to a boolean and does not compile. A counter-example was
    added to the tester prompt and held until documentation was also in the
    context, at which point the model reverted to it -- a prompt asks, a gate
    tells. Caught before the run, with the correction fed back."""
    bad = ('let () = pc_check ((sum_list [] = 0) "empty")\n'
           'let () = pc_check ((sum_list [1] = 1) "one")\n'
           'let () = pc_check ((sum_list [1; 2] = 3) "two")\n')
    ok, err = lint_tests(bad, spec=L.get("ocaml"))
    assert not ok
    assert "outside" in err.lower()


def test_the_ocaml_gate_accepts_the_documented_idiom():
    good = ('let () = pc_check (sum_list [] = 0) "empty"\n'
            'let () = pc_check (sum_list [1] = 1) "one"\n'
            'let () = pc_check (sum_list [1; 2] = 3) "two"\n')
    ok, err = lint_tests(good, spec=L.get("ocaml"))
    assert ok, err


def test_another_language_is_not_held_to_ocamls_rule():
    """C++ writes `PC_CHECK((a + b) == c, "label")` legitimately -- a rule about
    OCaml's application syntax must not reach it."""
    cpp = ('void pc_tests(){\n'
           '  PC_CHECK((add(1, 2)) == 3);\n'
           '  PC_CHECK((add(0, 0)) == 0);\n'
           '  PC_CHECK((add(-1, 1)) == 0);\n}')
    ok, err = lint_tests(cpp, spec=L.get("c++"))
    assert ok, err


def test_the_ocaml_tester_is_told_how_to_check_an_exception():
    """Live: asked for a function that raises Failure on an empty list, the
    tester wrote `pc_check (last_element [] = raise Failure) "empty"` --
    comparing a value against a raise, which does not compile. Python's tester
    prompt has carried the try/except idiom for months; OCaml's said nothing
    about exceptions at all."""
    text = L.get("ocaml").test_system
    assert "try" in text and "with" in text


def test_the_ocaml_gate_rejects_a_comparison_against_a_raise():
    """The prompt half of this has been ignored often enough today to be worth
    a mechanical catch. `= raise` in a check is never valid OCaml."""
    bad = ('let () = pc_check (f [] = raise Failure) "a"\n'
           'let () = pc_check (f [1] = 1) "b"\n'
           'let () = pc_check (f [2] = 2) "c"\n')
    ok, err = lint_tests(bad, spec=L.get("ocaml"))
    assert not ok
    assert "raise" in err
