# Language Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `purecoder learn <lang> <docs_dir> --ext .x` drafts a `LanguageSpec` from a language's own documentation, proves it with six probes, and registers it — or refuses, naming the probe that failed.

**Architecture:** Two new modules. `langstore.py` serialises a `LanguageSpec` to JSON under a user data dir and loads saved entries into `REGISTRY` at import. `bootstrap.py` drafts the harness field-by-field (grounded in retrieved docs *and* the existing C++/JS/Rust entries as worked examples), then gates the candidate on five mechanical probes plus one live generation round. Nothing model-authored reaches `subprocess.Popen` without explicit confirmation.

**Tech Stack:** Python 3.10+, stdlib only in the new modules (`json`, `shlex`, `dataclasses`, `pathlib`, `re`). Retrieval reuses `rag.DocStore`/`Embedder` (optional `[rag]` extra). Tests use `pytest` and the existing `FakeModel`.

Design spec: `docs/superpowers/specs/2026-08-03-language-bootstrap-design.md`.

## Global Constraints

- **A bootstrapped language is a candidate until the probes pass.** Never register an unproven spec. The project rule — *if it cannot be executed, it is not emitted* — is not weakened by this feature.
- **Model-authored argv requires explicit confirmation before its first execution.** Confirmation is recorded; it is re-asked whenever the commands change.
- **A bootstrapped entry may never shadow a built-in one.** `python`, `c++`, `javascript`, `rust`, `c#`, `go`, `java`, `swift`, `ocaml`, `powerquery` are reserved.
- **Drafting prompts carry worked examples, never prose rules.** Measured: prose translation rules perform *below baseline* on some models. Examples come from `languages.get("c++")` and friends.
- **`test_system` is templated, not drafted.** The tester prompt is PureCoder's discipline, not the model's to invent.
- **No literal renderers, ever.** That was the anchors layer; it produced five Critical false greens and was deleted.
- Every task ends green: `.venv/bin/python -m pytest -q` and `.venv/bin/ruff check purecoder tests examples`.
- Commit messages: no Claude co-author, no Anthropic trailer, scoped by functionality.
- Tests must pass with no GPU, no llama-server, and no toolchain beyond what is present (skip when absent).

---

## File Structure

| file | responsibility |
|---|---|
| `purecoder/langstore.py` | **new** — `LanguageSpec` ⇄ JSON, the store directory, loading saved entries into `REGISTRY` |
| `purecoder/bootstrap.py` | **new** — drafting, the probe suite, the `learn_language` orchestrator |
| `purecoder/languages.py` | **modify** — export `BUILTIN_NAMES` snapshot |
| `purecoder/__init__.py` | **modify** — load saved languages at import |
| `purecoder/cli.py` | **modify** — the `learn` subcommand |
| `tests/test_langstore.py` | **new** — round-trip, shadow guard, corrupt-file tolerance |
| `tests/test_bootstrap.py` | **new** — probes against real toolchains, drafting against `FakeModel` |
| `README.md`, `docs/ARCHITECTURE.md`, `docs/STATUS.md` | **modify** — document the layer and its boundaries |

---

### Task 1: Serialise a LanguageSpec, and reserve the built-in names

**Files:**
- Create: `purecoder/langstore.py`
- Modify: `purecoder/languages.py` (append after the powerquery entry)
- Test: `tests/test_langstore.py`

**Interfaces:**
- Consumes: `languages.LanguageSpec`, `languages.ProjectSpec`, `languages.REGISTRY`
- Produces: `langstore.to_json(spec, **provenance) -> dict`, `langstore.from_json(data) -> LanguageSpec`, `languages.BUILTIN_NAMES: frozenset[str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_langstore.py
"""Persisting a bootstrapped language: it must survive a round trip exactly,
and it must never be able to impersonate a built-in entry."""

import pytest

from purecoder import langstore
from purecoder.languages import BUILTIN_NAMES, PYTHON, LanguageSpec, ProjectSpec


def test_a_spec_survives_a_round_trip_unchanged():
    assert langstore.from_json(langstore.to_json(PYTHON)) == PYTHON


def test_tuple_fields_come_back_as_tuples_not_lists():
    """JSON has no tuple. A LanguageSpec is frozen and compared by value, so a
    list where a tuple belongs makes every equality check quietly false."""
    spec = langstore.from_json(langstore.to_json(PYTHON))
    assert isinstance(spec.run, tuple)
    assert isinstance(spec.aliases, tuple)


def test_a_spec_with_no_project_round_trips():
    bare = LanguageSpec(name="bare", extension=".b")
    assert langstore.from_json(langstore.to_json(bare)) == bare


def test_provenance_is_recorded_and_does_not_break_the_round_trip():
    data = langstore.to_json(PYTHON, docs_dir="/docs", learned="2026-08-03")
    assert data["bootstrapped"] is True
    assert data["docs_dir"] == "/docs"
    assert langstore.from_json(data) == PYTHON


def test_the_built_in_names_are_reserved():
    assert {"python", "c++", "rust", "powerquery"} <= BUILTIN_NAMES


def test_a_project_spec_round_trips_field_for_field():
    spec = LanguageSpec(name="x", extension=".x",
                        project=ProjectSpec(entry="m.x", install="i", run="r",
                                            test="t", entry_stub="STUB"))
    assert langstore.from_json(langstore.to_json(spec)).project.entry_stub == "STUB"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_langstore.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'purecoder.langstore'`

- [ ] **Step 3: Write the minimal implementation**

Append to `purecoder/languages.py`, at the very end of the file:

```python
# Snapshot taken before any bootstrapped entry can be loaded. A drafted spec
# may never shadow one of these: the hand-written entries are the reference,
# and silently overriding `python` with an approximation has no upside.
BUILTIN_NAMES = frozenset(REGISTRY)
```

Create `purecoder/langstore.py`:

