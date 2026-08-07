"""Prompt shaping, fence stripping, and grammar loading -- no server needed."""

import re

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


# ---- the writer prompt ---------------------------------------------------

class _Recorder(PureCoder):
    """Capture the system prompt instead of calling a server."""

    def complete(self, system, user, **kw):
        self.system = system
        return {"text": "code", "truncated": False, "tokens": 1, "raw": {}}


def test_the_writer_prompt_names_the_language():
    pc = _Recorder()
    pc.code("do a thing", language="rust")
    assert "only rust code" in pc.system


def test_a_language_extra_demand_reaches_the_writer():
    """C# is why this exists: its harness is a .NET file-based app, so a class
    wrapper or a Main method makes the assembled file fail to build. The demand
    was declared on the spec and read by nothing, so it never reached a prompt."""
    from purecoder.languages import get

    pc = _Recorder()
    pc.code("do a thing", language="c#", writer_system=get("c#").writer_system)
    assert "no class wrapper" in pc.system
    assert "no Main method" in pc.system


def test_a_language_with_no_extra_demand_adds_nothing():
    plain, extra = _Recorder(), _Recorder()
    plain.code("x", language="python")
    extra.code("x", language="python", writer_system="")
    assert plain.system == extra.system


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


def test_no_grammar_rule_spans_multiple_lines():
    """llama.cpp's GBNF parser rejects a rule split across lines.

    It fails at sampler-init with a 400, so a multi-line rule looks fine to
    every string-level check here and only dies against a live server. This
    catches the whole class without needing one -- contract.gbnf shipped
    broken exactly this way.
    """
    for path in sorted(GRAMMARS_DIR.glob("*.gbnf")):
        for i, raw in enumerate(path.read_text().splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            assert "::=" in line, (
                f"{path.name}:{i}: continuation of the previous rule -- "
                f"llama.cpp will reject the whole grammar"
            )


def test_env_grammar_bounds_line_length():
    """A rambling comment must be structurally impossible, not merely rejected.

    Left unbounded the model wrote 2500-character comments that the shape
    check passed and the semantic guard then had to reject, costing a model
    call per retry. The grammar's bound is stricter than the validator's, so
    generated output cannot reach the validator's limit.
    """
    from purecoder.validate import MAX_ENV_LINE

    text = (GRAMMARS_DIR / "env.gbnf").read_text()
    bounds = re.findall(r"\{0,(\d+)\}", text)
    assert bounds, "env.gbnf no longer bounds line length"
    assert all(int(b) < MAX_ENV_LINE for b in bounds), \
        "the grammar must be stricter than the validator it feeds"


# ---- truncation, which stopped being reported ----------------------------

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _completion(monkeypatch, payload):
    import purecoder.client as C

    monkeypatch.setattr(C.requests, "post",
                        lambda *a, **kw: _FakeResponse(payload))
    return C.PureCoder().complete(system="s", user="u")


def test_a_generation_cut_off_at_n_predict_is_reported(monkeypatch):
    """Found live. llama.cpp now reports `stop_type: "limit"`; the boolean the
    client read -- `stopped_limit` -- is simply absent, so `.get(..., False)`
    said "complete" for every cut-off generation. Every truncation retry in the
    project was dead: a .env cut mid-comment validated clean and was returned
    as ok."""
    out = _completion(monkeypatch, {"content": "KEY=val", "stop_type": "limit"})
    assert out["truncated"] is True


def test_the_older_field_still_works(monkeypatch):
    """A server predating `stop_type` must not start reporting every
    generation as truncated, or the loops would retry forever."""
    assert _completion(monkeypatch, {"content": "x",
                                     "stopped_limit": True})["truncated"] is True
    assert _completion(monkeypatch, {"content": "x",
                                     "stopped_limit": False})["truncated"] is False


def test_a_natural_stop_is_not_truncation(monkeypatch):
    for payload in ({"content": "x", "stop_type": "eos"},
                    {"content": "x", "stop_type": "word"},
                    {"content": "x"}):
        assert _completion(monkeypatch, payload)["truncated"] is False


def test_a_truncated_prompt_is_not_a_truncated_answer(monkeypatch):
    """llama.cpp's own `truncated` flag means the PROMPT was cut to fit the
    context -- a different failure, and reading it as ours would make every
    long-context call retry."""
    out = _completion(monkeypatch, {"content": "x", "truncated": True,
                                    "stop_type": "eos"})
    assert out["truncated"] is False
