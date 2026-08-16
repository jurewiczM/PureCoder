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
    info["languages"] = {n: languages.get(n).available()[0]
                         for n in languages.names()}
    return 200, info


POST_ROUTES = {
    "/code": _code,
    "/ask": partial(_code, required_docs=True),
}
GET_ROUTES = {"/status": _status}


class _Handler(BaseHTTPRequestHandler):
    server_version = "PureCoder"

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
        if route is None:
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
    print("  POST /code   POST /ask   GET /status")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        httpd.server_close()