```python
"""
purecoder/langstore.py

Where a bootstrapped language lives between runs.

A hand-written entry is code. A drafted one is data, so it is stored as data:
one JSON file per language under a user data dir, loaded into the registry at
import. That keeps "adding a language is data, not code" literally true, and
makes a bad entry removable with `rm`.
"""

import dataclasses
import json
import os
from pathlib import Path

from .languages import BUILTIN_NAMES, LanguageSpec, ProjectSpec, register

# JSON has no tuple, and a LanguageSpec is frozen and compared by value -- a
# list where a tuple belongs makes every equality check quietly false.
_TUPLE_FIELDS = ("probe", "build", "run", "aliases")
_FIELDS = tuple(f.name for f in dataclasses.fields(LanguageSpec))


def store_dir() -> Path:
    """Where saved languages live. PURECODER_HOME wins, then XDG."""
    root = os.environ.get("PURECODER_HOME")
    if root:
        return Path(root) / "languages"
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / "purecoder" / "languages"


def to_json(spec: LanguageSpec, **provenance) -> dict:
    """A spec plus where it came from. Provenance is not part of the spec, so
    the CLI can say 'drafted from ./zig-docs on 2026-08-03' rather than
    presenting a candidate as though a human had written it."""
    data = {f: getattr(spec, f) for f in _FIELDS}
    for f in _TUPLE_FIELDS:
        data[f] = list(data[f])
    data["project"] = dataclasses.asdict(spec.project) if spec.project else None
    data["bootstrapped"] = True
    data.update(provenance)
    return data


def from_json(data: dict) -> LanguageSpec:
    """Rebuild a spec, ignoring provenance keys it does not declare."""
    fields = {f: data[f] for f in _FIELDS if f in data}
    for f in _TUPLE_FIELDS:
        if f in fields:
            fields[f] = tuple(fields[f])
    if fields.get("project"):
        fields["project"] = ProjectSpec(**fields["project"])
    return LanguageSpec(**fields)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_langstore.py -q && .venv/bin/ruff check purecoder tests`
Expected: PASS, ruff clean

- [ ] **Step 5: Commit**

```bash
git add purecoder/langstore.py purecoder/languages.py tests/test_langstore.py
git commit -m "feat(langstore): serialise a language spec, and reserve the built-in names

JSON has no tuple and LanguageSpec is frozen, so the tuple fields are restored
explicitly -- a list where a tuple belongs makes every equality check quietly
false. BUILTIN_NAMES is snapshotted before any bootstrapped entry can load, so
a drafted spec can never shadow a hand-written one."
```

---

### Task 2: Save and load bootstrapped languages

**Files:**
- Modify: `purecoder/langstore.py`
- Modify: `purecoder/__init__.py`
- Test: `tests/test_langstore.py`

**Interfaces:**
- Consumes: `langstore.to_json`, `langstore.from_json`, `langstore.store_dir`
- Produces: `langstore.save(spec, **provenance) -> Path`, `langstore.load_all() -> list[LanguageSpec]`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_langstore.py`:

```python
# ---- saving and loading --------------------------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PURECODER_HOME", str(tmp_path))
    return tmp_path / "languages"


CANDIDATE = LanguageSpec(
    name="zig", extension=".zig", probe=("zig", "version"),
    build=("zig", "build-exe", "{src}"), run=("{bin}",),
    preamble="PRE", epilogue="POST", test_system="assert with PC_CHECK",
    check_call="PC_CHECK",
)


def test_saving_then_loading_restores_the_spec(store, monkeypatch):
    from purecoder.languages import REGISTRY

    monkeypatch.delitem(REGISTRY, "zig", raising=False)
    langstore.save(CANDIDATE, docs_dir="/docs")
    assert (store / "zig.json").is_file()

    monkeypatch.delitem(REGISTRY, "zig", raising=False)
    loaded = langstore.load_all()
    assert [s.name for s in loaded] == ["zig"]
    assert REGISTRY["zig"] == CANDIDATE


def test_saving_a_built_in_name_is_refused(store):
    with pytest.raises(ValueError, match="built-in"):
        langstore.save(dataclasses_replace_name(PYTHON, "python"))


def dataclasses_replace_name(spec, name):
    import dataclasses as dc
    return dc.replace(spec, name=name)


def test_a_file_claiming_a_built_in_name_is_ignored(store):
    """The guard has to hold at load time too -- the file is editable by hand
    and by anything else on the machine."""
    import json as _json

    from purecoder.languages import REGISTRY

    store.mkdir(parents=True, exist_ok=True)
    (store / "python.json").write_text(_json.dumps(
        {"name": "python", "extension": ".fake", "run": ["echo"],
         "test_system": "x", "probe": [], "build": [], "aliases": []}))
    langstore.load_all()
    assert REGISTRY["python"].extension == ".py", "a built-in was overwritten"


def test_a_corrupt_file_is_skipped_not_fatal(store):
    store.mkdir(parents=True, exist_ok=True)
    (store / "broken.json").write_text("{not json")
    (store / "zig.json").write_text(__import__("json").dumps(
        langstore.to_json(CANDIDATE)))
    assert [s.name for s in langstore.load_all()] == ["zig"]


def test_loading_an_empty_store_is_not_an_error(store):
    assert langstore.load_all() == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_langstore.py -q`
Expected: FAIL — `AttributeError: module 'purecoder.langstore' has no attribute 'save'`

- [ ] **Step 3: Write the minimal implementation**

Append to `purecoder/langstore.py`:

```python
def save(spec: LanguageSpec, **provenance) -> Path:
    """Write one language to the store. -> the path written."""
    if spec.name in BUILTIN_NAMES:
        raise ValueError(f"{spec.name!r} is a built-in language -- a drafted "
                         f"spec may not replace a hand-written one")
    directory = store_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{spec.name}.json"
    path.write_text(json.dumps(to_json(spec, **provenance), indent=2) + "\n")
    return path


def load_all() -> list:
    """Register every saved language. -> those loaded.

    A bad file must never stop the CLI from starting: this runs at import, so
    a truncated write or a hand-edit that lost a brace would otherwise make
    `purecoder status` unusable rather than merely losing one language.
    """
    directory = store_dir()
    if not directory.is_dir():
        return []

    loaded = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or data.get("name") in BUILTIN_NAMES:
            continue
        try:
            spec = from_json(data)
        except (TypeError, KeyError, ValueError):
            continue
        register(spec)
        loaded.append(spec)
    return loaded
```

Modify `purecoder/__init__.py` — add after the existing imports, before `__version__`:

```python
from .langstore import load_all as _load_bootstrapped_languages

# Saved languages join the registry at import, so `--lang zig` works in the CLI
# and in library use alike. Failure here must never stop the package loading:
# a language is a feature, and the rest of the pipeline does not depend on it.
try:
    _load_bootstrapped_languages()
except Exception:                                     # noqa: BLE001
    pass
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_langstore.py -q && .venv/bin/ruff check purecoder tests`
Expected: PASS, ruff clean

- [ ] **Step 5: Commit**

```bash
git add purecoder/langstore.py purecoder/__init__.py tests/test_langstore.py
git commit -m "feat(langstore): load saved languages into the registry at import

