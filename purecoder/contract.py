"""
purecoder/contract.py

The spec contract: a structured, grammar-constrained statement of what a
function must do, derived from prose before any code exists.

It exists because of the one failure the other layers cannot see. The writer
and the code-blind test designer both read the same prose; when they misread it
the same way, the tests agree with the bug and the loop reports success. A
contract makes that shared interpretation a single artifact you can read,
before any code exists.

It does not make the contract correct. It makes it visible. Those are
different claims and the docs keep them apart.
"""

import json
import keyword
import re

REQUIRED_KEYS = ("name", "summary", "params", "returns", "raises", "examples")

# an example whose `out` names an exception rather than a value.
_RAISES_EXAMPLE = re.compile(r"^raises\s+[A-Za-z_][A-Za-z0-9_]*$")

# a `returns` field that talks about raising instead of returning.
_RAISES_ANYWHERE = re.compile(r"^raises\b", re.IGNORECASE)


def _identifier_error(value, what):
    """Shared guard: contracts name things that must be valid Python names."""
    if not isinstance(value, str) or not value.isidentifier():
        return f"{what} is not a valid identifier: {value!r}"
    if keyword.iskeyword(value):
        return f"{what} is a Python keyword: {value!r}"
    return ""


def validate_contract(obj):
    """Semantic guards the grammar cannot express. Returns (ok, error).

    The grammar guarantees the SHAPE of the JSON. It cannot know that `name`
    must be a callable identifier or that an empty examples list makes the
    whole contract useless -- same division of labour as the Makefile
    validator, where `make -n` proves parsing and the guards prove sense.
    """
    if not isinstance(obj, dict):
        return False, f"contract is not a JSON object: {type(obj).__name__}"

    for key in REQUIRED_KEYS:
        if key not in obj:
            return False, f"missing required key {key!r}"

    err = _identifier_error(obj["name"], "name")
    if err:
        return False, err

    if not isinstance(obj["params"], list):
        return False, "params is not a list"
    for i, param in enumerate(obj["params"], 1):
        if not isinstance(param, dict) or "name" not in param or "type" not in param:
            return False, f"param {i}: needs both 'name' and 'type'"
        err = _identifier_error(param["name"], f"param {i} name")
        if err:
            return False, err

    if not isinstance(obj["raises"], list):
        return False, "raises is not a list"
    for i, item in enumerate(obj["raises"], 1):
        if not isinstance(item, dict) or "exc" not in item or "when" not in item:
            return False, f"raises {i}: needs both 'exc' and 'when'"
        err = _identifier_error(item["exc"], f"raises {i} exception name")
        if err:
            return False, err

    # `returns` describes the success value. Observed live: the model filled it
    # with "raises ValueError if unable to generate", leaving the contract
    # silent about what the function actually produces.
    if _RAISES_ANYWHERE.match(str(obj["returns"]).strip()):
        return False, ("returns describes an exception -- it must state the "
                       "value returned on success; exceptions go in 'raises'")

    if not isinstance(obj["examples"], list) or not obj["examples"]:
        return False, "examples is empty -- a contract with no example is untestable"
    seen = set()
    for i, ex in enumerate(obj["examples"], 1):
        if not isinstance(ex, dict):
            return False, f"example {i}: not an object"
        for side in ("in", "out"):
            if side not in ex or not isinstance(ex[side], str):
                return False, f"example {i}: missing string {side!r}"
        # An example that states no outcome grounds neither the writer nor the
        # tester. Observed live in a test-first run: `word_count('hello world')
        # -> ` with nothing after the arrow. The same emptiness `examples: []`
        # is already refused for, one level down.
        if not ex["out"].strip():
            return False, (f"example {i} states no outcome -- say what "
                           f"{obj['name']} returns or raises for that input")
        # An example that calls nothing. Observed live: `count_words(text)`
        # came back with `count_words() -> raises ValueError` as its
        # demonstration of the empty-input case, the test designer implemented
        # it exactly, and correct code failed the loop three times over a call
        # that cannot be made. Structural rather than semantic -- a contract
        # param has a name and a type and no default, so a function with a
        # declared parameter cannot be invoked with none.
        if obj["params"] and not ex["in"].strip():
            return False, (f"example {i} passes no arguments, but "
                           f"{obj['name']} declares "
                           f"{', '.join(p['name'] for p in obj['params'])} -- "
                           f"show the call that produces this outcome")
        # A repeated example is a fair sign the model had nothing further to
        # say about the spec.
        key = (ex["in"].strip(), ex["out"].strip())
        if key in seen:
            return False, (f"example {i} repeats an earlier example "
                           f"({ex['in']!r} -> {ex['out']!r})")
        seen.add(key)

    # Every example raising means the contract never states what the function
    # DOES, only how it fails -- so it grounds the tester in nothing. Observed
    # live: a "display a graph" spec produced two identical `raises ValueError`
    # examples and said nothing about the success path.
    if all(_RAISES_EXAMPLE.match(ex["out"].strip()) for ex in obj["examples"]):
        return False, ("every example raises -- give at least one showing what "
                       "the function returns on valid input")

    return True, ""


