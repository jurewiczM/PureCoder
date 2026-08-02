"""Config validators: they must reject the degenerate output `make` accepts."""

import shutil

import pytest

from purecoder.validate import validate_env, validate_makefile, validate_python

# ---- .env ----------------------------------------------------------------

def test_env_accepts_valid_file():
    ok, err = validate_env("# comment\nHOST=localhost\nPORT=5432\nDEBUG=\n")
    assert ok, err


def test_env_rejects_line_without_equals():
    ok, err = validate_env("HOST=localhost\nthis is prose\n")
    assert not ok
    assert "not KEY=VALUE" in err


def test_env_rejects_duplicate_key():
    ok, err = validate_env("HOST=a\nHOST=b\n")
    assert not ok
    assert "duplicate" in err


def test_env_rejects_empty_key():
    ok, err = validate_env("=value\n")
    assert not ok


# ---- python --------------------------------------------------------------

def test_python_accepts_valid_source():
    ok, err = validate_python("def f(x):\n    return x + 1\n")
    assert ok, err


def test_python_rejects_syntax_error():
    ok, err = validate_python("def f(x:\n    return x\n")
    assert not ok
    assert "SyntaxError" in err


# ---- Makefile ------------------------------------------------------------

GOOD_MAKEFILE = (
    ".PHONY: install test clean\n\n"
    "install:\n\tpip install -r requirements.txt\n\n"
    "test:\n\tpytest\n\n"
    "clean:\n\trm -rf __pycache__\n"
)


@pytest.mark.skipif(not shutil.which("make"), reason="make not installed")
def test_makefile_accepts_valid_file():
    ok, err = validate_makefile(GOOD_MAKEFILE)
    assert ok, err


def test_makefile_rejects_degeneration():
    """The failure that motivated the semantic guards: `make -n` passes this."""
    spiral = "clean:\n" + "\trm -f junk.txt\n" * 50
    ok, err = validate_makefile(spiral)
    assert not ok
    assert "degenerate" in err


def test_makefile_rejects_malformed_dot_target():
    ok, err = validate_makefile(".rm: -f foo.txt\n\techo hi\n")
    assert not ok
    assert "dot-target" in err


def test_makefile_allows_real_special_targets():
    ok, err = validate_makefile(".PHONY: all\n\nall:\n\techo hi\n")
    assert ok, err


@pytest.mark.skipif(not shutil.which("make"), reason="make not installed")
def test_makefile_rejects_unparseable():
    ok, err = validate_makefile("target\n\techo orphan recipe\n")
    assert not ok