The shadow guard holds at save time and again at load time -- the file is
editable by hand, so checking only on the way in would be checking the wrong
end. A corrupt file is skipped rather than fatal: this runs at import, and one
lost brace should cost one language, not the whole CLI."
```

---

### Task 3: The probe suite — what proves a drafted spec is real

**Files:**
- Create: `purecoder/bootstrap.py`
- Test: `tests/test_bootstrap.py`

**Interfaces:**
- Consumes: `execute.run_candidate`, `languages.LanguageSpec`
- Produces: `bootstrap.Fixture(correct, wrong, tests, empty, always_fails)`, `bootstrap.probe_language(spec, fixture, timeout=60) -> (ok: bool, results: list[Probe])`, `bootstrap.Probe(name, ok, detail)`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bootstrap.py
"""The bootstrap gate.

A drafted LanguageSpec is a claim. These are the probes that turn it into a
fact -- run against a real toolchain, because a harness that compiles is not a
harness that can fail wrong code, and that difference is the whole point.
"""

import dataclasses

import pytest

from purecoder import bootstrap, languages as L

# The C++ entry is hand-written and known good, so it is the reference the
# probes themselves are tested against.
CPP_FIXTURE = bootstrap.Fixture(
    correct="int add(int a,int b){return a+b;}",
    wrong="int add(int a,int b){return a-b;}",
    tests=("int add(int,int);\nvoid pc_tests(){ PC_CHECK(add(1,2)==3); "
           "PC_CHECK(add(0,0)==0); PC_CHECK(add(-1,1)==0); }"),
    empty="void pc_tests(){ }",
    always_fails="void pc_tests(){ PC_CHECK(1==2); }",
)


def _cpp():
    spec = L.get("c++")
    ok, why = spec.available()
    if not ok:
        pytest.skip(why)
    return spec


def test_a_known_good_language_passes_every_probe():
    ok, results = bootstrap.probe_language(_cpp(), CPP_FIXTURE)
    assert ok, [r for r in results if not r.ok]
    assert len(results) == 5


def test_a_harness_that_cannot_fail_is_rejected():
    """The probe that matters. A check helper that prints on failure but exits
    0 passes 'it compiles', 'it runs' and 'it reports' -- and is worthless.
    This is the false-green class the project keeps rediscovering."""
    spec = dataclasses.replace(_cpp(), preamble=(
        "#include <cstdio>\n"
        "static int pc_checks = 0;\n"
        "#define PC_CHECK(x) do { pc_checks++; } while (0)\n"))
    ok, results = bootstrap.probe_language(spec, CPP_FIXTURE)
    assert not ok
    failed = [r.name for r in results if not r.ok]
    assert "wrong implementation fails" in failed
    assert "a failing check fails the run" in failed


def test_a_harness_that_cannot_count_is_rejected():
    """No 'no checks ran' tail: an empty suite exits 0 and reports success."""
    spec = dataclasses.replace(_cpp(), epilogue="int main() { pc_tests(); return 0; }\n")
    ok, results = bootstrap.probe_language(spec, CPP_FIXTURE)
    assert not ok
    assert "a suite with no checks fails" in [r.name for r in results if not r.ok]


def test_a_broken_implementation_must_produce_an_error_message():
    ok, results = bootstrap.probe_language(_cpp(), CPP_FIXTURE)
    probe = next(r for r in results if "broken" in r.name)
    assert probe.ok and probe.detail.strip(), "no diagnostic to feed the fix loop"


def test_every_probe_carries_a_human_readable_name():
    _, results = bootstrap.probe_language(_cpp(), CPP_FIXTURE)
    assert all(r.name and " " in r.name for r in results)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_bootstrap.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'purecoder.bootstrap'`

- [ ] **Step 3: Write the minimal implementation**

Create `purecoder/bootstrap.py`:

```python
"""
purecoder/bootstrap.py

Drafting a language entry from the language's own documentation, and -- the
part that matters -- proving it before anything is registered.

A drafted LanguageSpec is a claim about a language nobody here has written by
hand. The probes below turn it into a fact. They are the same bar the
hand-written entries meet, run mechanically against a trivial `add(a, b)`: a
harness that cannot fail a wrong implementation is not a language entry, it is
a rubber stamp, and it would make every later run report success.
"""

from dataclasses import dataclass

from .execute import run_candidate

# Appended to a correct implementation to produce one that cannot possibly
# build or parse, in any language. Deliberately not a language-specific
# mistake: this probe asks whether an error reaches the fix loop at all.
SYNTAX_GARBAGE = "\n@@@ purecoder syntax probe @@@\n"


@dataclass(frozen=True)
class Fixture:
    """Five snippets in the target language, drafted alongside the harness.

    `empty` is not the empty string: a language whose epilogue calls
    `pc_tests()` needs that function to exist and do nothing, or the probe
    measures a compile error instead of a check that never ran.
    """

    correct: str
    wrong: str
    tests: str
    empty: str
    always_fails: str


@dataclass(frozen=True)
class Probe:
    name: str
    ok: bool
    detail: str


def probe_language(spec, fixture: Fixture, timeout: int = 60):
    """Run every mechanical probe. -> (all passed, [Probe, ...]).

    No probe trusts an exit code alone, because that is the mistake this whole
    project exists to catch.
    """
    results = []

    ok, err = run_candidate(spec, fixture.correct, fixture.tests,
                            timeout=timeout, require_checks=1)
    results.append(Probe("correct implementation passes", ok, err))

    ok, err = run_candidate(spec, fixture.wrong, fixture.tests,
                            timeout=timeout, require_checks=1)
    results.append(Probe("wrong implementation fails", not ok, err))

    ok, err = run_candidate(spec, fixture.correct, fixture.empty,
                            timeout=timeout, require_checks=1)
    results.append(Probe("a suite with no checks fails",
                         not ok and "no checks ran" in err, err))

    ok, err = run_candidate(spec, fixture.correct + SYNTAX_GARBAGE,
                            fixture.tests, timeout=timeout)
    results.append(Probe("a broken implementation reports its error",
                         not ok and bool(err.strip()), err))

    ok, err = run_candidate(spec, fixture.correct, fixture.always_fails,
                            timeout=timeout, require_checks=1)
    results.append(Probe("a failing check fails the run", not ok, err))

    return all(p.ok for p in results), results
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_bootstrap.py -q && .venv/bin/ruff check purecoder tests`
Expected: PASS (or skip if `g++` is absent), ruff clean

- [ ] **Step 5: Commit**

