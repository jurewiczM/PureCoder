"""Flag resolution: explicit flag beats env var beats per-command default."""

import pytest

from purecoder.cli import resolve_contract, resolve_language


class Args:
    def __init__(self, contract=None):
        self.contract = contract


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("PURECODER_CONTRACT", raising=False)


def test_default_is_used_when_nothing_is_set():
    assert resolve_contract(Args(), default=True) is True
    assert resolve_contract(Args(), default=False) is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_env_var_can_turn_it_on(monkeypatch, value):
    monkeypatch.setenv("PURECODER_CONTRACT", value)
    assert resolve_contract(Args(), default=False) is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_env_var_can_turn_it_off(monkeypatch, value):
    monkeypatch.setenv("PURECODER_CONTRACT", value)
    assert resolve_contract(Args(), default=True) is False


def test_explicit_flag_beats_the_env_var(monkeypatch):
    monkeypatch.setenv("PURECODER_CONTRACT", "0")
    assert resolve_contract(Args(contract=True), default=False) is True
    monkeypatch.setenv("PURECODER_CONTRACT", "1")
    assert resolve_contract(Args(contract=False), default=True) is False


def test_explicit_flag_beats_the_default():
    assert resolve_contract(Args(contract=True), default=False) is True
    assert resolve_contract(Args(contract=False), default=True) is False


# ---- language resolution -------------------------------------------------

class LangArgs:
    def __init__(self, lang="python", spec=""):
        self.lang, self.spec, self.contract = lang, spec, None


def test_a_known_available_language_resolves():
    assert resolve_language(LangArgs("python")).name == "python"


def test_an_alias_resolves():
    assert resolve_language(LangArgs("py")).name == "python"


def test_an_unknown_language_is_refused(capsys):
    assert resolve_language(LangArgs("cobol")) is None
    out = capsys.readouterr().out
    assert "unknown language" in out
    assert not out.startswith('"'), "KeyError repr quotes leaked into the message"


def test_a_permanently_unvalidatable_language_is_refused(capsys):
    assert resolve_language(LangArgs("powerquery")) is None
    out = capsys.readouterr().out
    assert "Power BI" in out or "Excel" in out
    assert "Available right now" in out


def test_a_declared_but_unwired_language_is_refused(capsys):
    assert resolve_language(LangArgs("go")) is None
    assert "not implemented" in capsys.readouterr().out


def test_a_spec_contradicting_the_flag_is_refused(capsys):
    """`--lang python` plus "a C++ Dijkstra" is how the pipeline used to
    silently emit `import heapq`."""
    args = LangArgs("python", "a C++ implementation of Dijkstra")
    assert resolve_language(args) is None
    assert "asks for c++" in capsys.readouterr().out


def test_a_spec_naming_its_own_language_is_not_a_contradiction():
    args = LangArgs("c++", "a C++ implementation of Dijkstra")
    assert resolve_language(args).name == "c++"


def test_an_alias_in_the_spec_is_not_a_contradiction():
    """--lang c++ with a spec that says 'cpp' means the same thing."""
    args = LangArgs("cpp", "a cpp quicksort")
    assert resolve_language(args).name == "c++"


# ---- every code-producing command resolves the language ------------------

def test_ask_refuses_a_language_it_cannot_validate(capsys):
    """`ask` is `code` with retrieval in front of it, and it used to parse
    --lang and drop it -- answering a C++ question in Python, which is the one
    failure the registry exists to stop. The refusal must come before the RAG
    import, so this needs neither a store nor sentence-transformers."""
    from purecoder.cli import cmd_ask

    assert cmd_ask(None, LangArgs("powerquery", "anything")) == 1
    assert "Power BI" in capsys.readouterr().out


def test_python_m_purecoder_propagates_the_exit_code():
    """The README calls `python -m purecoder` identical to the console script.
    setuptools wraps the latter in sys.exit(main()); without the same here a
    refusal printed its reason and exited 0."""
    import subprocess
    import sys

    out = subprocess.run([sys.executable, "-m", "purecoder", "--lang", "go",
                          "code", "x"], capture_output=True, text=True)
    assert "not implemented" in out.stdout
    assert out.returncode == 1, "a refusal must not look like success"


def test_every_code_producing_command_resolves_the_language():
    """A guard against the next command that forgets. `env` and `make` are
    exempt: config artifacts have no language."""
    import inspect

    from purecoder import cli

    for name in ("cmd_code", "cmd_ask", "cmd_project"):
        src = inspect.getsource(getattr(cli, name))
        assert "resolve_language(args)" in src, f"{name} ignores --lang"
