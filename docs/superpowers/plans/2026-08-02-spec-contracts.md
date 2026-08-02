# Spec Contracts & Anchor Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Derive a grammar-constrained contract from a prose spec, generate deterministic anchor assertions from its examples, and show the user the interpretation both writer and tester are working from.

**Architecture:** A new `purecoder/contract.py` sits between the prose spec and the existing execution loop. It derives a JSON contract using a new GBNF grammar, validates it semantically, turns its `examples` into assertions mechanically (no model), and renders it for display. `generate_validated_python` gains two optional parameters that leave current behaviour byte-identical when unused. The executor, fix loop, and `lint_tests` are unchanged — they receive better inputs, not new logic.

**Tech Stack:** Python 3.10+, stdlib only (`ast`, `json`, `re`, `builtins`), pytest, ruff, llama.cpp GBNF grammars.

## Global Constraints

- Python 3.10+ — CI runs 3.10, 3.11, 3.12. No syntax newer than 3.10.
- No new runtime dependencies. `contract.py` uses stdlib only.
- `ruff check purecoder tests examples` must pass. Config in `pyproject.toml`: `select = ["E", "F", "W", "I", "UP", "B"]`, `ignore = ["E501"]`, line-length 100.
- All new tests are model-independent: no GPU, no llama-server, no network. Use the `FakeModel` pattern already in `tests/test_loops.py`.
- The existing 66 tests must keep passing after every task.
- Default behaviour of `generate_validated_python` must not change when `use_contract=False` and `contract=None`.
- Comment density and naming follow the existing modules: lowercase, terse, `--` for em-dashes in docstrings, explanatory comments only where a choice is non-obvious.

---

### Task 1: Contract schema — grammar and validator

Defines what a valid contract *is*: the GBNF that makes malformed output impossible, and the semantic guards the grammar cannot express.

**Files:**
- Create: `purecoder/grammars/contract.gbnf`
- Create: `purecoder/contract.py`
- Create: `tests/test_contract.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `validate_contract(obj: dict) -> tuple[bool, str]`, returning `(ok, error)` in the same shape as every validator in `purecoder/validate.py`. Grammar name `"contract"` loadable via `PureCoder._load_grammar`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_contract.py`:

```python
"""The contract layer: schema guards, rendering, derivation."""

import pytest

from purecoder.client import GRAMMARS_DIR, PureCoder
from purecoder.contract import validate_contract

GOOD = {
    "name": "parse_ports",
    "summary": "parse comma-separated ports",
    "params": [{"name": "s", "type": "str"}],
    "returns": "sorted list of unique int",
    "raises": [{"exc": "ValueError", "when": "port outside 1-65535"}],
    "examples": [
        {"in": "'80,443'", "out": "[80, 443]"},
        {"in": "'99999'", "out": "raises ValueError"},
    ],
}


def _without(key):
    obj = {k: v for k, v in GOOD.items() if k != key}
    return obj


# ---- grammar -------------------------------------------------------------

def test_contract_grammar_ships_as_package_data():
    assert (GRAMMARS_DIR / "contract.gbnf").is_file()


def test_contract_grammar_loads():
    assert PureCoder()._load_grammar("contract").startswith("root")


# ---- validator -----------------------------------------------------------

def test_validate_accepts_a_good_contract():
    ok, err = validate_contract(GOOD)
    assert ok, err


def test_validate_rejects_non_dict():
    ok, err = validate_contract(["not", "a", "dict"])
    assert not ok
    assert "object" in err


@pytest.mark.parametrize("key", ["name", "summary", "params", "returns",
                                 "raises", "examples"])
def test_validate_rejects_missing_required_key(key):
    ok, err = validate_contract(_without(key))
    assert not ok
    assert key in err


def test_validate_rejects_non_identifier_name():
    ok, err = validate_contract({**GOOD, "name": "parse ports"})
    assert not ok
    assert "identifier" in err


def test_validate_rejects_python_keyword_as_name():
    ok, err = validate_contract({**GOOD, "name": "class"})
    assert not ok
    assert "keyword" in err


def test_validate_rejects_non_identifier_param_name():
    ok, err = validate_contract({**GOOD, "params": [{"name": "a b", "type": "str"}]})
    assert not ok
    assert "identifier" in err


def test_validate_rejects_non_identifier_exception_name():
    ok, err = validate_contract(
        {**GOOD, "raises": [{"exc": "not a name", "when": "x"}]})
    assert not ok
    assert "identifier" in err


def test_validate_rejects_empty_examples():
    ok, err = validate_contract({**GOOD, "examples": []})
    assert not ok
    assert "example" in err


def test_validate_rejects_example_missing_a_side():
    ok, err = validate_contract({**GOOD, "examples": [{"in": "'80'"}]})
    assert not ok
    assert "out" in err


def test_validate_accepts_empty_params_and_raises():
    """A zero-argument function that raises nothing is a legitimate contract."""
    ok, err = validate_contract({**GOOD, "params": [], "raises": []})
    assert ok, err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_contract.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'purecoder.contract'`

- [ ] **Step 3: Write the grammar**

Create `purecoder/grammars/contract.gbnf`:

