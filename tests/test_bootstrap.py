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


def test_the_writer_prompt_is_templated_too():
    """The last field `learn` could not produce. It is filled in from what the
    probes already proved -- the helper's name and the shape of the tail -- for
    the same reason the tester prompt is: a model writing its own instructions
    measured worst."""
    text = bootstrap.writer_system_for("PC_CHECK", tail_entry=True)
    assert "PC_CHECK" in text
    assert "wrapper class" in text


def test_a_tail_that_runs_the_tests_is_the_entry_point_the_writer_must_not_write():
    """C++'s tail is `int main() { pc_tests(); ... }` -- an entry point that
    calls a name the tests define. A writer that emits its own main breaks the
    assembly, and the fix loop sees only a linker error."""
    harness = bootstrap.Harness(
        preamble="static int pc_checks = 0;",
        check_call="PC_CHECK",
        epilogue="int main() { pc_tests(); return 0; }",
        fixture=bootstrap.Fixture("int add(int,int);", "int add(int,int);",
                                  "void pc_tests(){ PC_CHECK(1); }",
                                  "void pc_tests(){ }",
                                  "void pc_tests(){ PC_CHECK(0); }"))
    assert bootstrap.tail_provides_the_entry_point(harness)
    assert "entry point" in bootstrap.writer_system_for("PC_CHECK",
                                                        tail_entry=True)


def test_a_top_level_harness_says_so_instead():
    """JavaScript's tail calls nothing the tests define -- the statements have
    already run. The demand is the same shape, but the reason differs, and
    telling the writer the tail will call it would be a lie."""
    harness = bootstrap.Harness(
        preamble="let pcChecks = 0;",
        check_call="PC_CHECK",
        epilogue="if (pcChecks < 1) { process.exit(2); }",
        fixture=bootstrap.Fixture("function add(a,b){return a+b;}",
                                  "function add(a,b){return a-b;}",
                                  "PC_CHECK(add(1,2)===3, 'add');", "",
                                  "PC_CHECK(false, 'x');"))
    assert not bootstrap.tail_provides_the_entry_point(harness)
    assert "top level" in bootstrap.writer_system_for("PC_CHECK",
                                                      tail_entry=False)


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
    """One drafting attempt. The retry path has its own tests below; these
    would otherwise exhaust the scripted queue on the redraft."""
    kw.setdefault("max_retries", 1)
    # The layout is drafted and probed separately; these tests are about the
    # harness, and the project path has its own below.
    kw.setdefault("want_project", False)
    return bootstrap.learn_language(
        pc, name, ".cpp", docs_dir=None, retrieve=lambda q: "DOCS",
        confirm=lambda b, r, p=None: True, verbose=False, live_check=False, **kw)


def test_a_language_that_passes_every_probe_is_saved(store):
    _cpp()
    res = _learn(FakeModel(completions=DRAFTS), store)
    assert res["ok"], res["error"]
    assert (store / "cpplike.json").is_file()
    assert L.get("cpplike").check_call == "PC_CHECK"
    assert L.get("cpplike").probe == ("g++", "--version")


def test_a_learned_language_records_the_docs_it_came_from(store):
    """So that generating in it later can read the same documentation, instead
    of ingesting the directory a second time."""
    _cpp()
    assert _learn(FakeModel(completions=DRAFTS), store,
                  docs_store="cpplike")["ok"]
    assert L.get("cpplike").docs_store == "cpplike"


def test_a_learned_language_tells_the_writer_what_its_harness_defines(store):
    """The field `learn` used to leave empty. A hand-written entry gets one
    when a person judged it necessary; a drafted entry gets one because nobody
    judged anything, and the failure it prevents is silent."""
    _cpp()
    assert _learn(FakeModel(completions=DRAFTS), store)["ok"]
    demand = L.get("cpplike").writer_system
    assert "PC_CHECK" in demand
    assert "entry point" in demand