```bash
git add purecoder/bootstrap.py tests/test_bootstrap.py
git commit -m "feat(bootstrap): probe a drafted language before believing it

Five probes on a trivial add(a,b), run against a real toolchain. Two of them
are the reason the module exists: a check helper that prints on failure but
exits 0, and an epilogue with no 'no checks ran' tail, both compile clean and
both report success on wrong code. The tests build exactly those two broken
harnesses out of the known-good C++ entry and require the gate to reject them."
```

---

### Task 4: Draft the harness from docs and worked examples

**Files:**
- Modify: `purecoder/bootstrap.py`
- Test: `tests/test_bootstrap.py`

**Interfaces:**
- Consumes: `client.PureCoder.complete`, `languages.get`, `bootstrap.Fixture`
- Produces: `bootstrap.worked_examples(field, names=("c++", "javascript", "rust")) -> str`, `bootstrap.draft_preamble(pc, name, context) -> str`, `bootstrap.draft_check_call(pc, name, preamble) -> str`, `bootstrap.draft_epilogue(pc, name, preamble, context) -> str`, `bootstrap.draft_fixture(pc, name, preamble, check_call) -> Fixture`, `bootstrap.test_system_for(name, check_call) -> str`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bootstrap.py`:

```python
# ---- drafting ------------------------------------------------------------

class Scripted:
    """Returns queued completions in order and records the prompts it saw."""

    def __init__(self, *completions):
        self.queue = list(completions)
        self.prompts = []

    def complete(self, system, user, grammar=None, **kw):
        self.prompts.append((system, user))
        text = self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]
        return {"text": text, "truncated": False, "tokens": 1, "raw": {}}


def test_worked_examples_carry_real_entries_not_descriptions():
    """Measured result: prose translation rules score BELOW baseline on some
    models, while translation examples never hurt. The prompt must show the
    C++ and JavaScript harnesses, not describe them."""
    text = bootstrap.worked_examples("preamble")
    assert "PC_CHECK" in text
    assert "#include <cstdio>" in text          # the real C++ entry
    assert "process.exit(1)" in text            # the real JavaScript entry


def test_the_preamble_prompt_shows_examples_and_the_retrieved_docs():
    pc = Scripted("HELPER CODE")
    out = bootstrap.draft_preamble(pc, "zig", "DOCS ABOUT ZIG")
    assert out == "HELPER CODE"
    _system, user = pc.prompts[0]
    assert "DOCS ABOUT ZIG" in user
    assert "#include <cstdio>" in user, "no worked example in the prompt"


def test_the_check_call_is_extracted_from_a_single_line_answer():
    """Rust's invocation is `pc_check!` while its definition reads
    `macro_rules! pc_check {`, so the form has to be observed, not assumed."""
    assert bootstrap.draft_check_call(Scripted("PC_CHECK(1 == 1);"),
                                      "zig", "PRE PC_CHECK") == "PC_CHECK"
    assert bootstrap.draft_check_call(Scripted("pc_check!(1 == 1);"),
                                      "rs", "PRE pc_check") == "pc_check!"


def test_a_check_call_the_preamble_never_defines_is_refused():
    """The gate counts this token textually. If the preamble does not define
    it, every suite scores zero checks and the language silently fails."""
    with pytest.raises(ValueError, match="never defines"):
        bootstrap.draft_check_call(Scripted("VERIFY(1 == 1);"), "zig", "PRE")


def test_the_fixture_comes_back_as_five_labelled_snippets():
    pc = Scripted("CORRECT\n@@WRONG@@\nWRONG\n@@TESTS@@\nTESTS\n"
                  "@@EMPTY@@\nEMPTY\n@@ALWAYS_FAILS@@\nFAILS")
    fx = bootstrap.draft_fixture(pc, "zig", "PRE", "PC_CHECK")
    assert fx.correct == "CORRECT"
    assert fx.wrong == "WRONG"
    assert fx.tests == "TESTS"
    assert fx.empty == "EMPTY"
    assert fx.always_fails == "FAILS"


def test_a_fixture_missing_a_section_is_refused():
    with pytest.raises(ValueError, match="ALWAYS_FAILS"):
        bootstrap.draft_fixture(Scripted("CORRECT\n@@WRONG@@\nW"), "zig",
                                "PRE", "PC_CHECK")


def test_the_tester_prompt_is_templated_not_drafted():
    """Prose rules the model writes for itself measured below baseline. The
    tester prompt is the project's discipline, so it is filled in, not asked
    for -- and no model call is made to produce it."""
    text = bootstrap.test_system_for("zig", "PC_CHECK")
    assert "zig" in text
    assert "PC_CHECK(expr)" in text
    assert "no prose, no fences" in text.lower()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_bootstrap.py -q`
Expected: FAIL — `AttributeError: module 'purecoder.bootstrap' has no attribute 'worked_examples'`

- [ ] **Step 3: Write the minimal implementation**

Add to `purecoder/bootstrap.py` — imports first:

```python
import re

from .client import strip_fences
from .languages import get
```

Then append:

```python
# ---- drafting ------------------------------------------------------------
#
# Every prompt here is built from WORKED EXAMPLES, never from prose rules.
# That is a measured distinction, not a style preference: across six models,
# explicit translation rules scored below baseline on a third of the runs,
# while translation examples improved every model above 1B parameters
# (arXiv:2501.19085). The C++ and JavaScript entries are the examples.

EXAMPLE_LANGUAGES = ("c++", "javascript", "rust")


def worked_examples(field: str, names=EXAMPLE_LANGUAGES) -> str:
    """The same field, as written for languages we already run."""
    return "\n\n".join(f"--- {n} ---\n{getattr(get(n), field)}" for n in names)


def draft_preamble(pc, name: str, context: str) -> str:
    """The check helper: prints the failed expression to stderr, exits
    non-zero, and counts successes. The fix loop is only as good as the text it
    feeds back, which is why printing the expression is stated as a hard
    requirement rather than left to the model."""
    system = (f"You output only {name} source code. No prose, no explanation, "
              f"no code fences.")
    user = (
        f"Here is the same test-harness helper, written for three languages we "
        f"already run:\n\n{worked_examples('preamble')}\n\n"
        f"Reference documentation for {name}:\n\n{context}\n\n"
        f"Write the equivalent for {name}. It must: declare a counter starting "
        f"at zero; define a helper named PC_CHECK taking one boolean "
        f"expression; on failure print \"CHECK FAILED: \" plus the expression "
        f"to standard error and exit with status 1; on success increment the "
        f"counter. Output only that code.")
    return strip_fences(pc.complete(system=system, user=user, grammar=None,
                                    n_predict=512)["text"])


