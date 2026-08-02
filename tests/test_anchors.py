"""Anchor generation: contract examples -> assertions, no model involved."""

import ast

from purecoder.anchors import anchor_tests, count_anchors

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
    assert "assert parse_ports('80,443') == ([80, 443])" in src
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
    assert "assert parse_ports('80,443') == ([80, 443])" in src
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


def test_anchors_do_not_swallow_their_own_marker():
    """`raises AssertionError` must not be satisfied by our own assert False."""
    contract = {**GOOD, "name": "check",
                "examples": [{"in": "1", "out": "raises AssertionError"}]}
    src, dropped = anchor_tests(contract)
    assert dropped == []
    ns = {"check": lambda x: None}          # raises nothing -- must FAIL
    try:
        exec(src, ns)
    except AssertionError:
        pass
    else:
        raise AssertionError("anchor passed a function that never raised")


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
    assert "assert build() == (42)" in src
    assert dropped == []


# ---- interpolation: the emitted assertion must mean what it says ---------

def _fails_against(src, ns):
    """True when running `src` against namespace `ns` raises AssertionError."""
    try:
        exec(src, ns)
    except AssertionError:
        return True
    return False


def test_a_tuple_return_is_asserted_whole_not_as_a_message():
    """`out` of "3, 1" must not become `assert f(...) == 3, 1`.

    Python reads that as assert-with-message: it checks only `== 3` and uses
    `1` as the message, so an implementation returning the bare first element
    passes. The anchor is the one part of the suite no model wrote -- it has
    to fail a wrong implementation.
    """
    contract = {**GOOD, "name": "divmod_ish",
                "examples": [{"in": "10, 3", "out": "3, 1"}]}
    src, dropped = anchor_tests(contract)
    assert dropped == []
    assert _fails_against(src, {"divmod_ish": lambda a, b: 3}), src
    assert not _fails_against(src, {"divmod_ish": lambda a, b: (3, 1)}), src


def test_an_injected_in_side_cannot_split_the_assertion():
    """`in` carrying its own punctuation must be dropped, not interpolated."""
    contract = {**GOOD, "name": "f",
                "examples": [{"in": "1) or True; f(2", "out": "99"}]}
    src, dropped = anchor_tests(contract)
    assert src == ""
    assert len(dropped) == 1
    assert "'in'" in dropped[0]


def test_an_injected_out_side_cannot_rebind_the_comparison():
    """`out` of a conditional would bind as `(f(x) == 1) if c else 2`."""
    contract = {**GOOD, "name": "f",
                "examples": [{"in": "1", "out": "1 if c else 2"}]}
    src, dropped = anchor_tests(contract)
    assert dropped == []
    # parenthesised, so `c` is evaluated and the comparison stays intact
    assert _fails_against(src, {"f": lambda x: 2, "c": True}), src


def test_anchors_drop_a_blank_expected_value():
    contract = {**GOOD, "examples": [{"in": "'80'", "out": "   "}]}
    src, dropped = anchor_tests(contract)
    assert src == ""
    assert len(dropped) == 1
    assert "example 1" in dropped[0]


def test_anchors_drop_a_raises_naming_a_non_exception_builtin():
    """`int` exists but is not raisable -- `except int:` is a TypeError."""
    contract = {**GOOD, "examples": [{"in": "'x'", "out": "raises int"}]}
    src, dropped = anchor_tests(contract)
    assert src == ""
    assert len(dropped) == 1
    assert "int is not a built-in exception" in dropped[0]


def test_anchors_validate_the_raises_form_arguments_too():
    contract = {**GOOD, "name": "f",
                "examples": [{"in": "1); import os; f(2",
                              "out": "raises ValueError"}]}
    src, dropped = anchor_tests(contract)
    assert src == ""
    assert "'in'" in dropped[0]


# ---- counting ------------------------------------------------------------

def test_count_anchors_counts_blocks_not_lines():
    """One equality line and one try-block is two anchors, not three: the
    nested `assert False` is indented and must not be counted."""
    src, _ = anchor_tests(GOOD)
    assert count_anchors(src) == 2
    assert count_anchors("") == 0