CONTRACT_SYSTEM = (
    "You output only a JSON contract describing the function the user asks "
    "for. No prose, no explanation, no code fences. Capture ONLY what the "
    "description states -- do not invent requirements. Every error case the "
    "description mentions must appear in 'raises'. Give at least two "
    "'examples': 'in' is the argument list as Python source (empty string for "
    "no arguments), 'out' is either a literal value for the expected return "
    "or the exact form 'raises ExceptionName'. Examples must be DISTINCT, and "
    "at least one must show a successful call rather than an error -- a "
    "contract whose every example raises never says what the function does. "
    "Both sides must be plain literal data (numbers, strings, lists, dicts, "
    "tuples, True/False/None); never a function call, lambda, or variable. "
    "Write strings with quotes: \"'hello'\", not hello. For the error form "
    "write ONLY 'raises ValueError' -- never add a colon or a message after "
    "the exception name. 'returns' describes the value returned on success; "
    "it must not mention exceptions, which belong in 'raises'."
)


def render_contract(contract):
    """The 'what I assumed' block. This is the whole point of the layer: a
    wrong interpretation is worth far more when it is visible than when it is
    buried in generated tests."""
    lines = [f"Contract for `{contract['name']}`: {contract['summary']}"]

    params = contract.get("params") or []
    if params:
        joined = ", ".join(f"{p['name']}: {p['type']}" for p in params)
        lines.append(f"  params  : {joined}")
    else:
        lines.append("  params  : (none)")

    lines.append(f"  returns : {contract['returns']}")

    raises = contract.get("raises") or []
    if raises:
        for item in raises:
            lines.append(f"  raises  : {item['exc']} when {item['when']}")
    else:
        lines.append("  raises  : (nothing)")

    for ex in contract.get("examples") or []:
        lines.append(f"  example : {contract['name']}({ex['in']}) -> {ex['out']}")

    return "\n".join(lines)


def derive_contract(pc, description, max_retries=3, verbose=True, say=None):
    """Prose -> validated contract. Returns (contract, error); contract is
    None on failure so the caller can fall back rather than abort.

    Same write -> validate -> fix shape as the config loop, with JSON decoding
    counted as a validation failure: a truncated object is invalid JSON, so
    truncation is retried by the same path.
    """
    # The loop's reporter when there is one, so contract derivation appears in
    # the same transcript as everything else it is part of. Imported lazily:
    # execute.py already imports this module, and a module-level import back
    # would be a cycle.
    if say is None:
        from .execute import reporter
        say = reporter(verbose)
    task, error = description, ""

    for attempt in range(1, max_retries + 1):
        try:
            res = pc.complete(system=CONTRACT_SYSTEM, user=task,
                              grammar="contract", n_predict=512)
        except RuntimeError as e:
            # A dead server must not kill the run -- the caller falls back.
            return None, str(e)

        try:
            obj = json.loads(res["text"])
            ok, error = validate_contract(obj)
        except json.JSONDecodeError as e:
            ok, error = False, f"not valid JSON: {e}"

        if ok:
            say("contract", f"[contract] derived on attempt {attempt}", attempt)
            return obj, ""

        say("contract", f"[contract] attempt {attempt} rejected: {error} "
                        f"-> retrying", attempt)
        task = (f"{description}\n\n"
                f"Your previous contract was rejected: {error}\n"
                f"Output only the corrected JSON contract, nothing else.")

    return None, error
