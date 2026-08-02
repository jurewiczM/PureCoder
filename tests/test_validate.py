"""Config validators: they must reject the degenerate output `make` accepts."""

import shutil

import pytest

from purecoder.validate import (
    MAX_ENV_LINE,
    validate_env,
    validate_makefile,
    validate_python,
)

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


def test_env_rejects_a_rambling_comment():
    """The live failure: one 2000-char comment, truncated mid-sentence.
    Structurally a valid comment, so the shape check passed it."""
    rambling = "# " + ("this is prose not configuration " * 80)
    ok, err = validate_env(rambling + "\n")
    assert not ok
    assert "not prose" in err


def test_env_rejects_an_overlong_value_line():
    ok, err = validate_env("KEY=" + "x" * 300 + "\n")
    assert not ok
    assert "not prose" in err


def test_env_still_accepts_a_normal_commented_example():
    ok, err = validate_env("# Example: PORTS=8080,443\n# PORTS=\n")
    assert ok, err


def test_env_accepts_a_comment_at_the_length_limit():
    ok, err = validate_env("#" + "a" * (MAX_ENV_LINE - 1) + "\n")
    assert ok, err


def test_env_rejects_a_repeated_block():
    """The grammar bounds line LENGTH; the model then satisfied it by looping
    a short block eight times instead. Shape cannot constrain repetition."""
    block = ("# Note: keep secrets out of source control.\n"
             "# Always use environment variables.\n")
    ok, err = validate_env(block * 8)
    assert not ok
    assert "degenerate" in err


def test_env_allows_a_line_repeated_a_few_times():
    ok, err = validate_env("# a note\n# a note\nKEY=1\n")
    assert ok, err