def test_a_language_learned_without_an_index_points_at_nothing(store):
    """The caller owns the index. A run that never built one must not leave a
    spec claiming there is one."""
    _cpp()
    assert _learn(FakeModel(completions=DRAFTS), store)["ok"]
    assert L.get("cpplike").docs_store == ""


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
        retrieve=lambda q: "DOCS", confirm=lambda b, r, p=None: False, verbose=False,
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
        confirm=lambda b, r, p=None: True, verbose=False, live_check=True)
    assert res["ok"], res["error"]
    assert pc.code_kwargs[0]["language"] == "cpplike"
    # The demand is not merely stored on the spec: the live round is the first
    # thing that writes code in this language, and it is where a shape the
    # harness cannot assemble would first appear.
    assert "PC_CHECK" in pc.code_kwargs[0]["writer_system"]


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
                                 confirm=lambda b, r, p=None: True, verbose=False,
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
        confirm=lambda b, r, p=None: True, verbose=False, live_check=True)
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
        retrieve=lambda q: "DOCS", confirm=lambda b, r, p=None: False, verbose=False,
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


# ---- redrafting ----------------------------------------------------------

def test_a_failed_probe_is_redrafted_with_the_diagnostic(store):
    """Every other layer feeds its error back and tries again; this one used to
    refuse on the first bad draft. A live OCaml run reached four of five probes
    on a single malformed snippet."""
    _cpp()
    broken = list(DRAFTS)
    broken[0] = ("static int pc_checks = 0;\n#define PC_CHECK(x) do { "
                 "no_such_function(x); pc_checks++; } while (0)\n")
    pc = FakeModel(completions=[*broken, *DRAFTS])
    res = bootstrap.learn_language(
        pc, "cpplike", ".cpp", docs_dir=None, retrieve=lambda q: "DOCS",
        confirm=lambda b, r, p=None: True, verbose=False, live_check=False,
        max_retries=2, want_project=False)
    assert res["ok"], res["error"]
    assert (store / "cpplike.json").is_file()


def test_the_redraft_prompt_carries_the_compiler_message(store):
    _cpp()
    broken = list(DRAFTS)
    broken[0] = ("static int pc_checks = 0;\n#define PC_CHECK(x) do { "
                 "no_such_function(x); pc_checks++; } while (0)\n")
    pc = FakeModel(completions=[*broken, *DRAFTS])
    bootstrap.learn_language(
        pc, "cpplike", ".cpp", docs_dir=None, retrieve=lambda q: "DOCS",
        confirm=lambda b, r, p=None: True, verbose=False, live_check=False,
        max_retries=2, want_project=False)
    redraft = pc.calls[5][1]          # the first prompt of the second attempt
    assert "previous attempt was rejected" in redraft
    assert "no_such_function" in redraft, "the diagnostic never reached the model"


def test_the_commands_are_confirmed_once_however_many_redrafts(store):
    """confirm_commands reads stdin. Inside the retry body it would prompt per
    attempt -- and the commands are not what a probe failure implicates, since
    the build ran."""
    _cpp()
    asked = []
    broken = list(DRAFTS)
    broken[0] = "static int pc_checks = 0;\n#define PC_CHECK(x) do { nope(x); } while (0)\n"
    bootstrap.learn_language(
        FakeModel(completions=[*broken, *broken, *DRAFTS]), "cpplike", ".cpp",
        docs_dir=None, retrieve=lambda q: "DOCS",
        confirm=lambda b, r, p=None: asked.append((b, r)) or True,
        verbose=False, live_check=False, max_retries=3, want_project=False)
    assert len(asked) == 1


def test_giving_up_still_names_the_probe_that_failed(store):
    _cpp()
    broken = list(DRAFTS)
    # Compiles cleanly and counts, but cannot fail -- so the probe it trips is
    # the one that matters, not an incidental build error.
    broken[0] = ("#include <cstdio>\nstatic int pc_checks = 0;\n"
                 "#define PC_CHECK(x) do { pc_checks++; } while (0)\n")
    res = bootstrap.learn_language(
        FakeModel(completions=[*broken, *broken]), "cpplike", ".cpp",
        docs_dir=None, retrieve=lambda q: "DOCS", confirm=lambda b, r, p=None: True,
        verbose=False, live_check=False, max_retries=2, want_project=False)
    assert not res["ok"]
    assert "wrong implementation fails" in res["error"], \
        "giving up must name the probe, not the exhausted retry"


