"""The local HTTP surface, exercised against a real socket and a fake pipeline.

No llama-server: the generation functions are monkeypatched, so what is under
test is the transport, the shape of the answers, and the refusals -- not the
model. The server is started on port 0 and torn down per test.
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from purecoder import server as S
from purecoder.client import PureCoder


@pytest.fixture
def live():
    """A running server on an ephemeral port. -> a POST/GET helper."""
    httpd = S.make_server(PureCoder(), host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(path, payload=None, method=None, raw=None):
        data = raw if raw is not None else (
            json.dumps(payload).encode() if payload is not None else None)
        req = urllib.request.Request(base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            body = e.read()
            return e.code, json.loads(body) if body else {}

    yield call
    httpd.shutdown()
    httpd.server_close()


def _fake_generate(**kw):
    return {"ok": True, "text": "def f():\n    return 1\n", "tests": "assert f()",
            "contract": {"name": "f"}, "attempts": 1, "error": ""}


def test_code_returns_the_pipeline_result_as_json(live, monkeypatch):
    monkeypatch.setattr(S, "generate_validated_python",
                        lambda *a, **kw: _fake_generate())
    status, body = live("/code", {"spec": "a function f that returns 1"})
    assert status == 200
    assert body["ok"] is True
    assert body["code"].startswith("def f()")
    assert body["attempts"] == 1


def test_a_refusal_is_an_answer_not_a_server_error(live, monkeypatch):
    """The pipeline refusing is the pipeline working. A 500 would tell a caller
    that PureCoder broke, when in fact it declined and said why -- and there is
    still no tier where code is emitted unvalidated."""
    monkeypatch.setattr(S, "generate_validated_python",
                        lambda *a, **kw: {"ok": False, "text": "", "tests": "",
                                          "contract": None, "attempts": 0,
                                          "error": "test design failed the gate"})
    status, body = live("/code", {"spec": "x"})
    assert status == 200
    assert body["ok"] is False
    assert "gate" in body["error"]


def test_an_unrunnable_language_is_refused_with_its_reason(live):
    """Reusing the CLI's resolver rather than reimplementing it: `go` is
    declared and has no runner, and the API must say so in the same words."""
    status, body = live("/code", {"spec": "x", "lang": "go"})
    assert status == 200
    assert body["ok"] is False
    assert "go" in body["error"]


def test_an_unknown_language_is_refused_rather_than_guessed(live):
    status, body = live("/code", {"spec": "x", "lang": "klingon"})
    assert status == 200
    assert body["ok"] is False
    assert "klingon" in body["error"]


def test_a_missing_spec_is_the_callers_error(live):
    """400, not 200-with-ok-false: the request was malformed, the pipeline was
    never asked anything."""
    status, body = live("/code", {"lang": "python"})
    assert status == 400
    assert "spec" in body["error"]


def test_malformed_json_is_the_callers_error(live):
    status, body = live("/code", raw=b"{not json")
    assert status == 400


def test_an_unknown_path_is_404(live):
    status, _ = live("/nope", {})
    assert status == 404


def test_get_on_a_post_endpoint_is_405(live):
    status, _ = live("/code", method="GET")
    assert status == 405


def test_status_answers_without_a_model_call(live):
    status, body = live("/status", method="GET")
    assert status == 200
    assert "server" in body and "languages" in body


def test_the_default_host_is_loopback_because_this_executes_code():
    """Generated code runs in a subprocess on this machine. Binding anything
    but loopback would make that reachable off-box, so the default is not a
    preference."""
    import inspect

    params = inspect.signature(S.make_server).parameters
    assert params["host"].default == "127.0.0.1"


def test_ask_refuses_without_an_index_and_never_reaches_the_model(live,
                                                                  monkeypatch):
    """`ask` is `code` with retrieval in front of it: an index is not an
    improvement there, it is the command. A missing one must refuse rather than
    quietly answer ungrounded -- and it must refuse BEFORE spending a
    generation, which is what the untouched fake proves."""
    called = []
    monkeypatch.setattr(S, "generate_validated_python",
                        lambda *a, **kw: called.append(1) or _fake_generate())
    status, body = live("/ask", {"spec": "x", "store": "/nonexistent/index"})
    assert status == 200
    assert body["ok"] is False
    assert not called


def test_code_answers_ungrounded_where_ask_would_refuse(live, monkeypatch):
    """The same missing index is not fatal to `code`: documentation is optional
    there, and degrading is the documented behaviour."""
    monkeypatch.setattr(S, "generate_validated_python",
                        lambda *a, **kw: _fake_generate())
    status, body = live("/code", {"spec": "x", "no_docs": True})
    assert status == 200
    assert body["ok"] is True


def test_a_dead_llama_server_is_503_with_the_same_shape(live, monkeypatch):
    """Infrastructure, not the caller's fault and not a refusal -- so 503, not
    400 and not 200. The body keeps every key the success path has: a client
    that has to branch on which fields exist will get it wrong, and the one
    time it matters is the time the server died mid-run."""
    def dead(*a, **kw):
        raise RuntimeError("llama-server request failed: connection refused")

    monkeypatch.setattr(S, "generate_validated_python", dead)
    status, body = live("/code", {"spec": "x"})
    assert status == 503
    assert body["ok"] is False
    assert "llama-server request failed" in body["error"]
    assert set(body) == {"ok", "error", "code", "tests", "contract", "attempts"}


def test_a_refusal_carries_the_same_keys_as_a_success(live, monkeypatch):
    monkeypatch.setattr(S, "generate_validated_python",
                        lambda *a, **kw: _fake_generate())
    _, ok_body = live("/code", {"spec": "x", "no_docs": True})
    _, refused = live("/code", {"spec": "x", "lang": "go"})
    assert set(ok_body) == set(refused)


# ---- the streamed run ----------------------------------------------------

def _sse(base_call, path, payload, timeout=10):
    """Read a server-sent-event stream to the end. -> [record, ...].

    Deliberately not the `live` helper: that one parses a single JSON body,
    and the whole point here is that the answer arrives in pieces.
    """
    import urllib.request

    req = urllib.request.Request(base_call + path,
                                 data=json.dumps(payload).encode(),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    records = []
    with urllib.request.urlopen(req, timeout=timeout) as r:
        assert r.headers["Content-Type"] == "text/event-stream"
        for line in r:
            line = line.decode().strip()
            if line.startswith("data: "):
                records.append(json.loads(line[6:]))
    return records


def test_a_streamed_run_narrates_itself_and_ends_with_the_verdict(monkeypatch):
    """The transcript is the half of a run that `ok=False attempts=4` cannot
    tell you: the same verdict covers a model that wrote bad code and a harness
    that refused good code. Streaming exists so a caller can read which."""
    def fake(pc, description, **kw):
        say = kw["on_event"]
        say({"kind": "tests", "attempt": 1, "text": "[tests] accepted on attempt 1"})
        say({"kind": "attempt", "attempt": 1, "text": "[attempt 1] tests failed"})
        say({"kind": "verdict", "attempt": 2, "text": "[attempt 2] all tests passed"})
        return _fake_generate()

    monkeypatch.setattr(S, "generate_validated_python", fake)
    httpd = S.make_server(PureCoder(), host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        records = _sse(url, "/code/stream", {"spec": "a function f"})
    finally:
        httpd.shutdown()
        httpd.server_close()

    kinds = [r["kind"] for r in records]
    assert kinds == ["tests", "attempt", "verdict", "result"]
    assert records[0]["text"].startswith("[tests] accepted")
    # The last event is exactly what /code would have returned, so a caller
    # that ignores the narration still gets the blocking behaviour.
    assert records[-1]["result"]["ok"] is True
    assert records[-1]["result"]["code"].startswith("def f()")


def test_a_streamed_refusal_arrives_as_a_result_not_a_status(monkeypatch):
    """The response is committed with a 200 before the first line exists, so a
    language refusal cannot become a 4xx. It has to be an event."""
    httpd = S.make_server(PureCoder(), host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        records = _sse(url, "/code/stream", {"spec": "x", "lang": "go"})
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert [r["kind"] for r in records] == ["result"]
    assert records[0]["result"]["ok"] is False
    assert "go" in records[0]["result"]["error"]


def test_a_streamed_run_still_demands_a_spec(live):
    status, body = live("/code/stream", {"spec": "   "})
    assert status == 400
    assert "spec is required" in body["error"]


# ---- the other two additions ---------------------------------------------

def test_status_carries_the_reason_a_language_is_unavailable(live):
    """`false` says a language cannot be used; the reason says what to do about
    it. The registry has always computed that string and the API dropped it."""
    _, body = live("/status", method="GET")
    go = body["languages"]["go"]
    assert go["available"] is False
    assert "not implemented" in go["reason"]
    assert body["languages"]["python"]["available"] is True


def test_grammars_reports_each_root_rule(live):
    """A grammar whose root cannot end always truncates -- shipped twice now.
    The root is the line worth looking at, so it is lifted out of the text."""
    status, body = live("/grammars", method="GET")
    assert status == 200
    names = {g["name"] for g in body["grammars"]}
    assert {"env", "makefile", "contract"} <= names
    env = next(g for g in body["grammars"] if g["name"] == "env")
    assert env["root"].startswith("root")
    assert "line" in env["root"]
    assert env["text"].strip()
