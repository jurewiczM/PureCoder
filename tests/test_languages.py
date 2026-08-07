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
        # pc_check {` -- so compare on the bare name.
        assert spec.check_call.rstrip("!") in spec.preamble, \
            f"{name} names a check helper its preamble never defines"
        assert spec.epilogue, f"{name} cannot prove a check ran"


@pytest.mark.parametrize("name", ["c++", "javascript", "rust", "c#"])
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
    assert not ({"go", "java", "swift", "ocaml"} & L.RESERVED_NAMES)


def test_a_declared_but_unimplemented_language_refuses():
    """`ocaml` is installed on some machines. Having the binary is not the
    same as being wired, and reporting available would trade a clear refusal
    for a confusing runtime failure."""
    ok, why = L.get("ocaml").available()
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
