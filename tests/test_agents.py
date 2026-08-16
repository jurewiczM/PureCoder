"""The roster and the ledger: attribution recorded, not inferred.

The claim under test is narrow and worth stating. `benchlog.py` reads finished
transcripts and guesses which component a failure belonged to, and on its first
live run it put four of seven failures in `writer` when the code was correct in
all four. This records the same fact from inside the loop, where which role was
being asked is not a guess.
"""

import pytest

from purecoder.agents import CONTRACT, ROSTER, TESTER, WRITER, Ledger


def test_the_roster_is_only_the_model_backed_roles():
    """The gate is not on this list, and that is the design.

    `lint_tests`, `red_check` and the executor decide whether an agent's output
    is accepted and none of them asks a model anything. Listing the judge as an
    agent would lose the property that makes it worth having -- that the thing
    with the veto cannot be talked round.
    """
    assert [a.name for a in ROSTER] == ["contract", "tester", "writer"]
    for a in ROSTER:
        assert a.role and a.budget > 0


def test_a_clean_run_blames_nobody():
    ledger = Ledger()
    ledger.spend(TESTER, True)
    ledger.spend(WRITER, True)
    assert ledger.blame() == ""
    assert ledger.summary()["stopped_on"] == ""


def test_the_ledger_counts_each_role_separately():
    ledger = Ledger()
    ledger.spend(WRITER, False, "AssertionError")
    ledger.spend(WRITER, False, "AssertionError")
    ledger.spend(WRITER, True)
    assert ledger.attempts(WRITER) == 3
    assert ledger.attempts(TESTER) == 0


def test_a_failed_run_names_where_it_stopped():
    ledger = Ledger()
    ledger.spend(TESTER, True)
    ledger.spend(WRITER, False, "AssertionError: 2 != 3")
    assert ledger.blame() == "writer"
    roles = {r["name"]: r for r in ledger.summary()["roles"]}
    assert roles["writer"]["accepted"] is False
    assert "AssertionError" in roles["writer"]["reason"]
    assert roles["tester"]["reason"] == ""


def test_a_role_that_never_ran_is_absent_rather_than_zero():
    """A contract that was never derived is not a contract agent that scored
    nothing. Reporting it as present with zero attempts would put a row in
    every table for work nobody asked for."""
    ledger = Ledger()
    ledger.spend(WRITER, True)
    assert [r["name"] for r in ledger.summary()["roles"]] == ["writer"]


def test_the_tester_redesign_is_visible_as_a_second_spend():
    """The loop's no-progress rule suspects the TESTS when the same failure
    survives different generated code, and redesigns them. That inference is
    the one this ledger deliberately does not make for the reader -- it records
    the sequence and lets a reader see the tester spending again."""
    ledger = Ledger()
    ledger.spend(TESTER, True)
    for _ in range(3):
        ledger.spend(WRITER, False, "Syntax error")
    ledger.spend(TESTER, True)          # redesigned after the no-progress rule
    assert ledger.attempts(TESTER) == 2
    assert ledger.attempts(WRITER) == 3


@pytest.mark.parametrize("agent", [CONTRACT, TESTER, WRITER])
def test_every_role_declares_what_it_is_for(agent):
    """The role string reaches a UI and a reader, so it has to say something."""
    assert len(agent.role.split()) >= 4
