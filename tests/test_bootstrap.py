"""The bootstrap gate.

A drafted LanguageSpec is a claim. These are the probes that turn it into a
fact -- run against a real toolchain, because a harness that compiles is not a
harness that can fail wrong code, and that difference is the whole point.
"""

import dataclasses

import pytest
from conftest import FakeModel

from purecoder import bootstrap
from purecoder import languages as L

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
    0 passes "it compiles", "it runs" and "it reports" -- and is worthless.
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
    """No "no checks ran" tail: an empty suite exits 0 and reports success."""
    spec = dataclasses.replace(
        _cpp(), epilogue="int main() { pc_tests(); return 0; }\n")
    ok, results = bootstrap.probe_language(spec, CPP_FIXTURE)
    assert not ok
    assert "a suite with no checks fails" in [r.name for r in results if not r.ok]


def test_a_broken_implementation_must_produce_an_error_message():
    _, results = bootstrap.probe_language(_cpp(), CPP_FIXTURE)
    probe = next(r for r in results if "broken" in r.name)
    assert probe.ok and probe.detail.strip(), "no diagnostic to feed the fix loop"


def test_every_probe_carries_a_human_readable_name():
    _, results = bootstrap.probe_language(_cpp(), CPP_FIXTURE)
    assert all(r.name and " " in r.name for r in results)


# ---- drafting ------------------------------------------------------------

def test_worked_examples_carry_real_entries_not_descriptions():
    """Measured result: prose translation rules score BELOW baseline on some
    models, while translation examples never hurt. The prompt must show the C++
    and JavaScript harnesses, not describe them."""
    text = bootstrap.worked_examples("preamble")
    assert "PC_CHECK" in text
    assert "#include <cstdio>" in text          # the real C++ entry
    assert "process.exit(1)" in text            # the real JavaScript entry


def test_the_preamble_prompt_shows_examples_and_the_retrieved_docs():
    pc = FakeModel(completions=["HELPER CODE"])
    out = bootstrap.draft_preamble(pc, "zig", "DOCS ABOUT ZIG")
    assert out == "HELPER CODE"
    _system, user = pc.calls[0]
    assert "DOCS ABOUT ZIG" in user
    assert "#include <cstdio>" in user, "no worked example in the prompt"


def test_the_check_call_is_extracted_from_a_single_line_answer():
    """Rust's invocation is `pc_check!` while its definition reads
    `macro_rules! pc_check {`, so the form has to be observed, not assumed."""
    assert bootstrap.draft_check_call(FakeModel(completions=["PC_CHECK(1 == 1);"]),
                                      "zig", "PRE PC_CHECK") == "PC_CHECK"
    assert bootstrap.draft_check_call(FakeModel(completions=["pc_check!(1 == 1);"]),
                                      "rs", "PRE pc_check") == "pc_check!"


def test_a_check_call_the_preamble_never_defines_is_refused():
    """The gate counts this token textually. If the preamble does not define
    it, every suite scores zero checks and the language silently fails."""
    with pytest.raises(ValueError, match="disagree"):
        bootstrap.draft_check_call(FakeModel(completions=["VERIFY(1 == 1);"]),
                                   "zig", "PRE")


def test_a_leading_keyword_is_not_mistaken_for_the_call():
    """Observed live: OCaml answered `if pc_check 1 = 1 then ...` and taking the
    first identifier yielded `if`, which the preamble of course never defines.
    Candidates are tried against the preamble rather than assumed by position."""
    pre = "let pc_check cond = if not cond then exit 1"
    assert bootstrap.draft_check_call(
        FakeModel(completions=["if pc_check (1 = 1) then () else ()"]),
        "ocaml", pre) == "pc_check"


def test_the_fixture_comes_back_as_five_labelled_snippets():
    pc = FakeModel(completions=["CORRECT\n@@WRONG@@\nWRONG\n@@TESTS@@\nTESTS\n"
                  "@@EMPTY@@\nEMPTY\n@@ALWAYS_FAILS@@\nFAILS"])
    fx = bootstrap.draft_fixture(pc, "zig", "PRE", "POST", "PC_CHECK")
    assert fx.correct == "CORRECT"
    assert fx.wrong == "WRONG"
    assert fx.tests == "TESTS"
    assert fx.empty == "EMPTY"
    assert fx.always_fails == "FAILS"


