"""Reading a benchmark transcript back as an attributed verdict.

Every case here is built from the literal string `execute.py` emits, because
that is the only authority available: the cross-language benchmark has not yet
been run against a live server, so there are no recorded transcripts to fixture
from. The `unknown` bucket exists for exactly that gap -- see the last test.
"""

from purecoder.benchlog import classify


def _transcript(body, ok=None, attempts=None, error=None):
    """A transcript shaped like the real one: preamble, then cli.py's tail."""
    out = [body]
    if ok is not None:
        out.append(f"ok={ok}  attempts={attempts}")
    if error is not None:
        out.append(f"error: {error}")
    return "\n".join(out)


def test_a_passing_run_is_ok_and_carries_its_attempt_count():
    v = classify(_transcript("[attempt 1] tests passed", ok=True, attempts=1))
    assert v.verdict == "ok"
    assert v.attempts == 1
    assert v.reason == ""


def test_the_model_failing_every_attempt_is_the_writer():
    """The loop exhausted its retries and `error` is the toolchain's last
    diagnostic. This is the only bucket that says anything about the model."""
    v = classify(_transcript(
        "[attempt 4] tests failed: Syntax error -> retrying",
        ok=False, attempts=4,
        error="File \"main.ml\", line 3, characters 8-9:\nSyntax error"))
    assert v.verdict == "writer"
    assert v.attempts == 4


def test_the_test_gate_refusing_every_design_is_not_the_writer():
    """attempts=0: the writer was never reached. Counting this against the
    model is the error the whole benchmark exists to avoid."""
    v = classify(_transcript(
        "[tests] gate never satisfied -> giving up",
        ok=False, attempts=0,
        error="test design failed the quality gate: tests never mention any "
              "of ['IsPalindrome'] -- they are not testing the target"))
    assert v.verdict == "gate"
    assert v.attempts == 0
    assert "IsPalindrome" in v.reason


def test_caller_supplied_tests_failing_the_gate_are_also_the_gate():
    v = classify(_transcript("", ok=False, attempts=0,
                             error="the tests you supplied fail the quality "
                                   "gate: no assertions"))
    assert v.verdict == "gate"


def test_a_contract_that_would_not_derive_is_its_own_bucket():
    v = classify(_transcript("", ok=False, attempts=0,
                             error="test-first needs a contract and none "
                                   "could be derived, so there is no name to "
                                   "stub"))
    assert v.verdict == "contract"


def test_a_loop_that_stops_converging_is_stuck_not_a_writer_failure():
    v = classify(_transcript("", ok=False, attempts=3,
                             error="stopped after 3 identical failures: "
                                   "Unbound value pc_tests"))
    assert v.verdict == "stuck"
    assert v.attempts == 3


def test_a_refusal_before_the_model_is_the_harness_not_the_run():
    for error in ("cannot generate go: no test idiom exists",
                  "cannot declare packages for rust: only the Python executor",
                  "cannot validate: the sandbox has no module numpy",
                  "the sandbox cannot import numpy, so the run would fail"):
        v = classify(_transcript("", ok=False, attempts=0, error=error))
        assert v.verdict == "refused", error


def test_a_dead_server_is_infrastructure_and_prints_no_verdict_line():
    """The traceback is all there is -- cli.py never reaches its tail."""
    v = classify(
        'Traceback (most recent call last):\n'
        '  File "purecoder/client.py", line 78, in complete\n'
        "RuntimeError: llama-server request failed: HTTPConnectionPool("
        "host='localhost', port=8080): Max retries exceeded")
    assert v.verdict == "server"


def test_a_transcript_with_no_verdict_and_no_traceback_timed_out():
    """`timeout` killed the process mid-run, so nothing was printed."""
    v = classify("[attempt 2] tests failed: timeout -> retrying")
    assert v.verdict == "timeout"


def test_an_unrecognised_failure_is_unknown_rather_than_blamed_on_the_writer():
    """The load-bearing case. These markers were read out of execute.py, not
    out of a live run -- so a failure this cannot place must stay visible.
    Bucketing it as `writer` by default would manufacture the false capability
    result the benchmark exists to prevent."""
    v = classify(_transcript("", ok=False, attempts=0,
                             error="something nobody has seen yet"))
    assert v.verdict == "unknown"
    assert v.reason == "something nobody has seen yet"


def test_attempts_survives_a_None_the_loop_could_not_fill_in():
    v = classify(_transcript("", ok=False, attempts=None,
                             error="cannot generate swift: no toolchain"))
    assert v.verdict == "refused"
    assert v.attempts is None


def test_the_last_verdict_line_wins_when_a_transcript_holds_several():
    """A retry loop can print more than one. The final one is the run's."""
    v = classify("ok=False  attempts=1\nok=True  attempts=2")
    assert v.verdict == "ok"
    assert v.attempts == 2


def test_a_reason_carries_no_tab_because_the_runner_writes_tsv():
    """`batch.sh` appends one TSV row per task. A tab inside a compiler
    diagnostic would split it into a column nobody declared."""
    v = classify(_transcript("", ok=False, attempts=2,
                             error="main.rs:3\tunexpected\ttoken"))
    assert "\t" not in v.reason
    assert v.reason == "main.rs:3 unexpected token"


def test_a_reason_is_the_first_line_of_a_multi_line_error():
    """Diagnostics run to many lines; a summary table needs one."""
    v = classify(_transcript("", ok=False, attempts=4,
                             error="error: expected `;`\n  --> main.rs:3:1\n"
                                   "   |\n 3 | let x = 1\n"))
    assert v.reason == "error: expected `;`"