def test_feedback_is_empty_when_nothing_failed():
    assert bootstrap.probe_feedback(
        [bootstrap.Probe("a", True, ""), bootstrap.Probe("b", True, "")]) == ""


def test_feedback_says_so_when_a_probe_failed_by_succeeding():
    """A harness that cannot fail produces failing probes with empty detail --
    the run succeeded and there is nothing for the compiler to say."""
    text = bootstrap.probe_feedback(
        [bootstrap.Probe("wrong implementation fails", False, "")])
    assert "succeeded when it should have failed" in text


def test_a_tail_calling_something_nothing_defines_is_named():
    """OCaml's tail called `pc_tests ()` while the tests were bare top-level
    statements. Feeding back "Unbound value pc_tests" was not enough -- the
    model read it as "define pc_tests" rather than "stop calling it"."""
    harness = bootstrap.Harness(
        preamble="let pc_checks = ref 0", check_call="pc_check",
        epilogue="let () =\n  pc_tests ();\n  if !pc_checks < 1 then exit 2",
        fixture=bootstrap.Fixture("let add x y = x+y", "let add x y = x-y",
                                  "pc_check (add 1 2 = 3);", "()",
                                  "pc_check false"))
    assert bootstrap.dangling_calls(harness) == ["pc_tests"]
    hint = bootstrap.shape_feedback(harness)
    assert "'pc_tests'" in hint
    assert "top-level statements in order" in hint


def test_a_coherent_harness_gets_no_shape_hint():
    """C++ genuinely needs the indirection, and its tests define pc_tests. The
    hint must not fire on the shape that is correct."""
    harness = bootstrap.Harness(
        preamble="static int pc_checks = 0;\n#define PC_CHECK(x) do {} while (0)",
        check_call="PC_CHECK",
        epilogue="int main() { pc_tests(); return 0; }",
        fixture=bootstrap.Fixture("int add(int,int);", "int add(int,int);",
                                  "void pc_tests(){ PC_CHECK(1); }",
                                  "void pc_tests(){ }",
                                  "void pc_tests(){ PC_CHECK(0); }"))
    assert bootstrap.dangling_calls(harness) == []
    assert bootstrap.shape_feedback(harness) == ""


# ---- the project layout --------------------------------------------------

PROJECT_DRAFTS = [
    ("ENTRY: main.cpp\nINSTALL: @echo nothing to install\n"
     "RUN: g++ -std=c++17 -w main.cpp -o main && ./main\n"
     "TEST: g++ -std=c++17 -w main.cpp -o main && ./main\n"
     "CLEAN: rm -f main"),
    "int main() { return 0; }",
]


def test_a_project_draft_becomes_a_spec():
    pc = FakeModel(completions=[PROJECT_DRAFTS[0]])
    project = bootstrap.draft_project(pc, "cpplike", ".cpp", "DOCS")
    assert project.entry == "main.cpp"
    assert project.test.startswith("g++")
    assert "nothing to install" in project.install


def test_an_entry_filename_that_is_a_path_is_refused():
    """The scaffolder writes this name into the project directory. A path would
    escape it."""
    draft = PROJECT_DRAFTS[0].replace("ENTRY: main.cpp", "ENTRY: ../../main.cpp")
    with pytest.raises(ValueError, match="a path, not a name"):
        bootstrap.draft_project(FakeModel(completions=[draft]), "cpplike",
                                ".cpp", "DOCS")


def test_an_entry_filename_with_the_wrong_suffix_is_refused():
    draft = PROJECT_DRAFTS[0].replace("ENTRY: main.cpp", "ENTRY: main.txt")
    with pytest.raises(ValueError, match="does not end in"):
        bootstrap.draft_project(FakeModel(completions=[draft]), "cpplike",
                                ".cpp", "DOCS")


def test_a_project_draft_missing_a_target_is_refused():
    draft = "ENTRY: main.cpp\nINSTALL: true\nCLEAN: true"
    with pytest.raises(ValueError, match="RUN"):
        bootstrap.draft_project(FakeModel(completions=[draft]), "cpplike",
                                ".cpp", "DOCS")


