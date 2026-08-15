"""
purecoder/client.py

Thin client over llama-server's native /completion endpoint, with GBNF
grammar support. Forces valid .env / Makefile output and gets code-only
responses.

Native /completion (not /v1/chat/completions) because only the completion
endpoint accepts a raw `grammar` field. We apply Qwen's ChatML template
to the prompt ourselves.
"""

import time
from pathlib import Path

import requests


def strip_fences(text: str) -> str:
    """Remove a leading ```lang / trailing ``` if the model added them anyway."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]                       # drop opening fence (+ optional lang)
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]                  # drop closing fence
        text = "\n".join(lines)
    return text.strip()


# grammars ship inside the package, so they resolve regardless of cwd.
GRAMMARS_DIR = Path(__file__).parent / "grammars"


# A blip is not the server being down. Retrying a transport error costs a
# second and saves a batch when llama-server is restarted mid-run; retrying a
# refused connection three times only delays an honest failure, which is why
# the bound is small and the backoff short.
RETRIES = 2
BACKOFF = (0.5, 1.5)


class PureCoder:
    def __init__(self, base_url="http://localhost:8080", grammars_dir=None,
                 retries=RETRIES):
        self.base_url = base_url.rstrip("/")
        self.grammars_dir = Path(grammars_dir) if grammars_dir else GRAMMARS_DIR
        self._grammar_cache = {}                # avoid re-reading files each call
        self.retries = retries
        # One connection for the whole run instead of a fresh handshake per
        # call. `status.py` posts through this too -- it used bare requests.get
        # and so shared none of the configuration here, which is the drift a
        # single session exists to remove.
        self.session = requests.Session()

    def _post(self, path: str, payload: dict, timeout: int):
        """POST with a bounded retry on transport failures. -> response.

        Only ConnectionError and Timeout are retried. An HTTP status error is
        llama-server answering, and repeating the request hides a real
        server-side problem behind three identical failures.
        """
        for attempt in range(self.retries + 1):
            try:
                r = self.session.post(f"{self.base_url}{path}", json=payload,
                                      timeout=timeout)
                r.raise_for_status()
                return r
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt < self.retries:
                    time.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
                    continue
                raise self._failed(e) from e
            except requests.RequestException as e:
                raise self._failed(e) from e

    @staticmethod
    def _failed(e) -> RuntimeError:
        """The one wording other code matches on.

        `benchlog.classify` reads "llama-server request failed" to bucket a run
        as infrastructure rather than as a model failure, and a fake in
        test_contract.py raises it verbatim. Rewording it breaks failure
        attribution silently, so it is built in one place and tested.
        """
        return RuntimeError(f"llama-server request failed: {e}")

    def _load_grammar(self, name: str) -> str:
        if name not in self._grammar_cache:
            path = self.grammars_dir / f"{name}.gbnf"
            self._grammar_cache[name] = path.read_text()
        return self._grammar_cache[name]

    @staticmethod
    def _chatml(system: str, user: str) -> str:
        # ChatML, applied by hand: /completion is the only endpoint that takes
        # a raw GBNF grammar, and it does no templating. Both models this runs
        # on -- Qwen2.5-Coder-7B and Qwen3-Coder-30B-A3B -- expect this exact
        # special-token layout. GGUF chat-template metadata never reaches the
        # sampler, so re-downloading weights to fix a template does nothing.
        return (
            f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{user}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )

    def complete(self, system, user, grammar=None, n_predict=512,
                 temperature=0.2, repeat_penalty=1.15, repeat_last_n=256,
                 stop=None, timeout=120) -> dict:
        payload = {
            "prompt": self._chatml(system, user),
            "n_predict": n_predict,
            "temperature": temperature,
            # anti-spiral: penalize recently repeated tokens so the model
            # doesn't degenerate into endless near-identical lines.
            "repeat_penalty": repeat_penalty,
            "repeat_last_n": repeat_last_n,
            "cache_prompt": True,               # reuse system-prompt KV -> faster
        }
        if grammar:
            payload["grammar"] = self._load_grammar(grammar)
        if stop:
            payload["stop"] = stop

        data = self._post("/completion", payload, timeout).json()
        return {
            "text": data.get("content", ""),
            # Hit n_predict: the output is cut off mid-generation, and with a
            # grammar that means a valid *prefix* rather than a complete file.
            #
            # Two spellings, because llama.cpp changed its mind and this went
            # unnoticed: older builds set `stopped_limit`, current ones report
            # `stop_type: "limit"` and omit the boolean entirely -- so
            # `.get("stopped_limit", False)` answered "complete" for every
            # truncated generation, and every truncation retry in this project
            # was dead code. Found live, on a .env cut off mid-comment that
            # validated clean and was returned as ok.
            #
            # Note `data["truncated"]` is NOT this: it is llama.cpp saying the
            # PROMPT was cut to fit the context window, which is a different
            # failure and would fire on every long-context call.
            "truncated": bool(data.get("stopped_limit",
                                       data.get("stop_type") == "limit")),
            "tokens": data.get("tokens_predicted"),
            "raw": data,
        }

    # ---- per-artifact helpers -------------------------------------------

    def env_file(self, description, **kw) -> dict:
        return self.complete(
            # The grammar guarantees a comment is *shaped* like a comment, not
            # that it is brief -- left to itself the model writes paragraphs.
            system="You output only the contents of a .env file. "
                   "No prose, no explanation, no code fences. Every line must "
                   "be under 100 characters. Comments are at most one short "
                   "sentence. Never explain your reasoning in the file.",
            user=description,
            grammar="env",
            **kw,
        )

    def makefile(self, description, **kw) -> dict:
        return self.complete(
            system="You output only the contents of a Makefile. "
                   "No prose, no explanation, no code fences.",
            user=description,
            grammar="makefile",
            **kw,
        )

    def code(self, description, language="python", writer_system="", **kw) -> dict:
        out = self.complete(
            # The fix loop shows the model the tests its last attempt failed,
            # and left to itself it copies them into the implementation --
            # observed live, and they then run twice, since the real tests are
            # appended after the code.
            #
            # writer_system is the language's own extra demand, appended
            # verbatim. Most languages need none; C#'s harness is a file-based
            # app, so "no class wrapper, no Main" is what makes it assemble.
            system=f"You output only {language} code: the implementation and "
                   f"nothing else. No explanation, no markdown, no fences. "
                   f"Never include tests, assertions, example calls, print "
                   f"statements at module level, or a __main__ block."
                   + (f" {writer_system}." if writer_system else ""),
            user=description,
            grammar=None,
            **kw,
        )
        out["text"] = strip_fences(out["text"])
        return out


