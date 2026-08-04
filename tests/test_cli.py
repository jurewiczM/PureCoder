"""Flag resolution: explicit flag beats env var beats per-command default."""

import pytest

from purecoder.cli import resolve_contract, resolve_language


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


# ---- language resolution -------------------------------------------------

class LangArgs:
    def __init__(self, lang="python", spec=""):
        self.lang, self.spec, self.contract = lang, spec, None


def test_a_known_available_language_resolves():
    assert resolve_language(LangArgs("python")).name == "python"


def test_an_alias_resolves():
    assert resolve_language(LangArgs("py")).name == "python"


def test_an_unknown_language_is_refused(capsys):
    assert resolve_language(LangArgs("cobol")) is None
    out = capsys.readouterr().out
    assert "unknown language" in out
    assert not out.startswith('"'), "KeyError repr quotes leaked into the message"


def test_a_permanently_unvalidatable_language_is_refused(capsys):
    assert resolve_language(LangArgs("powerquery")) is None
    out = capsys.readouterr().out
    assert "Power BI" in out or "Excel" in out
    assert "Available right now" in out


def test_a_declared_but_unwired_language_is_refused(capsys):
    assert resolve_language(LangArgs("go")) is None
    assert "not implemented" in capsys.readouterr().out


def test_a_spec_contradicting_the_flag_is_refused(capsys):
    """`--lang python` plus "a C++ Dijkstra" is how the pipeline used to
    silently emit `import heapq`."""
    args = LangArgs("python", "a C++ implementation of Dijkstra")
    assert resolve_language(args) is None
    assert "asks for c++" in capsys.readouterr().out


def test_a_spec_naming_its_own_language_is_not_a_contradiction():
    args = LangArgs("c++", "a C++ implementation of Dijkstra")
    assert resolve_language(args).name == "c++"


def test_an_alias_in_the_spec_is_not_a_contradiction():
    """--lang c++ with a spec that says 'cpp' means the same thing."""
    args = LangArgs("cpp", "a cpp quicksort")
    assert resolve_language(args).name == "c++"


# ---- every code-producing command resolves the language ------------------

def test_ask_refuses_a_language_it_cannot_validate(capsys):
    """`ask` is `code` with retrieval in front of it, and it used to parse
    --lang and drop it -- answering a C++ question in Python, which is the one
    failure the registry exists to stop. The refusal must come before the RAG
    import, so this needs neither a store nor sentence-transformers."""
    from purecoder.cli import cmd_ask

    assert cmd_ask(None, LangArgs("powerquery", "anything")) == 1
    assert "Power BI" in capsys.readouterr().out


def test_ask_without_an_index_names_the_missing_files(capsys, tmp_path):
    """Forgetting to ingest is the common mistake, and it used to surface as a
    FileNotFoundError traceback out of np.load -- after paying for a model
    load first. Checking the files before building the Embedder is what lets
    this test run at all: sentence-transformers is not in the base install."""
    from purecoder.cli import cmd_ask

    args = LangArgs("python", "anything")
    args.store, args.device, args.retries, args.show_tests = \
        str(tmp_path / "absent"), "cpu", 1, False

    assert cmd_ask(None, args) == 1
    out = capsys.readouterr().out
    assert "no index at" in out
    assert "purecoder ingest" in out


# ---- a learned language's own documentation ------------------------------

class DocArgs(LangArgs):
    def __init__(self, lang="python", spec="", **kw):
        super().__init__(lang, spec)
        self.device, self.store, self.no_docs = "cpu", None, False
        self.retries, self.show_tests = 1, False
        self.__dict__.update(kw)