def test_an_entry_stub_is_omitted_when_the_language_needs_none():
    """Python and JavaScript run a file of plain definitions. Emitting the word
    `none` into the source would be a syntax error in every language."""
    project = bootstrap.draft_project(FakeModel(completions=[PROJECT_DRAFTS[0]]),
                                      "cpplike", ".cpp", "DOCS")
    assert bootstrap.draft_entry_stub(FakeModel(completions=["none"]), "js",
                                      "DOCS", project) == ""
    assert bootstrap.draft_entry_stub(FakeModel(completions=["None."]), "js",
                                      "DOCS", project) == ""


def test_a_layout_that_builds_and_runs_passes(store):
    """Against the real C++ project spec and a real toolchain -- the same bar
    the hand-written entries meet."""
    _cpp()
    ok, probes = bootstrap.probe_project(L.get("c++"), CPP_FIXTURE)
    assert ok, [p for p in probes if not p.ok]
    assert len(probes) == 2


def test_a_test_recipe_that_never_touches_the_source_is_rejected():
    """The probe that matters. `test: true` builds nothing, runs nothing, and
    exits 0 -- so a one-sided probe would call it a working layout."""
    _cpp()
    spec = dataclasses.replace(
        L.get("c++"), project=dataclasses.replace(L.get("c++").project,
                                                  test="@echo pretending"))
    ok, probes = bootstrap.probe_project(spec, CPP_FIXTURE)
    assert not ok
    assert "a project of broken code fails" in [p.name for p in probes if not p.ok]


def test_a_layout_missing_the_entry_point_is_rejected():
    """A C++ project of plain functions compiles clean in the sandbox, where
    the harness supplies main(), and then fails to link on disk. Observed live,
    which is why entry_stub exists at all."""
    _cpp()
    spec = dataclasses.replace(
        L.get("c++"), project=dataclasses.replace(L.get("c++").project,
                                                  entry_stub=""))
    ok, probes = bootstrap.probe_project(spec, CPP_FIXTURE)
    assert not ok
    assert "builds and runs" in [p.name for p in probes if not p.ok][0]


def test_a_proven_layout_is_attached_to_the_language(store):
    _cpp()
    res = _learn(FakeModel(completions=[*DRAFTS, *PROJECT_DRAFTS]), store,
                 want_project=True)
    assert res["ok"], res["error"]
    assert L.get("cpplike").project is not None
    assert L.get("cpplike").project.entry == "main.cpp"


def test_a_layout_that_does_not_build_costs_only_itself(store):
    """The layout is a separate claim from "this language can be generated and
    validated". Losing the second because the first failed would throw away
    what the harness probes already proved."""
    _cpp()
    drafts = list(PROJECT_DRAFTS)
    drafts[0] = drafts[0].replace("TEST: g++ -std=c++17 -w main.cpp -o main "
                                  "&& ./main", "TEST: @echo pretending")
    res = _learn(FakeModel(completions=[*DRAFTS, *drafts]), store,
                 want_project=True)
    assert res["ok"], "a bad layout must not sink a proven language"
    assert L.get("cpplike").project is None
    assert (store / "cpplike.json").is_file()


def test_the_project_recipes_are_shown_before_anything_runs(capsys):
    """They become a Makefile the user runs, and the layout probe runs
    `make test` against them. Same boundary as the build and run commands."""
    project = bootstrap.draft_project(FakeModel(completions=[PROJECT_DRAFTS[0]]),
                                      "cpplike", ".cpp", "DOCS")
    bootstrap.confirm_commands(("g++",), ("{bin}",), project, ask=lambda _: "n")
    out = capsys.readouterr().out
    assert "main.cpp" in out
    assert "make test" in out
    assert "never run by purecoder" in out, "install is not probed; say so"


def test_a_recipe_may_chain_but_not_pipe_or_redirect():
    """A make recipe IS a shell line, so the argv discipline the build and run
    commands get cannot apply -- `g++ ... && ./main` needs `&&`. This is
    therefore the one place drafted output reaches a shell, and the shell's
    other powers are denied by name."""
    ok = "g++ -std=c++17 main.cpp -o main && ./main"
    assert bootstrap._check_recipe("test", ok, "main.cpp") == ok

    for bad in ("cat main.cpp | sh",
                "g++ main.cpp > /dev/null",
                "g++ main.cpp; rm -rf ~",
                "g++ `whoami`.cpp",
                "g++ $(id).cpp"):
        with pytest.raises(ValueError, match="shell features"):
            bootstrap._check_recipe("test", bad, "main.cpp")