```
root       ::= "{" ws
               "\"name\":"     ws string "," ws
               "\"summary\":"  ws string "," ws
               "\"params\":"   ws params "," ws
               "\"returns\":"  ws string "," ws
               "\"raises\":"   ws raises "," ws
               "\"examples\":" ws examples ws
               "}"

params     ::= "[" ws (param (ws "," ws param)*)? ws "]"
param      ::= "{" ws "\"name\":" ws string "," ws "\"type\":" ws string ws "}"

raises     ::= "[" ws (raise (ws "," ws raise)*)? ws "]"
raise      ::= "{" ws "\"exc\":" ws string "," ws "\"when\":" ws string ws "}"

examples   ::= "[" ws (example (ws "," ws example)*)? ws "]"
example    ::= "{" ws "\"in\":" ws string "," ws "\"out\":" ws string ws "}"

string     ::= "\"" ([^"\\] | "\\" ["\\/bfnrt])* "\""
ws         ::= [ \t\n]*
```

Key order is fixed deliberately: a small model produces a valid object more
reliably when it has one path through the schema than when key order is free.

- [ ] **Step 4: Write the validator**

Create `purecoder/contract.py`:

```python
"""
purecoder/contract.py

The spec contract: a structured, grammar-constrained statement of what a
function must do, derived from prose before any code exists.

It exists because of the one failure the other layers cannot see. The writer
and the code-blind test designer both read the same prose; when they misread it
the same way, the tests agree with the bug and the loop reports success. A
contract makes that shared interpretation a single artifact you can read, and
turns the behaviour the spec states explicitly into assertions generated by
code rather than by a model.

It does not make the contract correct. It makes it visible. Those are
different claims and the docs keep them apart.
"""

import keyword

REQUIRED_KEYS = ("name", "summary", "params", "returns", "raises", "examples")


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

    if not isinstance(obj["examples"], list) or not obj["examples"]:
        return False, "examples is empty -- a contract with no example is untestable"
    for i, ex in enumerate(obj["examples"], 1):
        if not isinstance(ex, dict):
            return False, f"example {i}: not an object"
        for side in ("in", "out"):
            if side not in ex or not isinstance(ex[side], str):
                return False, f"example {i}: missing string {side!r}"

    return True, ""
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_contract.py -q`
Expected: PASS, 17 tests (6 come from the parametrized missing-key case).

- [ ] **Step 6: Verify nothing else broke and lint is clean**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check purecoder tests examples`
Expected: 83 passed, `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add purecoder/contract.py purecoder/grammars/contract.gbnf tests/test_contract.py
git commit -m "feat(contract): grammar and semantic validator for spec contracts"
```

---

### Task 2: Mechanical anchor tests

Turns contract examples into assertions without a model. This is the piece that changes the trust model: assertions encoding explicitly-stated behaviour stop being model-authored.

Its own module, not part of `contract.py`: the rest of the contract layer
handles a contract's lifecycle (produce, validate, show), while this generates
executable Python from it. Different responsibility, and the split lets this
task run in parallel with Task 1.

**Files:**
- Create: `purecoder/anchors.py`
- Create: `tests/test_anchors.py`

**Interfaces:**
- Consumes: nothing. It reads a contract dict by shape only, so it does not depend on Task 1.
- Produces: `anchor_tests(contract: dict) -> tuple[str, list[str]]`, returning `(source, dropped)`. `source` is assertion code, possibly empty. `dropped` is a list of human-readable reasons, one per skipped example.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_anchors.py`:

```python
"""Anchor generation: contract examples -> assertions, no model involved."""

import ast

from purecoder.anchors import anchor_tests

GOOD = {
    "name": "parse_ports",
    "summary": "parse comma-separated ports",
    "params": [{"name": "s", "type": "str"}],
    "returns": "sorted list of unique int",
    "raises": [{"exc": "ValueError", "when": "port outside 1-65535"}],
    "examples": [
        {"in": "'80,443'", "out": "[80, 443]"},
        {"in": "'99999'", "out": "raises ValueError"},
    ],
}


def test_anchors_emit_an_equality_assertion():
    src, dropped = anchor_tests(GOOD)
    assert "assert parse_ports('80,443') == [80, 443]" in src
    assert dropped == []


def test_anchors_emit_a_raises_block():
    src, _ = anchor_tests(GOOD)
    assert "try:" in src
    assert "parse_ports('99999')" in src
    assert "assert False" in src
    assert "except ValueError:" in src


def test_anchor_output_always_parses():
    src, _ = anchor_tests(GOOD)
    ast.parse(src)


def test_anchors_drop_a_malformed_example():
    contract = {**GOOD, "examples": [
        {"in": "'80,443'", "out": "[80, 443]"},
        {"in": "'80'", "out": "[80,"},          # unbalanced -- will not parse
    ]}
    src, dropped = anchor_tests(contract)
    assert "assert parse_ports('80,443') == [80, 443]" in src
    assert len(dropped) == 1
    assert "example 2" in dropped[0]
    ast.parse(src)


def test_anchors_drop_a_custom_exception():
    """An anchor is written before the code that would define the class."""
    contract = {**GOOD, "examples": [{"in": "'x'", "out": "raises MyOwnError"}]}
    src, dropped = anchor_tests(contract)
    assert src == ""
    assert len(dropped) == 1
    assert "MyOwnError" in dropped[0]


def test_anchors_accept_any_builtin_exception():
    contract = {**GOOD, "examples": [{"in": "None", "out": "raises TypeError"}]}
    src, dropped = anchor_tests(contract)
    assert "except TypeError:" in src
    assert dropped == []


def test_anchors_return_empty_when_every_example_is_bad():
    contract = {**GOOD, "examples": [
        {"in": "(", "out": "1"},
        {"in": "'x'", "out": "raises NotAnException"},
    ]}
    src, dropped = anchor_tests(contract)
    assert src == ""
    assert len(dropped) == 2


def test_anchors_handle_a_zero_argument_call():
    contract = {**GOOD, "name": "build", "examples": [{"in": "", "out": "42"}]}
    src, dropped = anchor_tests(contract)
    assert "assert build() == 42" in src
    assert dropped == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_anchors.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'purecoder.anchors'`