@pytest.fixture
def learned(store, monkeypatch):
    """A registered language with a real index of its own documentation.

    `open_docs` builds an `Embedder` for real, so standing in for the model is
    the only way to exercise the path that matters. Every other test of this
    feature asserts a FAILURE mode -- without this one the suite would stay
    green if grounding never happened at all.
    """
    from conftest import FakeEmbedder

    from purecoder import rag
    from purecoder.langstore import docs_index_path
    from purecoder.languages import LanguageSpec, register

    monkeypatch.setattr(rag, "Embedder", lambda **kw: FakeEmbedder())
    docs = store.parent / "zig-docs"
    docs.mkdir(parents=True)
    (docs / "io.md").write_text(
        "# Alpha\nalpha alpha. Use Zig.print to write output.\n")

    index = docs_index_path("ziglike")
    index.parent.mkdir(parents=True, exist_ok=True)
    s = rag.DocStore(FakeEmbedder(), path=str(index))
    s.ingest_dir(str(docs), verbose=False)
    s.save()

    spec = LanguageSpec(name="ziglike", extension=".zig", docs_store="ziglike",
                        run=("true",), test_system="x", probe=("true",))
    register(spec)
    return spec


def test_generating_reads_the_docs_the_language_was_learned_from(learned, capsys):
    """The claim the whole feature exists for: `code --lang X` is grounded with
    no second ingest and no --store."""
    from purecoder.cli import _grounded, ground_in_docs

    context, hint = ground_in_docs(DocArgs("ziglike"), learned, "alpha")
    assert "Relevant documentation:" in context
    assert "Zig.print" in context
    assert "using the ziglike docs from `learn`" in capsys.readouterr().out
    # and the caller puts it in front of the task rather than around it
    assert _grounded(context, "alpha").endswith("alpha")


def test_the_docs_answer_did_you_mean_for_that_language(learned):
    """The second half of the same wiring: the index's symbol library reaches
    the fix loop, so a name the toolchain rejects gets the real one back."""
    from purecoder.cli import ground_in_docs

    _, hint = ground_in_docs(DocArgs("ziglike"), learned, "alpha")
    assert "Zig.print" in hint("error: cannot find `Zig.prnt` in this scope")


def test_an_explicit_store_grounds_any_language(learned, store):
    """--store wins over the language's own index, and works for a language
    that never had one -- which is what makes `project` groundable at all,
    since a hand-written language has no docs_store."""
    from purecoder.cli import ground_in_docs
    from purecoder.langstore import docs_index_path
    from purecoder.languages import get

    named = str(docs_index_path("ziglike"))     # any index, reached by path
    context, hint = ground_in_docs(DocArgs("python", store=named),
                                   get("python"), "alpha")
    assert "Zig.print" in context and hint is not None


def test_the_scaffolder_grounds_the_code_and_nothing_else(monkeypatch, tmp_path):
    """Retrieval reaches the one artifact that is execution-validated. The
    Makefile's targets come from spec.project, the .env is derived from the
    code it is shown, and the README is prose -- context is double-edged on
    this card, and those three would spend it for nothing."""
    from conftest import FakeModel

    from purecoder import scaffold

    seen = {}

    def spy(pc, description, **kw):
        seen["description"] = description
        seen["error_hint"] = kw.get("error_hint")
        return {"ok": True, "text": "def f():\n    return 1\n", "tests": "",
                "contract": None, "attempts": 1, "error": ""}

    monkeypatch.setattr(scaffold, "generate_validated_python", spy)
    pc = FakeModel(completions=["HOST=x\n", "all:\n\techo hi\n", "# readme\n"])
    scaffold.scaffold_project(pc, "proj", "build a thing",
                              outdir=str(tmp_path / "out"), verbose=False,
                              use_contract=False, docs="DOCS HERE",
                              error_hint=lambda e: "hint")

    assert "DOCS HERE" in seen["description"]
    assert seen["error_hint"] is not None
    # The other three prompts are the model's own record of what it was asked.
    assert not any("DOCS HERE" in p for p in pc.prompts), \
        "documentation reached an artifact that cannot use it"


