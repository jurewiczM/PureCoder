"""The contract layer: schema guards, rendering, derivation."""

import pytest

from purecoder.client import GRAMMARS_DIR, PureCoder
from purecoder.contract import validate_contract

GOOD = {
    "name": "parse_ports",
    "summary": "parse comma-separated ports",
    "params": [{"name": "s", "type": "str"}],
    "returns": "sorted list of unique int",
    "raises": [{"exc": "ValueError", "when": "port outside 1-65535"}],
    "examples": [
        {"in": "'80,443'", "out": "[80, 443]"},
        {"in": "'99999'", "out": "raises ValueError"},
    ],
}


def _without(key):
    obj = {k: v for k, v in GOOD.items() if k != key}
    return obj


# ---- grammar -------------------------------------------------------------

def test_contract_grammar_ships_as_package_data():
    assert (GRAMMARS_DIR / "contract.gbnf").is_file()


def test_contract_grammar_loads():
    assert PureCoder()._load_grammar("contract").startswith("root")


# ---- validator -----------------------------------------------------------

def test_validate_accepts_a_good_contract():
    ok, err = validate_contract(GOOD)
    assert ok, err


def test_validate_rejects_non_dict():
    ok, err = validate_contract(["not", "a", "dict"])
    assert not ok
    assert "object" in err


@pytest.mark.parametrize("key", ["name", "summary", "params", "returns",
                                 "raises", "examples"])
def test_validate_rejects_missing_required_key(key):
    ok, err = validate_contract(_without(key))
    assert not ok
    assert key in err


def test_validate_rejects_non_identifier_name():
    ok, err = validate_contract({**GOOD, "name": "parse spaces"})
    assert not ok
    assert "identifier" in err


def test_validate_rejects_python_keyword_as_name():
    ok, err = validate_contract({**GOOD, "name": "class"})
    assert not ok
    assert "keyword" in err


def test_validate_rejects_non_identifier_param_name():
    ok, err = validate_contract({**GOOD, "params": [{"name": "a b", "type": "str"}]})
    assert not ok
    assert "identifier" in err


def test_validate_rejects_non_identifier_exception_name():
    ok, err = validate_contract(
        {**GOOD, "raises": [{"exc": "not a name", "when": "x"}]})
    assert not ok
    assert "identifier" in err


def test_validate_rejects_empty_examples():
    ok, err = validate_contract({**GOOD, "examples": []})
    assert not ok
    assert "example" in err


def test_validate_rejects_example_missing_a_side():
    ok, err = validate_contract({**GOOD, "examples": [{"in": "'80'"}]})
    assert not ok
    assert "out" in err


def test_validate_accepts_empty_params_and_raises():
    """A zero-argument function that raises nothing is a legitimate contract."""
    ok, err = validate_contract({**GOOD, "params": [], "raises": []})
    assert ok, err
