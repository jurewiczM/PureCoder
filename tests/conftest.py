"""Shared test doubles and fixtures.

One fake model, not three. Every loop in the pipeline talks to the same small
surface -- `complete`, `code`, and the two config helpers -- so one scripted
double covers all of them, and a test that needs the system prompt reads
`.calls` while one that needs only the task reads `.prompts`.
"""

import pytest


class FakeModel:
    """Returns queued responses in order; records the prompts it was given.

    A queue that runs down to its last entry keeps returning it, so a test that
    wants "the same wrong answer every time" queues one item and a test that
    wants a sequence queues several.
    """

    def __init__(self, code_outputs=None, completions=None):
        self.code_outputs = list(code_outputs or [])
        self.completions = list(completions or [])
        self.prompts = []                # the task text, in order
        self.calls = []                  # (system, user), for prompt-shaping tests
        self.code_kwargs = []            # what the loop asked the writer for

    def _next(self, queue):
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def complete(self, system, user, grammar=None, **kw):
        self.prompts.append(user)
        self.calls.append((system, user))
        return {"text": self._next(self.completions), "truncated": False,
                "tokens": 1, "raw": {}}

    def code(self, description, language="python", **kw):
        self.prompts.append(description)
        self.code_kwargs.append({"language": language, **kw})
        return {"text": self._next(self.code_outputs), "truncated": False,
                "tokens": 1, "raw": {}}

    def env_file(self, description, **kw):
        return self.complete("", description)

    def makefile(self, description, **kw):
        return self.complete("", description)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A private language store, and a registry left exactly as it was found.

    Without the restore, a test that registers a bootstrapped language leaks it
    into `test_languages.py`'s parametrization over `L.names()`, which is
    collected at import -- so ordering would decide whether the suite passed.
    """
    from purecoder.languages import REGISTRY

    monkeypatch.setenv("PURECODER_HOME", str(tmp_path))
    before = dict(REGISTRY)
    yield tmp_path / "languages"
    REGISTRY.clear()
    REGISTRY.update(before)