def test_a_hand_written_language_is_left_alone():
    """C++ was not learned from anything. Grounding must be something a
    learned entry opts into, not a cost every language pays."""
    from purecoder.cli import ground_in_docs
    from purecoder.languages import get

    assert ground_in_docs(DocArgs(), get("python"), "add two numbers") == ("", None)


def test_no_docs_turns_it_off():
    from purecoder.cli import ground_in_docs
    from purecoder.languages import LanguageSpec

    spec = LanguageSpec(name="zig", extension=".zig", docs_store="zig")
    assert ground_in_docs(DocArgs(no_docs=True), spec, "a thing") == ("", None)


def test_a_missing_index_does_not_stop_generation(capsys, store):
    """The harness is what proves a learned language's output, and it needs
    neither an index nor sentence-transformers. Losing the docs must cost the
    grounding, not the command."""
    from purecoder.cli import ground_in_docs
    from purecoder.languages import LanguageSpec

    spec = LanguageSpec(name="zig", extension=".zig", docs_store="zig")
    assert ground_in_docs(DocArgs(), spec, "a thing") == ("", None)
    assert "Traceback" not in capsys.readouterr().out


def test_an_unreadable_index_is_reported_and_stepped_over(capsys, tmp_path):
    """`load` refuses an index it cannot trust. For `code` that is a downgrade
    to ungrounded generation, not a failure -- said out loud either way."""
    from purecoder.cli import open_docs

    (tmp_path / "idx.npy").write_bytes(b"not an array")
    (tmp_path / "idx.json").write_text("{}")
    assert open_docs(str(tmp_path / "idx"), "cpu") is None
    assert "generating without the documentation" in capsys.readouterr().out


def test_ask_prefers_an_index_the_user_named_over_the_language(capsys, store):
    """--store is explicit. A learned language's own docs are the fallback for
    when nothing was named, never an override of what was.

    Both indexes are absent, so the message names which one it went looking
    for -- and it gets there before any model is loaded, which is what lets
    this run without sentence-transformers.
    """
    from purecoder.cli import cmd_ask
    from purecoder.languages import LanguageSpec, register

    # The `store` fixture points PURECODER_HOME here AND restores the registry;
    # registering a language without it leaks into every later test.
    register(LanguageSpec(name="ziglike", extension=".zig", docs_store="ziglike",
                          run=("true",), test_system="x", probe=("true",)))
    named = str(store.parent / "mine")

    assert cmd_ask(None, DocArgs("ziglike", "a thing", store=named)) == 1
    assert "no index at " + named in capsys.readouterr().out


def test_ask_falls_back_to_the_language_index_when_none_is_named(capsys, store):
    from purecoder.cli import cmd_ask
    from purecoder.languages import LanguageSpec, register

    register(LanguageSpec(name="ziglike", extension=".zig", docs_store="ziglike",
                          run=("true",), test_system="x", probe=("true",)))

    assert cmd_ask(None, DocArgs("ziglike", "a thing")) == 1
    out = capsys.readouterr().out
    assert "using the ziglike docs from `learn`" in out
    assert str(store.parent / "docs" / "ziglike") in out


# ---- the ingest review ---------------------------------------------------

class _Plan:
    def __init__(self, excluded=()):
        self.root, self.chunks, self.sources = "docs", ("a",), ("a.md",)
        self.skipped_dirs, self.binaries, self.duplicates = (), (), 0
        self.excluded = excluded

    @property
    def per_file(self):
        return [("a.md", 1)]


def test_the_review_returns_the_plan_when_accepted(monkeypatch, capsys):
    from purecoder.cli import review_plan

    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert review_plan(lambda ex: _Plan(), []) is not None
    assert "a.md" in capsys.readouterr().out


def test_the_review_can_abandon_the_index(monkeypatch, capsys):
    from purecoder.cli import review_plan

    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert review_plan(lambda ex: _Plan(), []) is None
    assert "nothing indexed" in capsys.readouterr().out


