"""The contract layer: schema guards, rendering, derivation."""

import json

import pytest

from purecoder.client import GRAMMARS_DIR, PureCoder
from purecoder.contract import (
    derive_contract,
    render_contract,
    validate_contract,
)

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


# ---- rendering -----------------------------------------------------------


def test_render_contains_the_fields_a_reader_needs():
    text = render_contract(GOOD)
    assert "parse_ports" in text
    assert "sorted list of unique int" in text
    assert "ValueError" in text
    assert "port outside 1-65535" in text
    assert "'80,443'" in text


def test_render_handles_empty_params_and_raises():
    text = render_contract({**GOOD, "params": [], "raises": []})
    assert "parse_ports" in text
    assert isinstance(text, str) and text.strip()


# ---- derivation ----------------------------------------------------------


class FakeContractModel:
    """Returns queued completions in order; records the prompts it saw."""

    def __init__(self, completions):
        self.completions = list(completions)
        self.prompts = []

    def complete(self, system, user, grammar=None, **kw):
        self.prompts.append(user)
        text = (self.completions.pop(0) if len(self.completions) > 1
                else self.completions[0])
        return {"text": text, "truncated": False, "tokens": 1, "raw": {}}


def test_derive_returns_a_validated_contract():
    pc = FakeContractModel([json.dumps(GOOD)])
    contract, err = derive_contract(pc, "parse ports", verbose=False)
    assert err == ""
    assert contract["name"] == "parse_ports"


def test_derive_requests_the_contract_grammar():
    pc = FakeContractModel([json.dumps(GOOD)])

    seen = {}
    original = pc.complete

    def spy(system, user, grammar=None, **kw):
        seen["grammar"] = grammar
        return original(system, user, grammar=grammar, **kw)

    pc.complete = spy
    derive_contract(pc, "parse ports", verbose=False)
    assert seen["grammar"] == "contract"


def test_derive_retries_on_invalid_json_and_feeds_the_error_back():
    pc = FakeContractModel(["{not json", json.dumps(GOOD)])
    contract, err = derive_contract(pc, "parse ports", verbose=False)
    assert contract is not None and err == ""
    assert "JSON" in pc.prompts[1]


def test_derive_retries_when_the_validator_rejects():
    bad = json.dumps({**GOOD, "name": "not an identifier"})
    pc = FakeContractModel([bad, json.dumps(GOOD)])
    contract, err = derive_contract(pc, "parse ports", verbose=False)
    assert contract is not None and err == ""
    assert "identifier" in pc.prompts[1]


def test_derive_gives_up_and_reports_the_error():
    pc = FakeContractModel(["{not json"])
    contract, err = derive_contract(pc, "parse ports", max_retries=2,
                                    verbose=False)
    assert contract is None
    assert err


def test_derive_survives_a_dead_server():
    class DeadModel:
        def complete(self, *a, **kw):
            raise RuntimeError("llama-server request failed: connection refused")

    contract, err = derive_contract(DeadModel(), "parse ports", verbose=False)
    assert contract is None
    assert "connection refused" in err


def test_contract_helpers_are_exported_from_the_package():
    import purecoder

    for name in ("derive_contract", "anchor_tests", "render_contract",
                 "validate_contract"):
        assert name in purecoder.__all__
        assert hasattr(purecoder, name)


# ---- example quality ------------------------------------------------------

def test_validate_rejects_duplicate_examples():
    """Duplicates yield duplicate anchors and signal the model ran dry."""
    dup = {"in": "'80'", "out": "[80]"}
    ok, err = validate_contract({**GOOD, "examples": [dup, dict(dup)]})
    assert not ok
    assert "repeats an earlier example" in err


def test_validate_ignores_whitespace_when_comparing_examples():
    ok, err = validate_contract({**GOOD, "examples": [
        {"in": "'80'", "out": "[80]"},
        {"in": " '80' ", "out": " [80] "},
    ]})
    assert not ok
    assert "repeats" in err


def test_validate_rejects_a_contract_where_every_example_raises():
    """Observed live: a 'display a graph' spec produced only `raises` examples,
    so the anchors demanded that correct code throw."""
    ok, err = validate_contract({**GOOD, "examples": [
        {"in": "'abc'", "out": "raises ValueError"},
        {"in": "'99999'", "out": "raises TypeError"},
    ]})
    assert not ok
    assert "every example raises" in err


def test_validate_accepts_a_mix_of_success_and_raises():
    ok, err = validate_contract({**GOOD, "examples": [
        {"in": "'80,443'", "out": "[80, 443]"},
        {"in": "'99999'", "out": "raises ValueError"},
    ]})
    assert ok, err


def test_a_rejected_contract_is_retried_with_the_reason_fed_back():
    all_raise = {**GOOD, "examples": [
        {"in": "'a'", "out": "raises ValueError"},
        {"in": "'b'", "out": "raises TypeError"},
    ]}
    pc = FakeContractModel([json.dumps(all_raise), json.dumps(GOOD)])
    contract, err = derive_contract(pc, "parse ports", verbose=False)
    assert contract is not None and err == ""
    assert "every example raises" in pc.prompts[1]


def test_validate_rejects_a_returns_field_describing_an_exception():
    """Observed live: `returns` read 'raises ValueError if unable to
    generate', leaving the contract silent about the success value."""
    ok, err = validate_contract(
        {**GOOD, "returns": "raises ValueError if the input is bad"})
    assert not ok
    assert "returns describes an exception" in err


def test_validate_still_accepts_a_returns_field_merely_mentioning_errors():
    ok, err = validate_contract(
        {**GOOD, "returns": "a sorted list, or an empty list on no input"})
    assert ok, err
