"""The symbol library: what the docs name, and what that can honestly decide.

The extraction is heuristic. Every test here exists because the heuristic got
something wrong first -- filenames read as modules, prose read as an API
listing, an error message read as gospel.
"""

from purecoder.symbols import (
    did_you_mean,
    extract_symbols,
    modules,
    qualified_names,
)


def test_a_dotted_name_is_a_symbol():
    assert qualified_names("call Printf.eprintf here") == {"Printf.eprintf"}


def test_a_deeper_chain_is_kept_whole():
    assert "os.path.join" in qualified_names("use os.path.join(a, b)")


def test_a_filename_is_not_a_symbol():
    """`README.md` is the same shape as `List.map`, and documentation is full
    of filenames. Admitted as a symbol, each one invents a module."""
    found = qualified_names("see README.md and docs/STATUS.md for details")
    assert found == set()


def test_a_domain_is_not_a_symbol():
    assert qualified_names("published at arxiv.org today") == set()


def test_a_single_letter_prefix_is_not_a_module():
    """`a.b` in a doc's example is two locals, not an API."""
    assert qualified_names("let a.b = 1") == set()


def test_extraction_gathers_every_mention():
    chunks = ["Printf.eprintf writes to stderr",
              "See also Printf.sprintf and List.map"]
    assert extract_symbols(chunks) == {"Printf.eprintf", "Printf.sprintf",
                                       "List.map"}


def test_modules_summarises_what_was_found():
    names = frozenset({"Printf.eprintf", "Printf.sprintf", "List.map"})
    assert modules(names) == [("Printf", 2), ("List", 1)]


# ---- what the library may and may not decide -----------------------------

DOCS = frozenset({"List.fold_left", "List.fold_right", "List.map",
                  "Printf.eprintf"})


def test_an_ordinary_failure_gets_no_commentary():
    """The library is consulted on every failed attempt, so anything it says
    about a failure that is not about a name is pure noise in the prompt."""
    assert did_you_mean("AssertionError: expected 4, got 5", DOCS) == ""
    assert did_you_mean("TypeError: unsupported operand type(s)", DOCS) == ""


def test_a_name_the_toolchain_rejected_gets_the_real_one():
    hint = did_you_mean("Error: Unbound value List.fold", DOCS)
    assert "List.fold_left" in hint


def test_a_name_split_across_two_quotes_is_reassembled():
    """Python reports `module 're' has no attribute 'escap'` -- one name, two
    quoted fragments. Joining adjacent pairs handles it without the library
    knowing whose error message it is reading."""
    docs = frozenset({"re.escape", "re.compile"})
    assert "re.escape" in did_you_mean(
        "AttributeError: module 're' has no attribute 'escap'", docs)


def test_nothing_close_enough_means_silence():
    assert did_you_mean("Error: Unbound value Hashtbl.replace", DOCS) == ""


def test_a_name_the_docs_do_have_is_not_second_guessed():
    assert did_you_mean("Error: Unbound value List.map", DOCS) == ""


def test_an_empty_library_says_nothing():
    assert did_you_mean("Error: Unbound value List.fold", frozenset()) == ""


def test_the_library_never_rules_on_code_the_toolchain_accepted():
    """The measurement that shaped this module: judging code directly against
    the docs produced 45 findings on this project's own source, all wrong --
    `re.escape` and `ast.walk` are real, the docs just had no reason to mention
    them. Documentation does not enumerate a module, so only a failure can
    start this conversation. There is deliberately no function that takes code.
    """
    import purecoder.symbols as symbols

    takes_code = [name for name in dir(symbols)
                  if name in ("unknown_members", "check_code", "lint_symbols")]
    assert takes_code == [], \
        "a symbol check that judges code needs a completeness the docs lack"
