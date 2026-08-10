"""
purecoder/benchlog.py

Reading a benchmark transcript back as an attributed verdict.

A run reports `ok=False  attempts=4` whether the model wrote bad code or the
harness refused good code, and those are opposite bugs. Every defect in the
2026-08-07 write-up was found by opening a transcript rather than reading a
score, and an earlier version of the benchmark discarded transcripts and nearly
recorded a batch of correct implementations as a capability result.

This module does not replace reading one. It answers the cheaper question --
*which component said no* -- so a table of forty runs can be scanned for the
rows worth opening.

The markers below were read out of `execute.py` and `cli.py`, not out of a live
run: the cross-language benchmark has never been run against a server. That is
why `unknown` exists and why nothing falls into `writer` by default at
`attempts=0`. A classifier that guessed would manufacture exactly the false
capability result this file exists to prevent.
"""

import re
from dataclasses import dataclass

# cli.py:96 prints `ok={ok}  attempts={attempts}`, and `attempts` can be None
# when the loop returned before it could count one.
_VERDICT = re.compile(r"^ok=(True|False)\s+attempts=(\d+|None)\s*$", re.M)

# A dead server raises out of client.py before cli.py prints anything at all,
# so the traceback is the whole transcript.
_SERVER = "RuntimeError: llama-server request failed"

# The loop's no-progress rule fires when the SAME failure appears on DIFFERENT
# code, and printing this is the loop suspecting the suite.
_SUSPECTED_TESTS = "suspecting the tests, redesigning them"

# Evidence that the tests actually EXECUTED and disagreed, rather than the code
# never building. This is what the marker above cannot tell on its own: the
# no-progress rule also fires when a model repeats one kind of mistake, so
# "same failure, different code" is not proof the suite is wrong.
#
# Measured: blaming the tester on the marker alone moved all seven of the
# 2026-08-09 failures out of `writer`, including three OCaml runs whose code
# genuinely did not compile. That is the original bug with the sign flipped.
_CHECKS_RAN = ("CHECK FAILED", "AssertionError",
               "Traceback (most recent call last)")

# (bucket, marker) in priority order. Matched as substrings of the error text
# because the loop interpolates a reason after most of them.
_BUCKETS = (
    ("gate", "test design failed the quality gate"),
    ("gate", "the tests you supplied fail the quality"),
    ("contract", "test-first needs a contract"),
    ("stuck", "identical failures"),
    ("refused", "cannot generate "),
    ("refused", "cannot run test-first for "),
    ("refused", "cannot declare packages for "),
    ("refused", "cannot validate: "),
    ("refused", "the sandbox cannot import "),
)


@dataclass(frozen=True)
class Verdict:
    """What a transcript says happened.

    `attempts` is None when the run never got far enough to count one, which
    is different from zero and worth keeping distinct in a table.
    """

    verdict: str
    attempts: int | None
    reason: str


def classify(transcript: str) -> Verdict:
    """A benchmark transcript -> (verdict, attempts, reason).

    `ok` the run succeeded. `writer` the loop spent its retries and the last
    word was a toolchain diagnostic -- the only bucket that says anything about
    the model. `suspect-tests` the loop suspected the suite, redesigned it, and
    still failed a check that RAN: an implementation and an expectation
    disagreed and this cannot say which is wrong. `gate`, `contract`, `stuck`,
    `refused` the harness stopped it. `server` and `timeout` are
    infrastructure. `unknown` is a failure whose marker is not in this file,
    kept visible on purpose.

    `suspect-tests` is deliberately not an accusation. Whether a failing check
    means wrong code or a wrong expectation is the boundary the test gate has
    never been able to cross -- it catches structurally bad suites, not
    plausible-but-wrong values -- so the useful thing a classifier can say is
    "open this transcript", not a guess dressed as a verdict.
    """
    matches = list(_VERDICT.finditer(transcript))
    if not matches:
        # No verdict line: either the process died or it was killed. Telling
        # these apart matters, because one is a bug and the other is a budget.
        return Verdict("server" if _SERVER in transcript else "timeout",
                       None, "")

    last = matches[-1]
    attempts = None if last.group(2) == "None" else int(last.group(2))
    if last.group(1) == "True":
        return Verdict("ok", attempts, "")

    error = _error_after(transcript, last.end())
    # One clean line. The runner appends a TSV row per task, and a tab inside a
    # compiler diagnostic would split it into a column nobody declared.
    reason = error.splitlines()[0].replace("\t", " ").strip() if error else ""

    for bucket, marker in _BUCKETS:
        if marker in error:
            return Verdict(bucket, attempts, reason)

    # Before blaming the model: the loop suspected the suite AND a check
    # actually ran. Both halves are needed. The marker alone also fires on a
    # model repeating one mistake, and a build that never produced a binary is
    # the writer's however many times the tests were redesigned.
    if (attempts and _SUSPECTED_TESTS in transcript
            and any(m in reason or m in transcript for m in _CHECKS_RAN)):
        return Verdict("suspect-tests", attempts, reason)

    # Nothing named it. One attempt means the writer was reached and its output
    # was rejected by a real tool, which is the writer's failure however the
    # diagnostic is worded. At zero attempts nothing was ever asked of the
    # model, so guessing here is what the `unknown` bucket refuses to do.
    return Verdict("writer" if attempts else "unknown", attempts, reason)


def _error_after(transcript: str, offset: int) -> str:
    """The text of cli.py's trailing `error:` line, which runs to the end.

    Searched only after the verdict line. Generated code is printed above it
    and a compiler diagnostic inside that code would otherwise be read as the
    run's own error.
    """
    tail = transcript[offset:]
    marker = "\nerror: "
    at = tail.find(marker)
    if at == -1:
        return tail.lstrip().removeprefix("error: ") if tail.strip() else ""
    return tail[at + len(marker):]