def test_a_backgrounded_recipe_is_refused():
    """`make test` would exit before the program had run, so the probe would
    be reading the exit code of nothing."""
    with pytest.raises(ValueError, match="backgrounds"):
        bootstrap._check_recipe("test", "./main &", "main.cpp")


def test_a_run_recipe_that_never_names_the_entry_is_refused_before_it_runs():
    """The two-sided probe catches this by EXECUTING it. Catching it at draft
    time means a recipe that does not touch the project never reaches a shell
    in the first place."""
    with pytest.raises(ValueError, match="never names main.cpp"):
        bootstrap._check_recipe("test", "@echo pretending", "main.cpp")


def test_install_and_clean_are_checked_but_need_not_name_the_entry():
    """`rm -rf build` is a legitimate clean and names nothing, and install is
    never executed by purecoder at all -- but both still land in a Makefile the
    user runs."""
    assert bootstrap._check_recipe("clean", "rm -rf build") == "rm -rf build"
    with pytest.raises(ValueError, match="shell features"):
        bootstrap._check_recipe("install", "curl http://x | sh")


def test_every_hand_written_recipe_passes_the_drafted_bar():
    """Calibration, not decoration: a rule the built-in entries could not meet
    would be a rule about this project's taste rather than about safety.
    Python's `pytest` is the one exception and is noted as such -- a drafted
    single-file layout has no test files for it to find."""
    for name in L.names():
        spec = L.get(name)
        if spec.project is None:
            continue
        bootstrap._check_recipe("install", spec.project.install)
        bootstrap._check_recipe("clean", spec.project.clean)
        bootstrap._check_recipe("run", spec.project.run, spec.project.entry)
        if name != "python":
            bootstrap._check_recipe("test", spec.project.test,
                                    spec.project.entry)


def test_the_layout_prompt_asks_its_own_question():
    """"Compile one file" and "lay a project out" are different questions;
    reusing the commands context would be convenience, not design."""
    assert "Makefile" in bootstrap.QUERIES["layout"]
    assert bootstrap.QUERIES["layout"] != bootstrap.QUERIES["commands"]


# ---- prose that reached the compiler -------------------------------------

OBSERVED = (
    "let pc_checks = ref 0\n"
    "let pc_check cond =\n"
    "  if not cond then begin\n"
    "    prerr_endline \"CHECK FAILED\";\n"
    "    exit 1\n"
    "  end else incr pc_checks\n"
    "\n"
    "This OCaml code declares a mutable reference `pc_checks` initialized to "
    "zero. The function `pc_check` takes a boolean expression as an argument. "
    "If the expression evaluates to false, it prints \"CHECK FAILED:\" "
    "followed by the string representation of the condition.\n"
)


def test_an_explanation_appended_to_drafted_code_is_removed():
    """Observed live. Every drafting prompt says "no prose", and the model
    wrote its explanation anyway; `unfence` strips fences only, so the
    paragraph reached ocamlc as `Error: Syntax error` on line 14. Two probes
    failed for it, the redraft was handed the compiler's complaint about its
    own English, and the language did not register."""
    cleaned = bootstrap.strip_prose(OBSERVED)
    assert "This OCaml code declares" not in cleaned
    assert "let pc_check cond =" in cleaned
    assert "incr pc_checks" in cleaned


@pytest.mark.parametrize("line", [
    "let () = if !pc_checks < 1 then (prerr_endline \"no checks ran\"; exit 2)",
    "#define PC_CHECK(x) do { if (!(x)) { std::exit(1); } } while (0)",
    "    return a + b;",
    "// adds two numbers and returns the result",
    "(* the number of checks that have run so far *)",
    "macro_rules! pc_check {",
])
def test_code_and_comments_survive(line):
    """A filter that eats code is far worse than the prose it removes -- the
    harness would fail a probe for a line nobody can see."""
    assert line in bootstrap.strip_prose(line)
