"""
purecoder/server.py

The pipeline over HTTP, for editors, scripts and other agents.

Nothing here relaxes anything. The same gates run, the same refusals come back,
and there is still no tier in which code is emitted unvalidated -- a refusal is
returned as a 200 with `ok: false` and the reason, because the pipeline
declining IS the pipeline working. A 500 would tell a caller that PureCoder
broke when in fact it did its job.

Two decisions worth stating.

**Loopback only, and that is not a preference.** `/code` runs model-authored
code in a subprocess on this machine. Binding anything else would make that
reachable off-box, so the default host is `127.0.0.1` and a test asserts it.
There is no auth because there is no remote.

**`/code` blocks.** A generation is minutes, and a job queue would add state,
polling and a way for a caller to lose a result. Blocking is honest for a local
tool: the caller sets a long timeout and gets an answer or an error. If an
editor ever needs it non-blocking, that is a wrapper around this, not a rewrite
of it.

Built on `http.server` because the project's only runtime dependencies are
`requests` and `numpy`, and a local control surface does not earn a framework.
"""

import json
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import languages
from .cli import ground_in_docs, language_for
from .client import GRAMMARS_DIR
from .execute import generate_validated_python
from .status import collect


def _answer(ok=False, error="", code="", tests="", contract=None, attempts=0):
    """One envelope for every `/code` and `/ask` outcome.

    Success, refusal and a dead llama-server all carry the same keys. A caller
    that has to branch on which fields exist will get it wrong, and the one
    time that matters is the time the server died in the middle of a run.
    """
    return {"ok": ok, "error": error, "code": code, "tests": tests,
            "contract": contract, "attempts": attempts}


def _code(pc, body, required_docs=False):
    """POST /code and /ask share everything but whether docs are mandatory."""
    if not str(body.get("spec", "")).strip():
        return 400, {"error": "spec is required"}

    spec, why = language_for(body.get("lang", "python"), body.get("spec", ""))
    if spec is None:
        return 200, _answer(error=why)

    # Straight to the shared resolver: which index, the did-you-mean hint and
    # degrading on a store that cannot be read are decided in one place, and
    # the API inherits all of it rather than keeping a second copy.
    context, hint, docs_for_error = ground_in_docs(
        spec, body["spec"], store=body.get("store"),
        device=body.get("device", "cuda"),
        no_docs=bool(body.get("no_docs", False)), required=required_docs)
    if context is None:
        return 200, _answer(error="no documentation index could be read")

    res = generate_validated_python(
        pc, body["spec"], context=context, spec=spec,
        max_retries=int(body.get("retries", 4)),
        use_contract=bool(body.get("contract", False)),
        error_hint=hint, error_docs=docs_for_error,
        packages=tuple(body.get("with") or ()),
        verbose=False)
    return 200, _answer(ok=bool(res["ok"]), code=res["text"],
                        tests=res.get("tests", ""),
                        contract=res.get("contract"),
                        attempts=res.get("attempts"),
                        error=res.get("error", ""))


def _status(pc, _body=None):
    info = collect(pc)
    # The reason, not just the boolean. "false" tells a caller a language is
    # unavailable; `'sqlite3' is not installed` tells them what to do about it,
    # and `go is declared but not implemented yet` tells them not to bother.
    # The registry has been computing that string all along and the API threw
    # it away.
    info["languages"] = {}
    for name in languages.names():
        available, reason = languages.get(name).available()
        info["languages"][name] = {"available": available, "reason": reason}
    return 200, info


def _grammars(_pc, _body=None):
    """The GBNF files this build would constrain generation with.

    Small, and worth having: a grammar whose root cannot end is a grammar that
    always truncates, and that defect has now shipped twice -- once in
    `env.gbnf` and once in `makefile.gbnf`, six days apart. The root rule is
    the line to look at, so it is lifted out rather than left for the reader to
    find in the text.
    """
    out = []
    for path in sorted(GRAMMARS_DIR.glob("*.gbnf")):
        text = path.read_text()
        root = next((ln for ln in text.splitlines()
                     if ln.startswith("root")), "")
        out.append({"name": path.stem, "root": root.strip(), "text": text})
    return 200, {"grammars": out}


POST_ROUTES = {
    "/code": _code,
    "/ask": partial(_code, required_docs=True),
}
GET_ROUTES = {"/status": _status, "/grammars": _grammars}