def test_a_fixture_missing_a_section_is_refused():
    """The refusal names the FIRST missing section, so a draft that stops early
    reports where it stopped rather than where it was going."""
    with pytest.raises(ValueError, match="ALWAYS_FAILS"):
        bootstrap.draft_fixture(
            FakeModel(completions=["C\n@@WRONG@@\nW\n@@TESTS@@\nT\n@@EMPTY@@\nE"]),
            "zig", "PRE", "POST", "PC_CHECK")

    with pytest.raises(ValueError, match="TESTS"):
        bootstrap.draft_fixture(FakeModel(completions=["C\n@@WRONG@@\nW"]), "zig",
                                "PRE", "POST", "PC_CHECK")


def test_the_tester_prompt_is_templated_not_drafted():
    """Prose rules the model writes for itself measured below baseline. The
    tester prompt is the project's discipline, so it is filled in, not asked
    for -- and no model call is made to produce it."""
    text = bootstrap.test_system_for("zig", "PC_CHECK")
    assert "zig" in text
    assert "PC_CHECK(expr)" in text
    assert "no prose, no fences" in text.lower()


# ---- the trust boundary --------------------------------------------------

def test_commands_are_parsed_into_argv_not_a_shell_string():
    """These reach subprocess.Popen. A string would mean a shell, and a shell
    means the model can write a pipeline."""
    pc = FakeModel(completions=["BUILD: zigc -o {bin} {src}\nRUN: {bin}\nTOOLCHAIN: zigc"])
    build, run, toolchain = bootstrap.draft_commands(pc, "zig", ".zig", "DOCS")
    assert build == ("zigc", "-o", "{bin}", "{src}")
    assert run == ("{bin}",)
    assert toolchain == "zigc"


def test_an_interpreted_language_needs_no_build():
    pc = FakeModel(completions=["BUILD: none\nRUN: zig run {src}\nTOOLCHAIN: zig"])
    build, run, _ = bootstrap.draft_commands(pc, "zig", ".zig", "DOCS")
    assert build == ()
    assert run == ("zig", "run", "{src}")


def test_the_toolchain_binary_is_required():
    """Without it a compiled language gets no availability probe: run[0] is
    {bin}, so available() would return True on a machine with no compiler and
    --lang zig would be accepted anywhere."""
    with pytest.raises(ValueError, match="toolchain"):
        bootstrap.draft_commands(FakeModel(completions=["BUILD: none\nRUN: zig run {src}"]),
                                 "zig", ".zig", "DOCS")


def test_a_run_command_that_never_names_the_source_is_refused():
    """Without {src} or {bin} the command ignores the candidate entirely and
    every probe would measure whatever it does run."""
    with pytest.raises(ValueError, match="src"):
        bootstrap.draft_commands(
            FakeModel(completions=["BUILD: none\nRUN: zig version\nTOOLCHAIN: zig"]),
            "zig", ".zig", "DOCS")


def test_a_build_command_that_produces_no_binary_is_refused():
    with pytest.raises(ValueError, match="bin"):
        bootstrap.draft_commands(
            FakeModel(completions=["BUILD: zigc {src}\nRUN: {bin}\nTOOLCHAIN: zigc"]),
            "zig", ".zig", "DOCS")


@pytest.mark.parametrize("line", [
    "sh -c 'cat {src} | zig run -'",
    "zig run {src} > /tmp/out",
    "zig build {src} && ./a.out",
    "zig run $(echo {src})",
])
def test_shell_metacharacters_are_refused(line):
    """Not a sandbox, just a closed door: argv is not a shell, so a pipeline
    here means the draft misunderstood the format."""
    with pytest.raises(ValueError, match="shell"):
        bootstrap.draft_commands(
            FakeModel(completions=[f"BUILD: none\nRUN: {line}\nTOOLCHAIN: zig"]),
            "zig", ".zig", "DOCS")


def test_commands_are_shown_in_full_before_confirmation(capsys):
    ok = bootstrap.confirm_commands(("zigc", "{src}"), ("{bin}",),
                                    ask=lambda _: "y")
    assert ok
    out = capsys.readouterr().out
    assert "zigc {src}" in out
    assert "{bin}" in out


