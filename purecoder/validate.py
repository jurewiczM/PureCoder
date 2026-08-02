"""
purecoder/validate.py

Phase 3: real-tool validators + the write -> validate -> fix loop.

Each validator returns (ok: bool, error: str). The loop generates an
artifact, runs its validator, and on failure feeds the error back into a
regeneration call, up to max_retries.

Design boundary made concrete: grammars (Phase 2) guarantee SHAPE. `make -n`
confirms a Makefile PARSES. But `make` is lenient -- it will happily parse
degenerate junk (50 identical rm lines, malformed dot-targets). So the
Makefile validator adds semantic sanity guards on top of the parse check.
A validator that rubber-stamps garbage is worse than none.
"""

import os
import subprocess
import tempfile
from collections import Counter

# GNU make's built-in special targets -- legitimate lines starting with '.'
MAKE_SPECIAL_TARGETS = {
    ".PHONY", ".DEFAULT", ".SUFFIXES", ".PRECIOUS", ".INTERMEDIATE",
    ".SECONDARY", ".SECONDEXPANSION", ".DELETE_ON_ERROR", ".IGNORE",
    ".SILENT", ".EXPORT_ALL_VARIABLES", ".NOTPARALLEL", ".ONESHELL", ".POSIX",
}

# if one command word appears more than this many times across recipe lines,
# treat it as a generation spiral rather than a real target.
MAX_REPEATED_CMD = 15


# ---- validators ---------------------------------------------------------

def validate_env(text: str):
    """Structural check on a .env file: KEY=VALUE, no dupes."""
    seen = set()
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            return False, f"line {i}: not KEY=VALUE -> {line!r}"
        key = line.split("=", 1)[0].strip()
        if not key:
            return False, f"line {i}: empty key"
        if key in seen:
            return False, f"line {i}: duplicate key {key!r}"
        seen.add(key)
    return True, ""


def validate_python(text: str):
    """Syntax check via in-process compile(). Catches SyntaxError only --
    not import/runtime errors (those need execution, added later)."""
    try:
        compile(text, "<generated>", "exec")
        return True, ""
    except SyntaxError as e:
        return False, f"SyntaxError: {e.msg} at line {e.lineno}"


def validate_makefile(text: str):
    """Parse check (make -n) PLUS semantic guards make itself won't catch."""
    lines = text.splitlines()

    # guard 1: degeneration -- a runaway of near-identical recipe commands.
    recipes = [line for line in lines if line.startswith("\t") and line.strip()]
    if recipes:
        heads = Counter(r.strip().split()[0] for r in recipes)
        top_cmd, count = heads.most_common(1)[0]
        if count > MAX_REPEATED_CMD:
            return False, (f"degenerate output: command {top_cmd!r} repeated "
                           f"{count} times -- likely a generation spiral")

    # guard 2: malformed dot-targets, e.g. '.rm: -f foo.txt'
    for i, line in enumerate(lines, 1):
        if line.startswith("\t"):
            continue                            # recipe line, not a target
        if line.startswith(".") and ":" in line:
            target = line.split(":", 1)[0].strip()
            if target not in MAKE_SPECIAL_TARGETS:
                return False, f"line {i}: suspicious dot-target {target!r}"

    # guard 3: the parse check -- make -n dry-runs the default target,
    # never executing recipes, so it's safe even with rm/clean targets.
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "Makefile")
        with open(path, "w") as f:
            f.write(text)
        try:
            proc = subprocess.run(
                ["make", "-n", "-f", path],
                cwd=d, capture_output=True, text=True, timeout=10,
            )
        except FileNotFoundError:
            return True, "make not installed, parse check skipped"
        except subprocess.TimeoutExpired:
            return False, "make timed out (possible infinite recursion)"

    if proc.returncode != 0:
        return False, (proc.stderr.strip() or proc.stdout.strip()
                       or f"make exited {proc.returncode}")
    return True, ""


VALIDATORS = {
    "env": validate_env,
    "makefile": validate_makefile,
    "python": validate_python,
}


# ---- the write -> validate -> fix loop ----------------------------------

def _generate(pc, kind, description, **kw):
    """Route to the right client helper for this artifact kind."""
    if kind == "env":
        return pc.env_file(description, **kw)
    if kind == "makefile":
        return pc.makefile(description, **kw)
    if kind == "python":
        return pc.code(description, language="python", **kw)
    raise ValueError(f"unknown kind: {kind}")


def generate_validated(pc, kind, description, max_retries=3, verbose=True, **kw):
    """Generate an artifact, validate it, retry with the error fed back on
    failure. Returns {ok, text, attempts, error}."""
    validate = VALIDATORS[kind]
    task = description
    text, error = "", ""

    for attempt in range(1, max_retries + 1):
        res = _generate(pc, kind, task, **kw)
        text = res["text"]

        if res["truncated"]:
            error = "output was cut off (hit n_predict)"
            task = (f"{description}\n\nYour previous output was cut off. "
                    f"Produce a shorter, complete file.")
            if verbose:
                print(f"[attempt {attempt}] truncated -> retrying")
            continue

        ok, error = validate(text)
        if ok:
            if verbose:
                note = f" ({error})" if error else ""
                print(f"[attempt {attempt}] valid{note}")
            return {"ok": True, "text": text, "attempts": attempt, "error": ""}

        if verbose:
            print(f"[attempt {attempt}] failed: {error} -> retrying")
        task = (f"{description}\n\n"
                f"Your previous attempt failed validation with this error:\n"
                f"{error}\n\n"
                f"Output only the corrected file, nothing else.")

    return {"ok": False, "text": text, "attempts": max_retries, "error": error}