- [ ] **Step 3: Implement anchor generation**

Create `purecoder/anchors.py`:

```python
"""
purecoder/anchors.py

Contract examples -> executable assertions, generated by code rather than by a
model. This is what changes the trust model: the assertions encoding behaviour
the spec states explicitly are no longer the model's opinion about its own
work.

Kept apart from contract.py deliberately. That module handles a contract's
lifecycle; this one emits Python source from it.
"""

import ast
import builtins
import re

# `out` may name an exception instead of a value: "raises ValueError".
RAISES = re.compile(r"^raises\s+([A-Za-z_][A-Za-z0-9_]*)$")


def _is_builtin_exception(name):
    obj = getattr(builtins, name, None)
    return isinstance(obj, type) and issubclass(obj, BaseException)


def anchor_tests(contract):
    """Contract examples -> assertion source, generated by code not by a model.

    Returns (source, dropped). Every block is parsed before it is accepted, so
    one malformed example cannot poison the suite -- it is dropped with a
    reason instead, and the caller reports it.
    """
    name = contract["name"]
    blocks, dropped = [], []

    for i, ex in enumerate(contract.get("examples", []), 1):
        args = ex.get("in", "").strip()
        expected = ex.get("out", "").strip()

        raises = RAISES.match(expected)
        if raises:
            exc = raises.group(1)
            # An anchor runs before the implementation exists, so it can only
            # name an exception that already does. Custom classes reach the
            # test designer through the rendered contract instead.
            if not _is_builtin_exception(exc):
                dropped.append(f"example {i}: {exc} is not a built-in exception")
                continue
            block = (f"try:\n"
                     f"    {name}({args})\n"
                     f"    assert False\n"
                     f"except {exc}:\n"
                     f"    pass")
        else:
            block = f"assert {name}({args}) == {expected}"

        try:
            ast.parse(block)
        except SyntaxError:
            dropped.append(f"example {i}: does not parse -- "
                           f"in={args!r} out={expected!r}")
            continue
        blocks.append(block)

    return "\n".join(blocks), dropped
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_anchors.py -q`
Expected: PASS, 8 tests.

- [ ] **Step 5: Verify the full suite and lint**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check purecoder tests examples`
Expected: 91 passed, `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add purecoder/anchors.py tests/test_anchors.py
git commit -m "feat(anchors): generate anchor assertions from contract examples"
```

---

### Task 3: Rendering and derivation

Produces the contract from prose, and the human-readable block that makes a wrong interpretation visible.

**Files:**
- Modify: `purecoder/contract.py` (append)
- Modify: `tests/test_contract.py` (append)

**Interfaces:**
- Consumes: `validate_contract` (Task 1). A model object exposing `complete(system, user, grammar=None, n_predict=...) -> dict` with a `"text"` key — satisfied by `PureCoder` and by the test `FakeModel`.
- Produces: `render_contract(contract: dict) -> str`; `derive_contract(pc, description: str, max_retries: int = 3, verbose: bool = True) -> tuple[dict | None, str]` returning `(contract, error)` where `contract` is `None` on failure.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_contract.py`:

```python
# ---- rendering -----------------------------------------------------------

def test_render_contains_the_fields_a_reader_needs():
    text = render_contract(GOOD)
    assert "parse_ports" in text
    assert "sorted list of unique int" in text
    assert "ValueError" in text
    assert "port outside 1-65535" in text
    assert "'80,443'" in text


def test_render_handles_empty_params_and_raises():
    text = render_contract({**GOOD, "params": [], "raises": []})
    assert "parse_ports" in text
    assert isinstance(text, str) and text.strip()


# ---- derivation ----------------------------------------------------------

class FakeContractModel:
    """Returns queued completions in order; records the prompts it saw."""

    def __init__(self, completions):
        self.completions = list(completions)
        self.prompts = []

    def complete(self, system, user, grammar=None, **kw):
        self.prompts.append(user)
        text = (self.completions.pop(0) if len(self.completions) > 1
                else self.completions[0])
        return {"text": text, "truncated": False, "tokens": 1, "raw": {}}


def test_derive_returns_a_validated_contract():
    pc = FakeContractModel([json.dumps(GOOD)])
    contract, err = derive_contract(pc, "parse ports", verbose=False)
    assert err == ""
    assert contract["name"] == "parse_ports"


def test_derive_requests_the_contract_grammar():
    pc = FakeContractModel([json.dumps(GOOD)])

    seen = {}
    original = pc.complete

    def spy(system, user, grammar=None, **kw):
        seen["grammar"] = grammar
        return original(system, user, grammar=grammar, **kw)

    pc.complete = spy
    derive_contract(pc, "parse ports", verbose=False)
    assert seen["grammar"] == "contract"


def test_derive_retries_on_invalid_json_and_feeds_the_error_back():
    pc = FakeContractModel(["{not json", json.dumps(GOOD)])
    contract, err = derive_contract(pc, "parse ports", verbose=False)
    assert contract is not None and err == ""
    assert "JSON" in pc.prompts[1]


def test_derive_retries_when_the_validator_rejects():
    bad = json.dumps({**GOOD, "name": "not an identifier"})
    pc = FakeContractModel([bad, json.dumps(GOOD)])
    contract, err = derive_contract(pc, "parse ports", verbose=False)
    assert contract is not None and err == ""
    assert "identifier" in pc.prompts[1]


def test_derive_gives_up_and_reports_the_error():
    pc = FakeContractModel(["{not json"])
    contract, err = derive_contract(pc, "parse ports", max_retries=2,
                                    verbose=False)
    assert contract is None
    assert err


def test_derive_survives_a_dead_server():
    class DeadModel:
        def complete(self, *a, **kw):
            raise RuntimeError("llama-server request failed: connection refused")

    contract, err = derive_contract(DeadModel(), "parse ports", verbose=False)
    assert contract is None
    assert "connection refused" in err
```

