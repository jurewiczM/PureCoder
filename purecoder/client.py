"""
purecoder/client.py

Thin client over llama-server's native /completion endpoint, with GBNF
grammar support. Forces valid .env / Makefile output and gets code-only
responses.

Native /completion (not /v1/chat/completions) because only the completion
endpoint accepts a raw `grammar` field. We apply Qwen's ChatML template
to the prompt ourselves.
"""

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


class PureCoder:
    def __init__(self, base_url="http://localhost:8080", grammars_dir=None):
        self.base_url = base_url.rstrip("/")
        self.grammars_dir = Path(grammars_dir) if grammars_dir else GRAMMARS_DIR
        self._grammar_cache = {}                # avoid re-reading files each call

    def _load_grammar(self, name: str) -> str:
        if name not in self._grammar_cache:
            path = self.grammars_dir / f"{name}.gbnf"
            self._grammar_cache[name] = path.read_text()
        return self._grammar_cache[name]

    @staticmethod
    def _chatml(system: str, user: str) -> str:
        # Qwen2.5-Coder-Instruct expects this exact special-token layout.
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

        try:
            r = requests.post(f"{self.base_url}/completion", json=payload,
                              timeout=timeout)
            r.raise_for_status()
        except requests.RequestException as e:
            raise RuntimeError(f"llama-server request failed: {e}") from e

        data = r.json()
        return {
            "text": data.get("content", ""),
            # stopped_limit == True -> hit n_predict, output cut off mid-gen.
            # With a grammar that's a valid *prefix*, not a complete file.
            "truncated": data.get("stopped_limit", False),
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


