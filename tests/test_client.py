"""Prompt shaping, fence stripping, and grammar loading -- no server needed."""

import pytest

from purecoder.client import GRAMMARS_DIR, PureCoder, strip_fences


def test_strip_fences_removes_language_fence():
    assert strip_fences("```python\ndef f():\n    pass\n```") == "def f():\n    pass"


def test_strip_fences_removes_bare_fence():
    assert strip_fences("```\nhello\n```") == "hello"


def test_strip_fences_leaves_unfenced_text_alone():
    assert strip_fences("def f():\n    pass") == "def f():\n    pass"


def test_strip_fences_handles_missing_closing_fence():
    assert strip_fences("```python\ndef f(): pass") == "def f(): pass"


def test_chatml_uses_qwen_special_tokens():
    prompt = PureCoder._chatml("SYS", "USER")
    assert prompt.startswith("<|im_start|>system\nSYS<|im_end|>")
    assert "<|im_start|>user\nUSER<|im_end|>" in prompt
    assert prompt.endswith("<|im_start|>assistant\n")


def test_grammars_resolve_regardless_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert PureCoder()._load_grammar("env").startswith("root")


def test_grammar_is_cached_after_first_read():
    pc = PureCoder()
    pc._load_grammar("makefile")
    assert "makefile" in pc._grammar_cache


def test_unknown_grammar_raises():
    with pytest.raises(FileNotFoundError):
        PureCoder()._load_grammar("nope")


def test_shipped_grammars_are_present():
    names = {p.stem for p in GRAMMARS_DIR.glob("*.gbnf")}
    assert {"env", "makefile"} <= names


def test_base_url_trailing_slash_is_normalised():
    assert PureCoder(base_url="http://x:8080/").base_url == "http://x:8080"