@pytest.mark.parametrize("answer", ["", "n", "no", "later", "Y E S"])
def test_anything_but_yes_declines(answer):
    assert bootstrap.confirm_commands(("a",), ("b",),
                                      ask=lambda _: answer) is False


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES", " yes "])
def test_yes_in_any_case_confirms(answer):
    assert bootstrap.confirm_commands(("a",), ("b",),
                                      ask=lambda _: answer) is True


# ---- the orchestrator ----------------------------------------------------

# The drafts are the real C++ harness under another name, so the probes run g++
# for real: the orchestrator is tested end to end with only the model faked.
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
    "BUILD: g++ -std=c++17 -w {src} -o {bin}\nRUN: {bin}\nTOOLCHAIN: g++",
]


def _learn(pc, store, name="cpplike", **kw):
    return bootstrap.learn_language(
        pc, name, ".cpp", docs_dir=None, retrieve=lambda q: "DOCS",
        confirm=lambda b, r: True, verbose=False, live_check=False, **kw)


def test_a_language_that_passes_every_probe_is_saved(store):
    _cpp()
    res = _learn(FakeModel(completions=DRAFTS), store)
    assert res["ok"], res["error"]
    assert (store / "cpplike.json").is_file()
    assert L.get("cpplike").check_call == "PC_CHECK"
    assert L.get("cpplike").probe == ("g++", "--version")


def test_a_language_that_fails_a_probe_is_not_saved(store):
    """A helper that counts but never fails: compiles, runs, reports success on
    wrong code. It must not reach the store."""
    _cpp()
    drafts = list(DRAFTS)
    drafts[0] = ("#include <cstdio>\nstatic int pc_checks = 0;\n"
                 "#define PC_CHECK(x) do { pc_checks++; } while (0)\n")
    res = _learn(FakeModel(completions=drafts), store)
    assert not res["ok"]
    assert "wrong implementation fails" in res["error"]
    assert not (store / "cpplike.json").exists()
    assert "cpplike" not in L.REGISTRY


def test_declining_the_commands_stops_before_anything_runs(store):
    res = bootstrap.learn_language(
        FakeModel(completions=DRAFTS), "cpplike", ".cpp", docs_dir=None,
        retrieve=lambda q: "DOCS", confirm=lambda b, r: False, verbose=False,
        live_check=False)
    assert not res["ok"]
    assert "declined" in res["error"]
    assert not (store / "cpplike.json").exists()


@pytest.mark.parametrize("name", ["python", "powerquery"])
def test_a_reserved_name_is_refused_before_any_model_call(store, name):
    """A wired entry is the reference implementation; a permanent refusal is a
    standing decision. Neither may be replaced by a draft."""
    pc = FakeModel(completions=DRAFTS)
    res = _learn(pc, store, name=name)
    assert not res["ok"]
    assert "reserved" in res["error"]
    assert pc.prompts == [], "a refused name must cost no model call"


def test_a_placeholder_name_is_accepted(store):
    """`learn ocaml` was refused because the placeholder that exists so a
    refusal can name the language also reserved it -- found on the first live
    run, and worked around with the name `ocaml5`."""
    _cpp()
    res = _learn(FakeModel(completions=DRAFTS), store, name="ocaml")
    assert res["ok"], res["error"]
    assert (store / "ocaml.json").is_file()


def test_a_draft_that_does_not_parse_is_reported_not_raised(store):
    res = _learn(FakeModel(completions=["PRE PC_CHECK", "PC_CHECK(1==1);", "POST",
                          "no separators here at all", "BUILD: none\nRUN: x"]),
                 store)
    assert not res["ok"]
    assert "drafting failed" in res["error"]


# ---- the live round ------------------------------------------------------

BUBBLE_CPP = ("#include <vector>\nstd::vector<int> bubble_sort(std::vector<int> v)"
              "{ for (size_t i=0;i<v.size();i++) for (size_t j=0;j+1<v.size();j++)"
              " if (v[j]>v[j+1]) std::swap(v[j],v[j+1]); return v; }")
