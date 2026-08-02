"""The language registry: what we claim to support, and what we can prove.

These run anywhere -- availability is checked against the real machine only in
the tests marked as such, and every per-language execution test is skipped when
its toolchain is absent so CI stays green on a bare runner.
"""

import shutil

import pytest

from purecoder import languages as L
from purecoder.languages import PYTHON, LanguageSpec

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

@pytest.mark.parametrize("name", L.names())
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

def test_a_permanently_unvalidatable_language_refuses_with_a_reason():
    ok, why = L.get("powerquery").available()
    assert not ok
    assert "Excel" in why or "Power BI" in why


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