def draft_check_call(pc, name: str, preamble: str) -> str:
    """How the helper is INVOKED, which is not always how it is named.

    Rust's is `pc_check!` while its definition reads `macro_rules! pc_check {`.
    The gate counts this token textually, so getting it wrong makes every suite
    score zero checks -- a silent failure, hence the check against the preamble.
    """
    system = "You output one line of code and nothing else."
    user = (f"This helper is already defined in a {name} file:\n\n{preamble}\n\n"
            f"Write a single line that uses it to check that 1 equals 1.")
    line = strip_fences(pc.complete(system=system, user=user, grammar=None,
                                    n_predict=64)["text"]).strip()

    match = re.search(r"[A-Za-z_][A-Za-z0-9_]*!?", line)
    if not match:
        raise ValueError(f"no call form found in {line!r}")
    call = match.group(0)
    if call.rstrip("!") not in preamble:
        raise ValueError(f"the drafted call {call!r} names a helper the "
                         f"preamble never defines")
    return call


def draft_epilogue(pc, name: str, preamble: str, context: str) -> str:
    """The tail that fails the run when nothing was checked. Without it, an
    empty suite exits 0 and the pipeline reports success on unverified code --
    the exact false green the Python path shipped for months."""
    system = (f"You output only {name} source code. No prose, no explanation, "
              f"no code fences.")
    user = (
        f"Here is the same harness tail, written for three languages we already "
        f"run:\n\n{worked_examples('epilogue')}\n\n"
        f"Reference documentation for {name}:\n\n{context}\n\n"
        f"This helper is already defined above it:\n\n{preamble}\n\n"
        f"Write the equivalent tail for {name}. It must run the tests, then, if "
        f"the counter is still zero, print \"no checks ran\" to standard error "
        f"and exit with status 2. Output only that code.")
    return strip_fences(pc.complete(system=system, user=user, grammar=None,
                                    n_predict=512)["text"])


_SECTIONS = ("WRONG", "TESTS", "EMPTY", "ALWAYS_FAILS")


def draft_fixture(pc, name: str, preamble: str, check_call: str) -> Fixture:
    """The five snippets the probes run. Delimited rather than parsed: we do
    not have a parser for this language and are not about to write one."""
    system = (f"You output only {name} source code and the exact separator "
              f"lines you are given. No prose, no explanation, no fences.")
    user = (
        f"A test harness for {name} defines this:\n\n{preamble}\n\n"
        f"Checks are written {check_call}(expression).\n\n"
        f"Write five snippets, separated by the exact lines shown:\n"
        f"1. a correct `add` function returning the sum of two integers\n"
        f"@@WRONG@@\n"
        f"2. the same function, but returning the difference instead\n"
        f"@@TESTS@@\n"
        f"3. a test body containing exactly three checks: add(1,2) is 3, "
        f"add(0,0) is 0, add(-1,1) is 0\n"
        f"@@EMPTY@@\n"
        f"4. the same test body with no checks in it at all\n"
        f"@@ALWAYS_FAILS@@\n"
        f"5. a test body containing exactly one check that must fail\n\n"
        f"Output the five snippets in that order, with the separator lines "
        f"between them and nothing else.")
    text = strip_fences(pc.complete(system=system, user=user, grammar=None,
                                    n_predict=768)["text"])

    parts = [text]
    for marker in _SECTIONS:
        head, sep, tail = parts[-1].partition(f"@@{marker}@@")
        if not sep:
            raise ValueError(f"the drafted fixture has no @@{marker}@@ section")
        parts[-1] = head
        parts.append(tail)
    return Fixture(*(p.strip() for p in parts))


def test_system_for(name: str, check_call: str) -> str:
    """The tester prompt, filled in rather than asked for.

    This is PureCoder's own discipline -- code-blind tests, no framework, no
    assertions on message text -- and a model writing its own instructions is
    the technique that measured worst. Templated, so it cannot drift.
    """
    return (
        f"You write {name} tests for a described function. Output ONLY the "
        f"test body, in the same form as the example you were shown: no main "
        f"function, no imports, no test framework, no prose, no fences. Assert "
        f"with {check_call}(expr), which is already defined: e.g. "
        f"{check_call}(add(1, 2) == 3). Use {check_call} and nothing else. "
        f"Assume the thing under test is already defined in the same file.")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_bootstrap.py -q && .venv/bin/ruff check purecoder tests`
Expected: PASS, ruff clean

- [ ] **Step 5: Commit**

```bash
git add purecoder/bootstrap.py tests/test_bootstrap.py
git commit -m "feat(bootstrap): draft the harness from worked examples, not prose rules

Every prompt shows the C++, JavaScript and Rust entries and asks for the
equivalent. That is measured rather than stylistic: explicit translation rules
scored below baseline on a third of runs across six models, while translation
examples improved every model above 1B (arXiv:2501.19085).

The tester prompt is templated for the same reason -- a model writing its own
instructions is the technique that measured worst -- and the invocation form is
observed rather than assumed, since Rust's macro is called pc_check! and
defined as macro_rules! pc_check."
```

---

### Task 5: Draft build/run, and require confirmation before running it

**Files:**
- Modify: `purecoder/bootstrap.py`
- Test: `tests/test_bootstrap.py`

**Interfaces:**
- Consumes: `client.PureCoder.complete`
- Produces: `bootstrap.draft_commands(pc, name, extension, context) -> (build: tuple, run: tuple)`, `bootstrap.confirm_commands(build, run, ask=input) -> bool`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bootstrap.py`:

```python
# ---- the trust boundary --------------------------------------------------

def test_commands_are_parsed_into_argv_not_a_shell_string():
    """These reach subprocess.Popen. A string would mean a shell, and a shell
    means the model can write a pipeline."""
    pc = Scripted("BUILD: zigc -o {bin} {src}\nRUN: {bin}")
    build, run = bootstrap.draft_commands(pc, "zig", ".zig", "DOCS")
    assert build == ("zigc", "-o", "{bin}", "{src}")
    assert run == ("{bin}",)


def test_an_interpreted_language_needs_no_build():
    pc = Scripted("BUILD: none\nRUN: zig run {src}")
    build, run = bootstrap.draft_commands(pc, "zig", ".zig", "DOCS")
    assert build == ()
    assert run == ("zig", "run", "{src}")


def test_a_run_command_that_never_names_the_source_is_refused():
    """Without {src} or {bin} the command ignores the candidate entirely and
    every probe would measure whatever it does run."""
    with pytest.raises(ValueError, match="src"):
        bootstrap.draft_commands(Scripted("BUILD: none\nRUN: zig version"),
                                 "zig", ".zig", "DOCS")


def test_a_build_command_that_produces_no_binary_is_refused():
    with pytest.raises(ValueError, match="bin"):
        bootstrap.draft_commands(Scripted("BUILD: zigc {src}\nRUN: {bin}"),
                                 "zig", ".zig", "DOCS")


def test_shell_metacharacters_are_refused():
    """Not a sandbox, just a closed door: argv is not a shell, so a pipeline
    here means the draft misunderstood the format."""
    with pytest.raises(ValueError, match="shell"):
        bootstrap.draft_commands(
            Scripted("BUILD: none\nRUN: sh -c 'cat {src} | zig run -'"),
            "zig", ".zig", "DOCS")


def test_commands_are_shown_in_full_before_confirmation(capsys):
    ok = bootstrap.confirm_commands(("zigc", "{src}"), ("{bin}",),
                                    ask=lambda _: "y")
    assert ok
    out = capsys.readouterr().out
    assert "zigc {src}" in out
    assert "{bin}" in out


def test_anything_but_yes_declines():
    for answer in ("", "n", "no", "later", "Y E S"):
        assert bootstrap.confirm_commands(("a",), ("b",),
                                          ask=lambda _: answer) is False


def test_yes_in_any_case_confirms():
    for answer in ("y", "Y", "yes", "YES", " yes "):
        assert bootstrap.confirm_commands(("a",), ("b",),
                                          ask=lambda _: answer) is True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_bootstrap.py -q`
Expected: FAIL — `AttributeError: module 'purecoder.bootstrap' has no attribute 'draft_commands'`

- [ ] **Step 3: Write the minimal implementation**

Add to the imports in `purecoder/bootstrap.py`:

```python
import shlex
```

Append:

```python
# ---- the trust boundary --------------------------------------------------
#
# Every hand-written entry's build and run commands were written by a person.
# These are written by a local model and then handed to subprocess.Popen, which
# is a different trust category from anything else in this codebase. Three
# things follow: argv rather than a shell string, structural checks that the
# command actually names the candidate, and explicit confirmation before the
# first execution.

_SHELL_METACHARACTERS = set(";|&$`><\n")


def _parse_command(label: str, line: str) -> tuple:
    if line.strip().lower() in ("none", "-", ""):
        return ()
    if set(line) & _SHELL_METACHARACTERS:
        return _refuse(f"the {label} command contains shell metacharacters; "
                       f"argv is not a shell: {line!r}")
    try:
        return tuple(shlex.split(line))
    except ValueError as e:
        return _refuse(f"the {label} command does not parse: {e}")


def _refuse(message):
    raise ValueError(message)


def draft_commands(pc, name: str, extension: str, context: str):
    """How this language compiles and runs ONE file. -> (build, run) argv.

    Placeholders are `{src}`, `{bin}` and `{python}`, filled in by the executor.
    """
    system = "You output exactly two lines and nothing else."
    user = (
        f"Reference documentation for {name}:\n\n{context}\n\n"
        f"A single source file `candidate{extension}` must be compiled (if the "
        f"language needs it) and run. Write exactly two lines:\n"
        f"BUILD: the compile command, using {{src}} for the source path and "
        f"{{bin}} for the output binary -- or the word none if this language "
        f"needs no compilation step\n"
        f"RUN: the command that runs it, using {{bin}} if you compiled one, "
        f"otherwise {{src}}\n"
        f"Use no shell features: no pipes, no redirection, no &&.")
    text = strip_fences(pc.complete(system=system, user=user, grammar=None,
                                    n_predict=128)["text"])

    lines = {}
    for raw in text.splitlines():
        key, sep, value = raw.partition(":")
        if sep and key.strip().upper() in ("BUILD", "RUN"):
            lines[key.strip().upper()] = value.strip()
    if "RUN" not in lines:
        raise ValueError(f"no RUN command in the draft: {text!r}")

    build = _parse_command("build", lines.get("BUILD", ""))
    run = _parse_command("run", lines["RUN"])

    if build and not any("{bin}" in a for a in build):
        raise ValueError("the build command never writes to {bin}, so the run "
                         "command would have no binary to execute")
    if not any("{src}" in a or "{bin}" in a for a in run):
        raise ValueError("the run command names neither {src} nor {bin}, so it "
                         "would not run the candidate at all")
    return build, run


def confirm_commands(build, run, ask=input) -> bool:
    """Show the drafted commands and require an explicit yes.

    This is the only place a local model's output becomes a process on the
    user's machine. It is shown in full, and silence is a no.
    """
    print("\nThese commands were drafted from the documentation and will be "
          "run on your machine:")
    print(f"  build : {' '.join(build) if build else '(none)'}")
    print(f"  run   : {' '.join(run)}")
    return ask("Run these? [y/N] ").strip().lower() in ("y", "yes")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_bootstrap.py -q && .venv/bin/ruff check purecoder tests`
Expected: PASS, ruff clean

- [ ] **Step 5: Commit**

```bash
git add purecoder/bootstrap.py tests/test_bootstrap.py
git commit -m "feat(bootstrap): draft build/run as argv, and confirm before running it

Every existing entry's commands were written by a person. These are written by
a local model and handed to subprocess.Popen, so: argv rather than a shell
string, shell metacharacters refused outright, a structural check that the
command actually names the candidate, and an explicit yes before the first
execution. Silence is a no."
```

---

### Task 6: The orchestrator, the live round, and the CLI

**Files:**
- Modify: `purecoder/bootstrap.py`
- Modify: `purecoder/cli.py`
- Test: `tests/test_bootstrap.py`

**Interfaces:**
- Consumes: everything above, `execute.generate_validated_python`, `rag.DocStore`/`Embedder`/`retrieve_context`, `langstore.save`
- Produces: `bootstrap.learn_language(pc, name, extension, docs_dir, *, retrieve, confirm, verbose) -> dict{ok, spec, probes, error}`, `cli.cmd_learn`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bootstrap.py`:

```python
# ---- the orchestrator ----------------------------------------------------

DRAFTS = [
    "#include <cstdio>\n#include <cstdlib>\nstatic int pc_checks = 0;\n"
    "#define PC_CHECK(x) do { if (!(x)) { std::fprintf(stderr, "
    "\"CHECK FAILED: %s\\n\", #x); std::exit(1); } pc_checks++; } while (0)\n",
    "PC_CHECK(1 == 1);",
    "int main() { pc_tests(); if (pc_checks < 1) { std::fprintf(stderr, "
    "\"no checks ran\\n\"); return 2; } return 0; }\n",
    "int add(int a,int b){return a+b;}\n"
    "@@WRONG@@\nint add(int a,int b){return a-b;}\n"
    "@@TESTS@@\nint add(int,int);\nvoid pc_tests(){ PC_CHECK(add(1,2)==3); "
    "PC_CHECK(add(0,0)==0); PC_CHECK(add(-1,1)==0); }\n"
    "@@EMPTY@@\nvoid pc_tests(){ }\n"
    "@@ALWAYS_FAILS@@\nvoid pc_tests(){ PC_CHECK(1==2); }",
    "BUILD: g++ -std=c++17 -w {src} -o {bin}\nRUN: {bin}",
]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PURECODER_HOME", str(tmp_path))
    monkeypatch.delitem(L.REGISTRY, "cpplike", raising=False)
    return tmp_path / "languages"


def test_a_language_that_passes_every_probe_is_saved(store):
    """End to end on a real toolchain, with the model scripted: the drafts are
    the C++ harness under another name, so the probes genuinely run g++."""
    _cpp()
    pc = Scripted(*DRAFTS)
    res = bootstrap.learn_language(pc, "cpplike", ".cpp", docs_dir=None,
                                   retrieve=lambda q: "DOCS",
                                   confirm=lambda b, r: True, verbose=False,
                                   live_check=False)
    assert res["ok"], res["error"]
    assert (store / "cpplike.json").is_file()
    assert L.get("cpplike").check_call == "PC_CHECK"


def test_a_language_that_fails_a_probe_is_not_saved(store):
    """A helper that counts but never fails: compiles, runs, reports success
    on wrong code. It must not reach the store."""
    drafts = list(DRAFTS)
    drafts[0] = ("#include <cstdio>\nstatic int pc_checks = 0;\n"
                 "#define PC_CHECK(x) do { pc_checks++; } while (0)\n")
    res = bootstrap.learn_language(Scripted(*drafts), "cpplike", ".cpp",
                                   docs_dir=None, retrieve=lambda q: "DOCS",
                                   confirm=lambda b, r: True, verbose=False,
                                   live_check=False)
    assert not res["ok"]
    assert "wrong implementation fails" in res["error"]
    assert not (store / "cpplike.json").exists()


def test_declining_the_commands_stops_before_anything_runs(store):
    res = bootstrap.learn_language(Scripted(*DRAFTS), "cpplike", ".cpp",
                                   docs_dir=None, retrieve=lambda q: "DOCS",
                                   confirm=lambda b, r: False, verbose=False,
                                   live_check=False)
    assert not res["ok"]
    assert "declined" in res["error"]
    assert not (store / "cpplike.json").exists()


def test_a_built_in_name_is_refused_before_any_model_call(store):
    pc = Scripted(*DRAFTS)
    res = bootstrap.learn_language(pc, "python", ".py", docs_dir=None,
                                   retrieve=lambda q: "DOCS",
                                   confirm=lambda b, r: True, verbose=False,
                                   live_check=False)
    assert not res["ok"]
    assert "built-in" in res["error"]
    assert pc.prompts == [], "a refused name must cost no model call"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_bootstrap.py -q`
Expected: FAIL — `AttributeError: module 'purecoder.bootstrap' has no attribute 'learn_language'`

- [ ] **Step 3: Write the minimal implementation**

Append to `purecoder/bootstrap.py`:

```python
# ---- the orchestrator ----------------------------------------------------

# Asked of the docs, once each. Retrieval is per-question rather than one broad
# query because the answers live in different pages: how a language prints to
# stderr is rarely on the page that says how to compile it.
QUERIES = {
    "helper": "print to standard error, exit with a status code, define a "
              "macro or function taking a boolean",
    "entry": "program entry point, main function, top-level statements",
    "syntax": "define a function taking two integers and returning an integer",
    "commands": "compile and run a single source file from the command line",
}

BUBBLE_SORT = ("a function that takes an array of integers and returns them "
               "sorted in ascending order using bubble sort")


def learn_language(pc, name: str, extension: str, docs_dir, *, retrieve,
                   confirm=confirm_commands, verbose=True, live_check=True,
                   timeout=60):
    """Draft a language entry, prove it, and save it. -> {ok, spec, probes, error}.

    `retrieve` is a callable taking a query and returning context, injected so
    the drafting path is testable without an embedding model.
    """
    from .langstore import save

    def log(message):
        if verbose:
            print(message)

    if name in BUILTIN_NAMES:
        return _failed(f"{name!r} is a built-in language -- a drafted spec may "
                       f"not replace a hand-written one")

    log(f"[learn] drafting a {name} harness")
    try:
        preamble = draft_preamble(pc, name, retrieve(QUERIES["helper"]))
        check_call = draft_check_call(pc, name, preamble)
        epilogue = draft_epilogue(pc, name, preamble, retrieve(QUERIES["entry"]))
        fixture = draft_fixture(pc, name, preamble, check_call)
        build, run = draft_commands(pc, name, extension,
                                    retrieve(QUERIES["commands"]))
    except ValueError as e:
        return _failed(f"drafting failed: {e}")

    if not confirm(build, run):
        return _failed("declined: the drafted commands were not confirmed, so "
                       "nothing was run and nothing was saved")

    spec = LanguageSpec(
        name=name, extension=extension,
        probe=(run[0],) if run and "{" not in run[0] else (),
        build=build, run=run, preamble=preamble, epilogue=epilogue,
        test_system=test_system_for(name, check_call), check_call=check_call,
    )

    log(f"[learn] probing the candidate ({len(build or run)} argv)")
    ok, probes = probe_language(spec, fixture, timeout=timeout)
    for probe in probes:
        log(f"[learn]   {'pass' if probe.ok else 'FAIL'}  {probe.name}")
    if not ok:
        failed = ", ".join(p.name for p in probes if not p.ok)
        return _failed(f"the candidate failed a probe: {failed}", probes=probes)

    if live_check:
        log("[learn] one live round: bubble sort")
        result = generate_validated_python(pc, BUBBLE_SORT, spec=spec,
                                           verbose=verbose)
        if not result["ok"]:
            return _failed(f"the harness runs, but the writer and tester could "
                           f"not work inside it: {result['error']}",
                           probes=probes)

    path = save(spec, docs_dir=str(docs_dir) if docs_dir else "")
    register(spec)
    log(f"[learn] registered {name} -> {path}")
    return {"ok": True, "spec": spec, "probes": probes, "error": ""}


def _failed(error, probes=()):
    return {"ok": False, "spec": None, "probes": list(probes), "error": error}
```