def test_an_exclusion_is_applied_and_printed_back_as_a_flag(monkeypatch, capsys):
    """A prompt that cannot be turned back into a command is a dead end: a
    session spent narrowing the index by hand has to be replayable."""
    from purecoder.cli import review_plan

    answers = iter(["e", "internal drafts/*", "y"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))

    seen = []

    def plan_for(exclude):
        seen.append(tuple(exclude))
        return _Plan(excluded=exclude)

    assert review_plan(plan_for, []) is not None
    assert seen == [(), ("internal", "drafts/*")]
    assert "--exclude internal --exclude drafts/*" in capsys.readouterr().out


def test_a_non_interactive_review_does_not_read_stdin(monkeypatch):
    """CI and `echo y | purecoder ...` are how this project is tested. A prompt
    blocking on a closed stdin would break the reproduce command in its own
    live-run writeup."""
    from purecoder.cli import review_plan

    def explode(_):
        raise AssertionError("prompted with no terminal to prompt at")

    monkeypatch.setattr("builtins.input", explode)
    assert review_plan(lambda ex: _Plan(), [], interactive=False) is not None


def test_a_plan_that_matches_nothing_stops_before_the_model_loads(capsys):
    from purecoder.cli import review_plan

    def plan_for(exclude):
        raise ValueError("no files matched under docs")

    assert review_plan(plan_for, [], interactive=False) is None
    assert "nothing to index" in capsys.readouterr().out


def test_python_m_purecoder_propagates_the_exit_code():
    """The README calls `python -m purecoder` identical to the console script.
    setuptools wraps the latter in sys.exit(main()); without the same here a
    refusal printed its reason and exited 0."""
    import subprocess
    import sys

    out = subprocess.run([sys.executable, "-m", "purecoder", "--lang", "go",
                          "code", "x"], capture_output=True, text=True)
    assert "not implemented" in out.stdout
    assert out.returncode == 1, "a refusal must not look like success"


def test_every_code_producing_command_resolves_the_language():
    """A guard against the next command that forgets. `env` and `make` are
    exempt: config artifacts have no language."""
    import inspect

    from purecoder import cli

    for name in ("cmd_code", "cmd_ask", "cmd_project"):
        src = inspect.getsource(getattr(cli, name))
        assert "resolve_language(args)" in src, f"{name} ignores --lang"


def test_measure_is_routed_and_reports_without_a_server():
    """The measurement is a command like any other. With the server down every
    task lands in "no code" -- which is the honest verdict, not zero
    divergence."""
    from purecoder import bench, cli

    class Args:
        repeats = 1
        retries = 1
        timeout = 5

    class Dead:
        def complete(self, *a, **kw):
            raise RuntimeError("server down")

        def code(self, *a, **kw):
            raise RuntimeError("server down")

    rows = bench.measure(Dead(), tasks=bench.TASKS[:1], repeats=1,
                         max_retries=1, verbose=False)["rows"]
    assert {r["verdict"] for r in rows} == {"no code"}
    assert hasattr(cli, "cmd_measure")


def test_declared_packages_reach_the_loop_and_default_to_none():
    """`--with` is on `code` alone. A global flag that `project` ignored would
    generate under a permission that was never real."""
    from purecoder import cli

    class Args:
        spec = "the mean of a list"
        lang = "python"
        retries = 1
        contract = False
        show_tests = False
        store = None
        no_docs = True
        packages = ["numpy"]

    seen = {}

    def spy(pc, description, **kw):
        seen.update(kw)
        return {"ok": True, "text": "", "tests": "", "contract": None,
                "attempts": 1, "error": ""}

    real = cli.generate_validated_python
    cli.generate_validated_python = spy
    try:
        cli.cmd_code(None, Args())
    finally:
        cli.generate_validated_python = real
    assert seen["packages"] == ("numpy",)