BUBBLE_TESTS = (
    "#include <vector>\nstd::vector<int> bubble_sort(std::vector<int>);\n"
    "void pc_tests(){ std::vector<int> a{3,1,2}; std::vector<int> b{1,2,3};\n"
    "  PC_CHECK(bubble_sort(a)==b); PC_CHECK(bubble_sort({}).empty());\n"
    "  std::vector<int> c{5}; PC_CHECK(bubble_sort(c)==c); }")


def test_the_live_round_runs_and_can_pass(store):
    """The default CLI path. It compiles a real bubble sort with g++."""
    _cpp()
    pc = FakeModel(completions=[*DRAFTS, BUBBLE_TESTS],
                        code_outputs=[BUBBLE_CPP])
    res = bootstrap.learn_language(
        pc, "cpplike", ".cpp", docs_dir=None, retrieve=lambda q: "DOCS",
        confirm=lambda b, r: True, verbose=False, live_check=True)
    assert res["ok"], res["error"]
    assert pc.code_kwargs[0]["language"] == "cpplike"


def test_the_live_round_uses_the_same_timeout_as_the_probes(store):
    """A spec that passed five probes at 60s must not be failed by a 10s live
    round on a slow toolchain."""
    _cpp()
    seen = {}
    pc = FakeModel(completions=[*DRAFTS, BUBBLE_TESTS],
                        code_outputs=[BUBBLE_CPP])

    import purecoder.bootstrap as B
    real = B.generate_validated_python

    def spy(*a, **kw):
        seen["timeout"] = kw.get("timeout")
        return real(*a, **kw)

    B.generate_validated_python = spy
    try:
        bootstrap.learn_language(pc, "cpplike", ".cpp", docs_dir=None,
                                 retrieve=lambda q: "DOCS",
                                 confirm=lambda b, r: True, verbose=False,
                                 live_check=True, timeout=45)
    finally:
        B.generate_validated_python = real
    assert seen["timeout"] == 45


def test_a_harness_the_writer_cannot_work_in_is_not_saved(store):
    """The probes and the live round are different claims: this harness passes
    every probe and still fails, because the writer's output never converges."""
    _cpp()
    pc = FakeModel(completions=[*DRAFTS, BUBBLE_TESTS],
                        code_outputs=["int not_bubble_sort(){return 0;}"])
    res = bootstrap.learn_language(
        pc, "cpplike", ".cpp", docs_dir=None, retrieve=lambda q: "DOCS",
        confirm=lambda b, r: True, verbose=False, live_check=True)
    assert not res["ok"]
    assert "could not work inside it" in res["error"]
    assert not (store / "cpplike.json").exists()


def test_a_learned_language_refuses_to_scaffold_rather_than_crashing(store):
    """A learned spec proves it can be run; it says nothing about project
    layout, so it arrives with no ProjectSpec. Scaffolding used to read .entry
    off None."""
    _cpp()
    from purecoder.scaffold import scaffold_project

    assert _learn(FakeModel(completions=DRAFTS), store)["ok"]
    out = store.parent / "proj"
    res = scaffold_project(None, "x", "y", outdir=str(out),
                           spec=L.get("cpplike"), verbose=False)
    assert not res["ok"]
    assert "no project layout" in res["error"]
    assert not out.exists()


def test_a_refused_candidate_carries_the_diagnostic_not_just_the_verdict(store):
    """The refusal names which probe failed; the probe's detail says why, and it
    is the compiler's own message. The CLI prints it, so it has to be there."""
    _cpp()
    drafts = list(DRAFTS)
    # Defines PC_CHECK, so drafting succeeds -- but calls something that does
    # not exist, so probe 1 fails carrying the compiler's own message. A helper
    # that merely cannot FAIL produces failing probes with empty detail, since
    # there the run succeeded and there is nothing to report.
    drafts[0] = ("static int pc_checks = 0;\n"
                 "#define PC_CHECK(x) do { no_such_function(x); pc_checks++; } "
                 "while (0)\n")
    res = _learn(FakeModel(completions=drafts), store)
    assert not res["ok"]
    failed = [p for p in res["probes"] if not p.ok]
    assert failed, "a refusal with no failing probe explains nothing"
    assert any("no_such_function" in p.detail for p in failed), \
        "the compiler said why and the refusal dropped it"