Extend the imports at the top of `purecoder/bootstrap.py`:

```python
from .execute import generate_validated_python, run_candidate
from .languages import BUILTIN_NAMES, LanguageSpec, get, register
```

Add to `purecoder/cli.py` — a new command function after `cmd_ask`:

```python
def cmd_learn(pc, args):
    from .bootstrap import learn_language
    from .rag import DocStore, Embedder, retrieve_context

    store = DocStore(Embedder(device=args.device), path=args.store)
    store.ingest_dir(args.docs_dir)

    res = learn_language(pc, args.name, args.ext, args.docs_dir,
                         retrieve=lambda q: retrieve_context(store, q),
                         live_check=not args.no_live)
    if not res["ok"]:
        print(f"\nnot registered: {res['error']}")
        return 1
    print(f"\n{args.name} is registered. It is a drafted entry, proven by "
          f"probe rather than written by hand -- try it on something small "
          f"first:\n  purecoder --lang {args.name} code \"...\"")
```

Register the subcommand in `main()`, after the `ingest` line:

```python
    sl = sub.add_parser("learn")
    sl.add_argument("name")
    sl.add_argument("docs_dir")
    sl.add_argument("--ext", required=True,
                    help="source file extension, e.g. .zig")
    sl.add_argument("--no-live", action="store_true",
                    help="skip the live generation round (probes only)")
```

And add `"learn": cmd_learn,` to the dispatch dict.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check purecoder tests examples`
Expected: PASS, ruff clean. Then confirm the command is wired:
Run: `.venv/bin/python -m purecoder learn --help`
Expected: usage text showing `name`, `docs_dir`, `--ext`, `--no-live`

- [ ] **Step 5: Commit**

```bash
git add purecoder/bootstrap.py purecoder/cli.py tests/test_bootstrap.py
git commit -m "feat(cli): learn a language from its docs, and refuse it unless it proves out

purecoder learn <name> <docs_dir> --ext .x drafts a harness, requires the
drafted commands to be confirmed, runs five probes against a real toolchain,
and finishes with one live round on a bubble sort -- the probes prove the
harness can fail wrong code, the live round proves the writer and tester can
work inside it. Nothing is saved unless every one of them passes, and the
refusal names the probe that failed."
```

---

### Task 7: Document the layer and its boundaries

**Files:**
- Modify: `README.md`, `docs/ARCHITECTURE.md`, `docs/STATUS.md`

**Interfaces:**
- Consumes: everything above
- Produces: nothing code-facing

- [ ] **Step 1: Add the README section**

After the existing `## Languages` section, add:

````markdown
### Teaching it a language

```bash
purecoder learn zig ./zig-docs --ext .zig
```

Points the pipeline at a language's documentation and has it draft its own
registry entry: the check helper, the harness tail, the tester prompt, and the
build/run commands. The drafting prompts carry the C++, JavaScript and Rust
entries as worked examples, because [measured results](https://huggingface.co/papers/2501.19085)
show translation examples help at every model size while prose translation
rules score *below* baseline on a third of runs.

Nothing is registered until it proves itself. Five probes run against a trivial
`add(a, b)` on the real toolchain — a correct implementation passes, a wrong
one fails, an empty suite fails, a broken one produces a diagnostic, a failing
check fails the run — and then one live round on a bubble sort. A harness that
merely compiles is not a harness that can fail wrong code, and only the second
kind is worth having.

The drafted build/run commands are shown and need explicit confirmation before
they first execute: every other entry's commands were written by hand, and
these are the one place a local model's output becomes a process.
````

- [ ] **Step 2: Add the ARCHITECTURE section**

After §3 (execution validation), add a §3.5 covering: what is drafted vs
templated and why (the measured prose-rules result), the probe list, the
statement that a bootstrapped entry is a candidate until proven, and the two
recorded gaps (`deep_equality`, per-language `stop` tokens) with MultiPL-E
named as the source of the comparison.

- [ ] **Step 3: Update STATUS.md**

Add a `| 7 | language bootstrap | ✅ tested | ... |` row, add the refreshed test
count in the header line, and add to Known boundaries:

```markdown
- **A bootstrapped language is proven, not trusted.** Five probes plus a live
  round decide it; a harness that cannot fail wrong code is refused. What the
  probes cannot see is idiom: a spec can pass every probe and still produce
  code no practitioner of the language would write.
- The doc chunker is Python-and-markdown only, so a language's own code samples
  are chunked as prose. tree-sitter chunking remains the real fix.
```

- [ ] **Step 4: Verify the counts match**

Run: `.venv/bin/python -m pytest -q | tail -1`
Then confirm the number appears in both `README.md` and `docs/STATUS.md`.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/ARCHITECTURE.md docs/STATUS.md
git commit -m "docs: describe the language bootstrap, and what its probes cannot see"
```

---

## Self-Review

**Spec coverage.** Drafting from worked examples → Task 4. Templated tester
prompt → Task 4. Build/run trust boundary → Task 5. Five probes → Task 3. Live
bubble-sort round → Task 6. JSON store under a user data dir → Tasks 1–2. Shadow
guard → Tasks 1, 2, 6. Provenance → Task 1. Documented boundaries → Task 7. The
two MultiPL-E gaps (`deep_equality`, `stop`) are recorded in the spec and in
Task 7's docs, and are deliberately not built.

**Type consistency.** `Fixture` has the same five fields in Tasks 3, 4 and 6.
`Probe.name`/`.ok`/`.detail` are used identically in Tasks 3 and 6.
`probe_language` returns `(bool, list[Probe])` everywhere. `draft_commands`
returns tuples, which is what `LanguageSpec.build`/`.run` require and what
`_TUPLE_FIELDS` restores from JSON in Task 1.

**Known risk, flagged not solved.** `learn_language` builds `probe=(run[0],)`
only when `run[0]` has no placeholder — a language whose runner is the built
binary gets no availability probe, so `available()` returns True on a machine
without the toolchain and the failure surfaces at build time instead. That
matches how `c++` behaves today (its probe is `g++`, its run is `{bin}`) only
because the C++ entry was hand-written to probe the compiler. Task 6 cannot
infer that. Accept it, or extend Task 5's prompt to ask for the toolchain
binary name explicitly — the second is one more line in the draft and worth
doing if the first bootstrapped language turns out to need it.