Add `import json` at the top of the file and extend the import to
`from purecoder.contract import (anchor_tests, derive_contract,
render_contract, validate_contract)`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_contract.py -q`
Expected: FAIL — `ImportError: cannot import name 'derive_contract'`

- [ ] **Step 3: Implement rendering and derivation**

Append to `purecoder/contract.py`, and add `import json` to the imports:

```python
CONTRACT_SYSTEM = (
    "You output only a JSON contract describing the function the user asks "
    "for. No prose, no explanation, no code fences. Capture ONLY what the "
    "description states -- do not invent requirements. Every error case the "
    "description mentions must appear in 'raises'. Give at least two "
    "'examples': 'in' is the argument list as Python source (empty string for "
    "no arguments), 'out' is either a Python expression for the expected "
    "return value or the exact form 'raises ExceptionName'."
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


def derive_contract(pc, description, max_retries=3, verbose=True):
    """Prose -> validated contract. Returns (contract, error); contract is
    None on failure so the caller can fall back rather than abort.

    Same write -> validate -> fix shape as the config loop, with JSON decoding
    counted as a validation failure: a truncated object is invalid JSON, so
    truncation is retried by the same path.
    """
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
            if verbose:
                print(f"[contract] derived on attempt {attempt}")
            return obj, ""

        if verbose:
            print(f"[contract] attempt {attempt} rejected: {error} -> retrying")
        task = (f"{description}\n\n"
                f"Your previous contract was rejected: {error}\n"
                f"Output only the corrected JSON contract, nothing else.")

    return None, error
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_contract.py -q`
Expected: PASS, 33 tests.

- [ ] **Step 5: Verify the full suite and lint**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check purecoder tests examples`
Expected: 99 passed, `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add purecoder/contract.py tests/test_contract.py
git commit -m "feat(contract): derive contracts from prose and render them for review"
```

---

### Task 4: Wire the contract into the execution loop

The behavioural change. Anchors join the test source, the gate is restricted to the designed portion, and a failed derivation degrades to today's path.

**Files:**
- Modify: `purecoder/execute.py:102` (`lint_tests` signature), `purecoder/execute.py:156-178` (`design_tests`), `purecoder/execute.py:183-236` (`generate_validated_python`)
- Modify: `tests/test_loops.py` (append)
- Modify: `tests/test_execute.py` (append)

**Interfaces:**
- Consumes: `derive_contract`, `anchor_tests`, `render_contract` (Task 3).
- Produces: `generate_validated_python(pc, description, tests=None, contract=None, use_contract=False, max_retries=3, timeout=10, verbose=True, **kw)` returning the existing dict plus two keys: `"contract"` (dict or `None`) and `"anchors"` (str, possibly empty). `lint_tests(tests, targets=None, min_assertions=MIN_ASSERTIONS)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_execute.py`:

```python
def test_gate_accepts_a_lower_assertion_floor():
    """With anchors carrying the spec, the designed portion can be smaller."""
    ok, err = lint_tests("assert add(1, 2) == 3\n", targets=["add"],
                         min_assertions=1)
    assert ok, err
```

Append to `tests/test_loops.py`:

```python
# ---- contracts -----------------------------------------------------------

CONTRACT = {
    "name": "add",
    "summary": "add two numbers",
    "params": [{"name": "a", "type": "int"}, {"name": "b", "type": "int"}],
    "returns": "int",
    "raises": [],
    "examples": [
        {"in": "1, 2", "out": "3"},
        {"in": "0, 0", "out": "0"},
    ],
}


def test_contract_is_derived_and_its_anchors_reach_the_executor():
    pc = FakeModel(code_outputs=[GOOD_CODE],
                   completions=[json.dumps(CONTRACT), GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", use_contract=True,
                                    verbose=False)
    assert res["ok"]
    assert res["contract"]["name"] == "add"
    assert "assert add(1, 2) == 3" in res["anchors"]
    assert res["anchors"] in res["tests"]


def test_contract_reaches_the_prompts_alongside_the_prose():
    pc = FakeModel(code_outputs=[GOOD_CODE],
                   completions=[json.dumps(CONTRACT), GOOD_TESTS])
    generate_validated_python(pc, "add two numbers", use_contract=True,
                              verbose=False)
    grounded = [p for p in pc.prompts if "Contract for" in p]
    assert grounded, "no prompt carried the rendered contract"
    assert all("add two numbers" in p for p in grounded)


def test_supplied_contract_skips_derivation():
    pc = FakeModel(code_outputs=[GOOD_CODE], completions=[GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", contract=CONTRACT,
                                    use_contract=True, verbose=False)
    assert res["ok"]
    assert res["contract"] is CONTRACT


def test_failed_derivation_falls_back_to_the_plain_path():
    """A dead or unhelpful contract call must never make the tool worse."""
    pc = FakeModel(code_outputs=[GOOD_CODE],
                   completions=["{not json", GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", use_contract=True,
                                    max_retries=1, verbose=False)
    assert res["ok"]
    assert res["contract"] is None
    assert res["anchors"] == ""


def test_contract_off_by_default_changes_nothing():
    pc = FakeModel(code_outputs=[GOOD_CODE], completions=[GOOD_TESTS])
    res = generate_validated_python(pc, "add two numbers", verbose=False)
    assert res["ok"]
    assert res["contract"] is None
    assert res["anchors"] == ""
    assert not any("Contract for" in p for p in pc.prompts)


def test_gate_sees_only_the_designed_portion():
    """Free anchors must not satisfy the gate on a lazy tester's behalf.

    The contract yields two anchors. If the gate counted the combined source
    it would see 2 assertions and pass a designer that wrote none. Counting
    the designed portion alone sees 0 and rejects.
    """
    lazy = "x = 1\n"                          # no assertions at all
    pc = FakeModel(code_outputs=[GOOD_CODE],
                   completions=[json.dumps(CONTRACT), lazy, GOOD_TESTS])
    generate_validated_python(pc, "add two numbers", use_contract=True,
                              verbose=False)
    assert any("rejected" in p for p in pc.prompts)
```

Add `import json` to the top of `tests/test_loops.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_loops.py tests/test_execute.py -q`
Expected: FAIL — `TypeError: generate_validated_python() got an unexpected keyword argument 'use_contract'` and `lint_tests() got an unexpected keyword argument 'min_assertions'`

- [ ] **Step 3: Make the gate's floor tunable**

In `purecoder/execute.py`, change the `lint_tests` signature and the
assertion-count check:

```python
def lint_tests(tests, targets=None, min_assertions=MIN_ASSERTIONS):
```

and:

```python
    # mode 2: too few assertions.
    asserts = [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
    if len(asserts) < min_assertions:
        return False, (f"only {len(asserts)} assertion(s); "
                       f"need at least {min_assertions}")
```

Update the docstring line listing the five modes to mention the floor is
caller-tunable.

- [ ] **Step 4: Thread the floor through the designer**

Change `design_tests` in `purecoder/execute.py` to accept and forward it:

```python
def design_tests(pc, description, targets=None, max_retries=3, verbose=True,
                 n_predict=512, min_assertions=MIN_ASSERTIONS):
```

and inside the loop:

```python
        ok, reason = lint_tests(tests, targets=targets,
                                min_assertions=min_assertions)
```

- [ ] **Step 5: Wire the contract into the loop**

Add to the imports at the top of `purecoder/execute.py`:

```python
from .anchors import anchor_tests
from .contract import derive_contract, render_contract
```

Replace `generate_validated_python` with:

```python
def generate_validated_python(pc, description, tests=None, contract=None,
                              use_contract=False, max_retries=3,
                              timeout=10, verbose=True, **kw):
    """Generate code, run it against (code-blind) tests, retry on failure
    with the traceback fed back.

    With use_contract, the prose is first turned into a contract that both the
    writer and the test designer read, and whose examples become mechanical
    anchor assertions. Returns {ok, text, tests, anchors, contract, attempts,
    error}.
    """
    if use_contract and contract is None:
        contract, cerr = derive_contract(pc, description,
                                         max_retries=max_retries,
                                         verbose=verbose)
        if contract is None and verbose:
            print(f"[contract] {cerr} -> continuing without one")

    anchors = ""
    if contract is not None:
        anchors, dropped = anchor_tests(contract)
        if verbose:
            for reason in dropped:
                print(f"[contract] dropped {reason}")

    # Everything downstream reads the contract, never the implementation --
    # the test designer stays code-blind.
    spec = description
    if contract is not None:
        spec = f"{description}\n\n{render_contract(contract)}"

    if tests is None:
        # Anchors already assert everything the contract states, so the
        # designer is asked for what they do NOT cover, and judged on that
        # alone. Counting free anchors toward the floor would let a lazy
        # tester through on tests it did not write.
        floor = 1 if anchors else MIN_ASSERTIONS
        ask = spec
        if anchors:
            ask = (f"{spec}\n\nThese cases are already covered and must NOT be "
                   f"repeated:\n{anchors}\n\nWrite only ADDITIONAL tests.")
        designed, _, _ = design_tests(pc, ask, max_retries=max_retries,
                                      verbose=verbose, min_assertions=floor)
    else:
        designed = tests

    def _assemble(designed_src):
        return f"{anchors}\n\n{designed_src}".strip() if anchors else designed_src

    task = spec
    code, error = "", ""
    regated = False          # the target-name check runs once, after code exists

    for attempt in range(1, max_retries + 1):
        res = pc.code(task, language="python", **kw)
        code = res["text"]

        if res["truncated"]:
            error = "output was cut off (hit n_predict)"
            task = f"{spec}\n\nPrevious output was cut off. Be complete but concise."
            if verbose:
                print(f"[attempt {attempt}] truncated -> retrying")
            continue

        # Now that an implementation exists, the gate can also check the tests
        # actually call it -- the one check that needs a name from the code.
        if not regated:
            regated = True
            targets = public_names(code)
            if targets:
                floor = 1 if anchors else MIN_ASSERTIONS
                gate_ok, gate_reason = lint_tests(designed, targets=targets,
                                                  min_assertions=floor)
                if not gate_ok:
                    if verbose:
                        print(f"[tests] post-code gate: {gate_reason} -> redesigning")
                    designed, _, _ = design_tests(pc, spec, targets=targets,
                                                  max_retries=max_retries,
                                                  verbose=verbose,
                                                  min_assertions=floor)

        full = _assemble(designed)
        ok, error = run_python(code, full, timeout=timeout)
        if ok:
            if verbose:
                print(f"[attempt {attempt}] all tests passed")
            return {"ok": True, "text": code, "tests": full, "anchors": anchors,
                    "contract": contract, "attempts": attempt, "error": ""}

        if verbose:
            first = error.splitlines()[-1] if error else "unknown"
            print(f"[attempt {attempt}] tests failed: {first} -> retrying")
        task = (f"{spec}\n\n"
                f"Your previous implementation failed these tests:\n{full}\n\n"
                f"With this error:\n{error}\n\n"
                f"Output only the corrected code, nothing else.")

    return {"ok": False, "text": code, "tests": _assemble(designed),
            "anchors": anchors, "contract": contract,
            "attempts": max_retries, "error": error}
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_loops.py tests/test_execute.py -q`
Expected: PASS.

- [ ] **Step 7: Verify the full suite and lint**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check purecoder tests examples`
Expected: 106 passed, `All checks passed!`

If `test_execution_loop_designs_tests_when_none_are_given` or
`test_execution_loop_redesigns_tests_rejected_by_the_gate` now fail on the
`res["tests"]` comparison, that is a real regression: with no contract,
`anchors` is `""` and `_assemble` must return the designed source unchanged.
Fix `_assemble`, not the test.

- [ ] **Step 8: Commit**

```bash
git add purecoder/execute.py tests/test_loops.py tests/test_execute.py
git commit -m "feat(execute): ground generation in a contract and anchor its tests"
```

---

### Task 5: Scaffolder and CLI

Exposes the layer: on by default for `project`, opt-in for `code`, with an env override.

**Files:**
- Modify: `purecoder/scaffold.py:26-45`
- Modify: `purecoder/cli.py:19-24` (imports), `:27-41` (`_print_result`, `cmd_code`), `:54-59` (`cmd_project`), `:70-81` (`cmd_ask`), `:89-99` (argparse)
- Modify: `tests/test_loops.py` (append)
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `generate_validated_python(..., use_contract=...)` (Task 4), `render_contract` (Task 3).
- Produces: `scaffold_project(pc, name, description, outdir="build", entry="main.py", max_retries=5, verbose=True, use_contract=True)`; `purecoder.cli.resolve_contract(args, default: bool) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli.py`:

```python
"""Flag resolution: explicit flag beats env var beats per-command default."""

import pytest

from purecoder.cli import resolve_contract


class Args:
    def __init__(self, contract=None):
        self.contract = contract


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("PURECODER_CONTRACT", raising=False)


def test_default_is_used_when_nothing_is_set():
    assert resolve_contract(Args(), default=True) is True
    assert resolve_contract(Args(), default=False) is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_env_var_can_turn_it_on(monkeypatch, value):
    monkeypatch.setenv("PURECODER_CONTRACT", value)
    assert resolve_contract(Args(), default=False) is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_env_var_can_turn_it_off(monkeypatch, value):
    monkeypatch.setenv("PURECODER_CONTRACT", value)
    assert resolve_contract(Args(), default=True) is False


def test_explicit_flag_beats_the_env_var(monkeypatch):
    monkeypatch.setenv("PURECODER_CONTRACT", "0")
    assert resolve_contract(Args(contract=True), default=False) is True
    monkeypatch.setenv("PURECODER_CONTRACT", "1")
    assert resolve_contract(Args(contract=False), default=True) is False


def test_explicit_flag_beats_the_default():
    assert resolve_contract(Args(contract=True), default=False) is True
    assert resolve_contract(Args(contract=False), default=True) is False
```

Append to `tests/test_loops.py`:

```python
def test_scaffold_grounds_the_code_artifact_in_a_contract(tmp_path):
    pc = FakeModel(
        code_outputs=[GOOD_CODE],
        completions=[json.dumps(CONTRACT), GOOD_TESTS, MAKEFILE,
                     "KEY=value\n", "# readme\n"],
    )
    out = tmp_path / "proj"
    res = scaffold_project(pc, "proj", "a project", outdir=str(out),
                           verbose=False)
    assert res["ok"]
    assert any("Contract for" in p for p in pc.prompts)


def test_scaffold_can_run_without_a_contract(tmp_path):
    pc = FakeModel(
        code_outputs=[GOOD_CODE],
        completions=[GOOD_TESTS, MAKEFILE, "KEY=value\n", "# readme\n"],
    )
    res = scaffold_project(pc, "proj", "a project", outdir=str(tmp_path / "p"),
                           use_contract=False, verbose=False)
    assert res["ok"]
    assert not any("Contract for" in p for p in pc.prompts)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py tests/test_loops.py -q`
Expected: FAIL — `ImportError: cannot import name 'resolve_contract'`

- [ ] **Step 3: Add the scaffolder parameter**

In `purecoder/scaffold.py`, change the signature and the code call:

```python
def scaffold_project(pc, name, description, outdir="build",
                     entry="main.py", max_retries=5, verbose=True,
                     use_contract=True):
```

and:

```python
    code_res = generate_validated_python(
        pc,
        f"{description}\n\nThis is the main module `{entry}`.",
        use_contract=use_contract,
        max_retries=max_retries, verbose=verbose,
    )
```

Add to the module docstring, after the existing "Order matters" paragraph:

```
Only the code artifact is contract-grounded. The Makefile, .env and README
are config and prose -- their validators already cover what a contract would
add, and a fifth model call per artifact is not worth it on a tight card.
```

- [ ] **Step 4: Add flag resolution and display to the CLI**

In `purecoder/cli.py`, add `import os` to the imports, then add above
`_print_result`:

```python
def resolve_contract(args, default):
    """Most specific wins: explicit flag, then PURECODER_CONTRACT, then the
    per-command default."""
    if args.contract is not None:
        return args.contract
    env = os.environ.get("PURECODER_CONTRACT")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    return default
```

Extend `_print_result` so the assumed interpretation is visible:

```python
def _print_result(res, show_tests=False):
    contract = res.get("contract")
    if contract:
        from .contract import render_contract
        print(render_contract(contract))
        n = len([ln for ln in res.get("anchors", "").splitlines()
                 if ln.startswith(("assert", "try:"))])
        print(f"[{n} anchor assertion(s) generated mechanically]")
    print("-" * 60)
    print(res["text"])
    print("-" * 60)
    ok = res.get("ok")
    print(f"ok={ok}  attempts={res.get('attempts')}")
    if show_tests and res.get("tests"):
        print("\n[tests used]\n" + res["tests"])
    if not ok and res.get("error"):
        print(f"error: {res['error']}")
```

Update the three call sites:

```python
def cmd_code(pc, args):
    _print_result(generate_validated_python(
        pc, args.spec, max_retries=args.retries,
        use_contract=resolve_contract(args, default=False)),
        show_tests=args.show_tests)
```

```python
def cmd_project(pc, args):
    from .scaffold import scaffold_project
    r = scaffold_project(pc, args.name, args.spec,
                         outdir=args.outdir or args.name,
                         max_retries=args.retries,
                         use_contract=resolve_contract(args, default=True))
    print(f"\nscaffold {'complete' if r['ok'] else 'incomplete'} -> {r['outdir']}/")
```

In `cmd_ask`, change the final call to:

```python
    _print_result(generate_validated_python(
        pc, task, max_retries=args.retries,
        use_contract=resolve_contract(args, default=False)),
        show_tests=args.show_tests)
```

Register the flags in `main()`, after the `--show-tests` line:

```python
    p.add_argument("--contract", dest="contract", action="store_true",
                   default=None,
                   help="derive a spec contract first (default: on for project)")
    p.add_argument("--no-contract", dest="contract", action="store_false",
                   help="skip contract derivation")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli.py tests/test_loops.py -q`
Expected: PASS.

- [ ] **Step 6: Verify the full suite, lint, and the real CLI**

Run:
```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check purecoder tests examples
.venv/bin/purecoder --help | grep -A1 contract
```
Expected: 121 passed (test_cli.py contributes 13 -- two of its cases are parametrized five ways), `All checks passed!`, and both flags listed in help.

- [ ] **Step 7: Commit**

```bash
git add purecoder/scaffold.py purecoder/cli.py tests/test_cli.py tests/test_loops.py
git commit -m "feat(cli): --contract/--no-contract with PURECODER_CONTRACT override"
```

---

### Task 6: Exports and documentation

Makes the layer reachable from the package root and brings the docs back in line with the code — the discipline the reorganisation established.

**Files:**
- Modify: `purecoder/__init__.py`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/STATUS.md`
- Modify: `README.md`
- Modify: `tests/test_contract.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: `purecoder.derive_contract`, `purecoder.anchor_tests`, `purecoder.render_contract`, `purecoder.validate_contract`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_contract.py`:

```python
def test_contract_helpers_are_exported_from_the_package():
    import purecoder

    for name in ("derive_contract", "anchor_tests", "render_contract",
                 "validate_contract"):
        assert name in purecoder.__all__
        assert hasattr(purecoder, name)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_contract.py -q -k exported`
Expected: FAIL — `assert 'derive_contract' in [...]`

- [ ] **Step 3: Add the exports**

In `purecoder/__init__.py`, add the import (ruff's isort will order it):

```python
from .anchors import anchor_tests
from .contract import derive_contract, render_contract, validate_contract
```

and add the four names to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_contract.py -q -k exported`
Expected: PASS

- [ ] **Step 5: Document the layer in ARCHITECTURE.md**

Insert a new section between "### 2. Config validation" and
"### 3. Execution validation":

```markdown
### 2.5 Spec contracts (`purecoder/contract.py`)
Prose is ambiguous; a contract is not. Before any code exists, the description
is turned into a grammar-constrained JSON contract — name, params, returns,
error cases, examples — which both the writer and the test designer read.

**The failure this exists for:** every other layer catches the model being
wrong about *how* to write something. None catch it being wrong about *what*.
When the writer and the code-blind tester misread the same ambiguous spec the
same way, the tests agree with the bug and the loop reports success. The
checked-in `examples/portcheck/` output shows it: "raising ValueError on
out-of-range" produced code that silently skipped them, and tests that agreed.

A contract does two things about that. It makes the shared interpretation a
single artifact the user can read in seconds instead of an invisible agreement
between two generated files. And it lets the explicitly-stated behaviour become
assertions **generated by code, not by a model** — the examples compile
mechanically into anchor tests, so the assertions encoding the spec are no
longer the model's opinion.

**Boundary, stated plainly:** this does not make the contract correct. A
confidently wrong contract yields confidently wrong anchors. It makes the
wrongness *visible*, which is a weaker and more honest claim.

**Design line:** the gate judges only the tests the designer wrote, never the
anchors. Free assertions must not satisfy the floor on a lazy tester's behalf —
the same reasoning that put semantic guards on top of `make -n`.

Anchors run in the same sandboxed subprocess that already executes
model-written code, so this adds no new trust boundary.
```

Then add to the `## Next` list:

```markdown
- Semantic guard for `.env` (a single rambling comment is structurally valid)
```

- [ ] **Step 6: Update STATUS.md**

Add these rows to the "Done and tested" table, after the `lint_tests` row:

```markdown
| 5 | `contract.gbnf` + `validate_contract` | ✅ tested | schema guards past the grammar |
| 5 | `anchors.py` (mechanical assertions) | ✅ tested | both shapes, malformed and custom-exception drops |
| 5 | `derive_contract` + fallback | ✅ tested | retries, feeds errors back, degrades on a dead server |
```

Update the test count sentence above the table to the real number from
`pytest -q`. Then replace the first "Next steps" item with:

```markdown
1. **Semantic guard for `.env`** — the one validator that still rubber-stamps
   degenerate output; `examples/portcheck/.env` is a live example.
```

and add, as the new final item:

```markdown
5. **Measure the contract layer** — does grounding actually reduce
   spec-divergence, or only make it visible? Needs a small task set with
   known-ambiguous specs.
```

- [ ] **Step 7: Update README.md**

In the "Why it's interesting" list, insert before the **Retrieval** bullet:

```markdown
- **Spec contracts** turn prose into a structured contract the writer and
  tester both read, and compile its examples into assertions **no model
  wrote** — so a misread spec is visible instead of silently agreed on.
```

In the Layout block, add the module line after `client.py`:

```
  contract.py    prose → grammar-constrained spec contract
  anchors.py     contract examples → assertions no model wrote
```

Add to the Commands table note, directly under the table:

```markdown
`project` derives a spec contract by default; `code` does not. Add
`--contract` to opt in, `--no-contract` to opt out, or set
`PURECODER_CONTRACT=1` to change the default for both.
```

- [ ] **Step 8: Verify everything**

Run:
```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check purecoder tests examples
.venv/bin/python -c "import purecoder; print(sorted(purecoder.__all__))"
```
Expected: 122 passed, `All checks passed!`, and the four contract names listed.

- [ ] **Step 9: Commit**

```bash
git add purecoder/__init__.py docs/ARCHITECTURE.md docs/STATUS.md README.md tests/test_contract.py
git commit -m "docs: document the contract layer and its boundary"
```

---

## Manual verification (after Task 6)

The suite is model-independent by design, so one live run is worth doing before
calling this done. With `llama-server` up:

```bash
.venv/bin/purecoder --contract code "a function parse_ports(s) returning a sorted list of unique valid ports (1-65535), raising ValueError on bad input"
```

Check, in order:

1. A contract block prints before the code.
2. Its `raises` mentions out-of-range, not only non-numeric — this is the exact
   clause the un-grounded run got wrong.
3. The anchor count is non-zero.
4. The generated `parse_ports` raises on `'99999'` rather than skipping it.

Point 4 is the whole reason the layer exists. If the contract is right and the
code still diverges, that is a finding worth writing down in `docs/STATUS.md`,
not a bug to paper over.