def test_every_refusal_path_reports_probes_even_with_none_to_report(store):
    """The CLI iterates res["probes"] unconditionally, so the key must exist on
    the paths that never got as far as probing."""
    declined = bootstrap.learn_language(
        FakeModel(completions=DRAFTS), "cpplike", ".cpp", docs_dir=None,
        retrieve=lambda q: "DOCS", confirm=lambda b, r: False, verbose=False,
        live_check=False)
    refused = _learn(FakeModel(completions=DRAFTS), store, name="python")
    for res in (declined, refused):
        assert res["probes"] == []


def test_each_fixture_section_is_unfenced_separately():
    """Observed live on OCaml: the model fences every snippet, so stripping the
    outermost pair once leaves ``` markers embedded mid-fixture. The language
    then failed to parse and the diagnostic pointed at the syntax error rather
    than at the cause."""
    pc = FakeModel(completions=[
        "```ocaml\nlet add x y = x + y\n```\n@@WRONG@@\n"
        "```ocaml\nlet add x y = x - y\n```\n@@TESTS@@\n"
        "```ocaml\npc_check (add 1 2 = 3);\n```\n@@EMPTY@@\n"
        "```ocaml\n()\n```\n@@ALWAYS_FAILS@@\n"
        "```ocaml\npc_check false;\n```"])
    fx = bootstrap.draft_fixture(pc, "ocaml", "PRE", "POST", "pc_check")
    for section in (fx.correct, fx.wrong, fx.tests, fx.empty, fx.always_fails):
        assert "```" not in section, f"fence survived in {section!r}"
    assert fx.correct == "let add x y = x + y"


def test_the_fixture_prompt_shows_the_tail_that_will_run_the_tests():
    """The tests and the epilogue have to agree on shape. On OCaml the tail
    called `pc_tests ()` while the tests were top-level statements, so the
    harness could not compile however good either half was alone."""
    pc = FakeModel(completions=["C\n@@WRONG@@\nW\n@@TESTS@@\nT\n"
                                "@@EMPTY@@\nE\n@@ALWAYS_FAILS@@\nF"])
    bootstrap.draft_fixture(pc, "zig", "THE PREAMBLE", "THE EPILOGUE", "PC_CHECK")
    _system, user = pc.calls[0]
    assert "THE EPILOGUE" in user
    assert "THE PREAMBLE" in user


def test_a_dot_slash_prefixed_placeholder_is_normalised_not_refused():
    """`./{bin}` is a near-universal habit and it is broken here: {bin} expands
    to an absolute path, so the `./` resolves it against the working directory.
    It has exactly one correct reading, so it is fixed rather than refused --
    two of four live OCaml drafts wrote it."""
    build, run, _ = bootstrap.draft_commands(
        FakeModel(completions=["BUILD: ocamlc -o {bin} {src}\n"
                               "RUN: ./{bin}\nTOOLCHAIN: ocamlc"]),
        "ocaml", ".ml", "DOCS")
    assert run == ("{bin}",)
    assert build == ("ocamlc", "-o", "{bin}", "{src}")


def test_the_epilogue_prompt_says_where_the_tests_already_are():
    """Two of the three worked examples need an entry point, so the model
    generalised the majority shape onto OCaml and emitted `pc_tests ()` for a
    language that runs top-level statements. The invariant is now stated."""
    pc = FakeModel(completions=["TAIL"])
    bootstrap.draft_epilogue(pc, "ocaml", "PRE", "DOCS")
    _system, user = pc.calls[0]
    assert "already placed" in user
    assert "top-level statements in order" in user


@pytest.mark.parametrize("raw,want", [
    ("```ocaml\nlet x = 1\n```", "let x = 1"),          # the normal case
    ("let x = 1\n;;```", "let x = 1\n;;"),              # fence welded to code
    ("```ocaml\nlet x = 1", "let x = 1"),               # opened, never closed
    ("```\nlet x = 1\n```\nmore\n```", "let x = 1\n\nmore"),   # several
    ("let x = 1", "let x = 1"),                         # nothing to do
])
def test_fence_markers_are_removed_wherever_they_appear(raw, want):
    """Three of these reached the OCaml compiler as a syntax error pointing at
    the fence rather than at anything the model got wrong. A triple backtick is
    not valid syntax in any language the executor runs, so it is always markup."""
    assert bootstrap.unfence(raw) == want
