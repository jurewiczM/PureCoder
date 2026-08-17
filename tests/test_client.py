"""Prompt shaping, fence stripping, and grammar loading -- no server needed."""

import re

import pytest
import requests

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
#
# `_FakeResponse` and `_FakeSession` live below with the transport tests.


def _completion(monkeypatch, payload):
    """One completion against a scripted response, with no server involved.

    This used to patch `requests.post` at module level. When the client moved
    onto a session that patch stopped intercepting, and the tests did not all
    fail -- they reached the llama-server actually listening on 8080 and one of
    them passed on its answer. Injecting the session is the seam that cannot
    silently stop working: there is no global left to miss.
    """
    pc = PureCoder()
    pc.session = _FakeSession(_FakeResponse(payload))
    return pc.complete(system="s", user="u")


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


# ---- the transport -------------------------------------------------------
#
# One TCP connection per run instead of one per call, and a bounded retry for
# a blip that is not the server being down. Hermetic: a fake session stands in
# for requests, so none of this needs a llama-server.

class _FakeResponse:
    def __init__(self, payload=None, status=200):
        self._payload = payload if payload is not None else {"content": "ok"}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Server Error")

    def json(self):
        return self._payload


class _FakeSession:
    """Records every post and replays a scripted sequence of outcomes."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def post(self, url, **kw):
        self.calls += 1
        out = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(out, Exception):
            raise out
        return out


def test_every_call_goes_through_one_session():
    """A new connection per call is 60+ handshakes over a benchmark run. The
    session is the thing that makes them one."""
    pc = PureCoder()
    assert pc.session is not None
    assert pc.session is pc.session          # same object, not rebuilt per call


def test_a_transient_connection_error_is_retried(monkeypatch):
    """llama-server restarting mid-batch should cost a retry, not the run."""
    monkeypatch.setattr("purecoder.client.time.sleep", lambda s: None)
    pc = PureCoder()
    pc.session = _FakeSession(requests.ConnectionError("reset"),
                              _FakeResponse({"content": "recovered"}))
    assert pc.complete("s", "u")["text"] == "recovered"
    assert pc.session.calls == 2


def test_a_timeout_is_retried_too(monkeypatch):
    monkeypatch.setattr("purecoder.client.time.sleep", lambda s: None)
    pc = PureCoder()
    pc.session = _FakeSession(requests.Timeout("slow"),
                              _FakeResponse({"content": "recovered"}))
    assert pc.complete("s", "u")["text"] == "recovered"


def test_retries_are_bounded_so_a_dead_server_still_fails_fast(monkeypatch):
    """The pipeline depends on a dead server failing rather than hanging: the
    contract layer falls back on it and the benchmark buckets it."""
    monkeypatch.setattr("purecoder.client.time.sleep", lambda s: None)
    pc = PureCoder(retries=2)
    pc.session = _FakeSession(requests.ConnectionError("refused"))
    with pytest.raises(RuntimeError):
        pc.complete("s", "u")
    assert pc.session.calls == 3             # the first try plus two retries


def test_the_failure_message_is_unchanged_because_things_match_on_it():
    """`benchlog.classify` reads this exact wording to bucket a run as
    `server`, and a fake in test_contract.py raises it verbatim. Rewording it
    breaks failure attribution silently, which is this project's favourite
    bug."""
    pc = PureCoder(retries=0)
    pc.session = _FakeSession(requests.ConnectionError("refused"))
    with pytest.raises(RuntimeError, match="llama-server request failed"):
        pc.complete("s", "u")


def test_an_http_error_is_not_retried(monkeypatch):
    """A 500 from llama-server is a real answer, not a blip. Retrying it hides
    a server-side problem behind three identical failures."""
    monkeypatch.setattr("purecoder.client.time.sleep", lambda s: None)
    pc = PureCoder(retries=2)
    pc.session = _FakeSession(_FakeResponse(status=500))
    with pytest.raises(RuntimeError, match="llama-server request failed"):
        pc.complete("s", "u")
    assert pc.session.calls == 1


def test_status_probes_share_the_client_session(monkeypatch):
    """status.py opened its own connections with bare requests.get, which is
    the same drift the session exists to remove."""
    from purecoder import status

    seen = []

    class _GetSession(_FakeSession):
        def get(self, url, **kw):
            seen.append(url)
            return _FakeResponse({"model_path": "/models/m.gguf"})

    pc = PureCoder()
    pc.session = _GetSession()
    up, model = status._check_server(pc)
    assert up is True
    assert seen and all(u.startswith(pc.base_url) for u in seen)


def test_the_start_command_names_weights_that_are_already_on_disk(tmp_path):
    """`-hf` resolves against the HuggingFace cache and nothing else.

    Measured 2026-08-17: the 30B was sitting in ~/models as a plain file, the
    printed command asked for it by repo id, and llama.cpp began a 14.7 GB
    download that ran for fifty minutes before anyone looked at where it was
    writing. Nothing was broken -- the cache genuinely did not hold it -- which
    is exactly why a status line that names a path costs nothing and a status
    line that names a repo can cost an hour.
    """
    from purecoder import status

    weights = tmp_path / "Qwen3-Coder-30B-A3B-Instruct-Q3_K_M.gguf"
    weights.write_bytes(b"\x00")

    assert status._local_weights([tmp_path]) == weights
    assert status._start_command([tmp_path]).startswith(f"-m {weights}")
    # Nothing on disk: the repo id is still the answer, not an error.
    assert "-hf unsloth/" in status._start_command([tmp_path / "absent"])