# Streamed variants live apart from POST_ROUTES: they own the response rather
# than returning a payload for the handler to serialise.
STREAM_ROUTES = {"/code/stream"}


class _Handler(BaseHTTPRequestHandler):
    server_version = "PureCoder"

    def _stream_code(self, body):
        """POST /code/stream -- the same run as /code, narrated as it happens.

        A generation is a minute of silence followed by a verdict, and the
        verdict is the half that cannot be trusted on its own: `ok=False
        attempts=4` reads identically whether the model wrote bad code or the
        harness refused good code. The transcript is what separates them, and
        until now it went to stdout and nowhere a caller could reach.

        Server-sent events, one JSON record per event, in the order the loop
        produced them. The LAST event is `{"kind": "result", "result": {...}}`
        carrying exactly the envelope `/code` returns -- so a caller that
        ignores everything until the stream closes gets the blocking behaviour
        with no special handling, and one that reads along gets the run.

        Errors are events too, not statuses. The response has already been
        committed with a 200 by the time the first line is generated, so a dead
        llama-server three attempts in cannot become a 503 -- it arrives as a
        result whose `ok` is false and whose `error` says so.
        """
        if not str(body.get("spec", "")).strip():
            self._send(400, {"error": "spec is required"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        def emit(record):
            self.wfile.write(f"data: {json.dumps(record)}\n\n".encode())
            self.wfile.flush()

        spec, why = language_for(body.get("lang", "python"),
                                 body.get("spec", ""))
        if spec is None:
            emit({"kind": "result", "result": _answer(error=why)})
            return

        try:
            context, hint, docs_for_error = ground_in_docs(
                spec, body["spec"], store=body.get("store"),
                device=body.get("device", "cuda"),
                no_docs=bool(body.get("no_docs", False)))
            res = generate_validated_python(
                self.server.pc, body["spec"], context=context or "",
                spec=spec, max_retries=int(body.get("retries", 4)),
                use_contract=bool(body.get("contract", False)),
                error_hint=hint, error_docs=docs_for_error,
                packages=tuple(body.get("with") or ()),
                verbose=False, on_event=emit)
            answer = _answer(ok=bool(res["ok"]), code=res["text"],
                             tests=res.get("tests", ""),
                             contract=res.get("contract"),
                             attempts=res.get("attempts"),
                             error=res.get("error", ""))
        except RuntimeError as e:
            answer = _answer(error=str(e))
        except (BrokenPipeError, ConnectionResetError):
            # The reader closed the tab mid-run. Nothing left to report to.
            return
        emit({"kind": "result", "result": answer})

    def _send(self, status, payload):
        blob = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def do_GET(self):
        route = GET_ROUTES.get(self.path)
        if route is None:
            # A path that only exists for POST is 405, not 404: saying "no such
            # thing" about something that does exist sends a caller looking for
            # a typo instead of a verb.
            self._send(405 if self.path in POST_ROUTES else 404,
                       {"error": f"no GET {self.path}"})
            return
        self._send(*route(self.server.pc))

    def do_POST(self):
        route = POST_ROUTES.get(self.path)
        if route is None and self.path not in STREAM_ROUTES:
            self._send(405 if self.path in GET_ROUTES else 404,
                       {"error": f"no POST {self.path}"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except (ValueError, json.JSONDecodeError) as e:
            self._send(400, {"error": f"malformed request: {e}"})
            return
        if self.path in STREAM_ROUTES:
            self._stream_code(body)
            return
        try:
            self._send(*route(self.server.pc, body))
        except RuntimeError as e:
            # A dead llama-server is not this server being broken, and 503 is
            # the one status that says so without claiming the request was bad.
            self._send(503, _answer(error=str(e)))

    def log_message(self, fmt, *args):
        pass                                    # the CLI prints its own line


def make_server(pc, host="127.0.0.1", port=8100) -> ThreadingHTTPServer:
    """A configured server, not yet serving. Port 0 picks a free one."""
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.pc = pc
    return httpd


def serve(pc, host="127.0.0.1", port=8100):
    httpd = make_server(pc, host, port)
    bound = httpd.server_address[1]
    print(f"PureCoder listening on http://{host}:{bound}")
    print("  POST /code   POST /code/stream   POST /ask")
    print("  GET  /status   GET  /grammars")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        httpd.server_close()
