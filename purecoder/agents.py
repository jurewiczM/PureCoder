"""
purecoder/agents.py

The roles this pipeline runs, named -- and a ledger of what each one spent.

Three roles are backed by a model, and each has always had its own system
prompt, its own retry budget and its own acceptance check. They were just
never named, so nothing downstream could say which of them a failed run was
about. This module gives them names and records their spend as the run
happens.

**The gate is not an agent, and that distinction is the point.** `lint_tests`,
`red_check` and the executor decide whether an agent's output is accepted, and
none of them asks a model anything. Agents propose; tools dispose. Calling the
judge an agent too would flatter the architecture and lose the only property
that makes it trustworthy -- that the thing with the veto cannot be talked
round.

**What this buys is attribution.** A run reports `ok=False attempts=4` whether
the model wrote bad code or the harness refused good code, and until now the
only way to tell them apart was to read the transcript. `benchlog.py` exists to
guess it afterwards from that text, and on its first live run it put four of
seven failures in `writer` when the code was correct in all four. A guess made
from the outside was never going to be reliable. A ledger written from the
inside, by the loop that actually spent the attempts, is not a guess.

What this deliberately does NOT do is give any role autonomy. Nothing here
decides what to do next; the control flow stays where it was, in Python, where
it can be read and tested. This project's governing rule is that a mechanical
constraint beats a prompt, and an agent that chose its own next step would be
that rule run backwards.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Agent:
    """A named role backed by a model call.

    `budget` is how many attempts the role may spend in one run. It is declared
    here and currently NOT enforced separately -- every role still runs on the
    caller's `max_retries`, exactly as before. The field exists because the
    budgets are genuinely per-role and someone will want to tune them; changing
    the numbers changes measured behaviour, and this project re-runs its
    controls when it does that. Declaring the seam and leaving it unused is
    honest; quietly changing three retry counts inside a refactor is not.
    """

    name: str
    role: str
    budget: int


CONTRACT = Agent(
    "contract", "turns prose into a contract both other roles read", 3)
TESTER = Agent(
    "tester", "writes the suite from the spec, never seeing the code", 3)
WRITER = Agent(
    "writer", "writes the implementation, and only that", 4)

#: Order matters here: it is the order a run consults them, and the order the
#: UI lists them in.
ROSTER = (CONTRACT, TESTER, WRITER)
BY_NAME = {a.name: a for a in ROSTER}


@dataclass
class Entry:
    attempts: int = 0
    accepted: bool = False
    reason: str = ""


@dataclass
class Ledger:
    """Who spent what during one run, and who it stopped on.

    Written as the run happens rather than inferred from its output. The
    difference matters: the loop knows which role it was asking when an attempt
    was spent, and no amount of reading the transcript afterwards recovers that
    with certainty.
    """

    entries: dict = field(default_factory=dict)
    #: The last role to fail, which is not always the role at fault -- see
    #: `blame`.
    _last_failed: str = ""

    def spend(self, agent: Agent, ok: bool, reason: str = "") -> None:
        entry = self.entries.setdefault(agent.name, Entry())
        entry.attempts += 1
        entry.accepted = ok
        entry.reason = "" if ok else reason
        if not ok:
            self._last_failed = agent.name

    def attempts(self, agent: Agent) -> int:
        return self.entries.get(agent.name, Entry()).attempts

    def blame(self) -> str:
        """The role a failed run ended on. "" when nothing failed.

        Read this as *where the run stopped*, not as *whose fault it was*.
        Those differ, and the difference is this project's oldest measurement
        problem: when the tester writes a suite that cannot pass, the writer is
        the role that spends the attempts and the writer is not at fault. The
        loop's own no-progress rule already encodes that suspicion -- an
        identical failure across DIFFERENT generated code implicates the tests
        -- and when it fires it redesigns the suite, which shows up here as the
        tester spending again. So the ledger records the sequence honestly and
        leaves the inference to a reader who can see it.
        """
        return self._last_failed

    def summary(self) -> dict:
        """The ledger as plain data, for the verdict, the API and the UI."""
        return {
            "stopped_on": self._last_failed,
            "roles": [
                {
                    "name": a.name,
                    "role": a.role,
                    "budget": a.budget,
                    "attempts": self.entries.get(a.name, Entry()).attempts,
                    "accepted": self.entries.get(a.name, Entry()).accepted,
                    "reason": self.entries.get(a.name, Entry()).reason,
                }
                for a in ROSTER
                if a.name in self.entries
            ],
        }
