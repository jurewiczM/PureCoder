"""Anchor generation: contract examples -> assertions, no model involved."""

import ast

from purecoder.anchors import anchor_tests

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


def test_anchors_emit_an_equality_assertion():
    src, dropped = anchor_tests(GOOD)
    assert "assert parse_ports('80,443') == [80, 443]" in src
    assert dropped == []


def test_anchors_emit_a_raises_block():
    src, _ = anchor_tests(GOOD)
    assert "try:" in src
    assert "parse_ports('99999')" in src
    assert "assert False" in src
    assert "except ValueError:" in src


def test_anchor_output_always_parses():
    src, _ = anchor_tests(GOOD)
    ast.parse(src)


def test_anchors_drop_a_malformed_example():
    contract = {**GOOD, "examples": [
        {"in": "'80,443'", "out": "[80, 443]"},
        {"in": "'80'", "out": "[80,"},          # unbalanced -- will not parse
    ]}
    src, dropped = anchor_tests(contract)
    assert "assert parse_ports('80,443') == [80, 443]" in src
    assert len(dropped) == 1
    assert "example 2" in dropped[0]
    ast.parse(src)


def test_anchors_drop_a_custom_exception():
    """An anchor is written before the code that would define the class."""
    contract = {**GOOD, "examples": [{"in": "'x'", "out": "raises MyOwnError"}]}
    src, dropped = anchor_tests(contract)
    assert src == ""
    assert len(dropped) == 1
    assert "MyOwnError" in dropped[0]


def test_anchors_accept_any_builtin_exception():
    contract = {**GOOD, "examples": [{"in": "None", "out": "raises TypeError"}]}
    src, dropped = anchor_tests(contract)
    assert "except TypeError:" in src
    assert dropped == []


def test_anchors_return_empty_when_every_example_is_bad():
    contract = {**GOOD, "examples": [
        {"in": "(", "out": "1"},
        {"in": "'x'", "out": "raises NotAnException"},
    ]}
    src, dropped = anchor_tests(contract)
    assert src == ""
    assert len(dropped) == 2


def test_anchors_handle_a_zero_argument_call():
    contract = {**GOOD, "name": "build", "examples": [{"in": "", "out": "42"}]}
    src, dropped = anchor_tests(contract)
    assert "assert build() == 42" in src
    assert dropped == []
