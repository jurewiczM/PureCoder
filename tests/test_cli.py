"""Flag resolution: explicit flag beats env var beats per-command default."""

import pytest

from purecoder.cli import resolve_contract


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
