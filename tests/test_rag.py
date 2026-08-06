"""Chunking, retrieval and the retrieve-when-needed gate.

The Embedder is injectable precisely so this runs without a GPU or a model
download -- a deterministic fake stands in for sentence-transformers.
"""

import json
import os

import numpy as np
import pytest
from conftest import FakeEmbedder

from purecoder import rag
from purecoder.rag import (
    DocStore,
    StoreError,
    chunk_file,
    chunk_markdown,
    chunk_python,
    plan_ingest,
    render_plan,
    retrieve_context,
    tokenize,
)

# ---- markdown chunking ---------------------------------------------------

def test_markdown_splits_on_headings():
    text = "# One\nalpha\n\n## Two\nbeta\n\n# Three\ngamma\n"
    chunks = chunk_markdown(text, "doc.md")
    assert len(chunks) == 3
    assert all(src == "doc.md" for _, src in chunks)
    assert chunks[0][0].startswith("# One")


def test_markdown_splits_oversized_sections_with_overlap():
    text = "# Big\n" + ("word " * 1000)
    chunks = chunk_markdown(text, "doc.md", max_chars=200, overlap=50)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c, _ in chunks)


def test_markdown_drops_empty_sections():
    assert chunk_markdown("\n\n   \n", "doc.md") == []


def test_markdown_terminates_when_overlap_exceeds_the_window():
    """Stride was `max_chars - overlap`. At or below zero the loop never
    advances: no output, no error, the process simply stops responding. A
    caller narrowing max_chars without touching the default overlap of 150 is
    all it takes."""
    chunks = chunk_markdown("# Big\n" + ("word " * 200), "doc.md", max_chars=100)
    assert chunks


# ---- code-aware chunking -------------------------------------------------

SOURCE = '''\
import os

CONST = 1


def alpha(x):
    """doc"""
    return x


class Thing:
    def method_a(self):
        return 1

    def method_b(self):
        return 2
'''


def test_python_chunks_on_function_and_class_boundaries():
    chunks = chunk_python(SOURCE, "mod.py")
    labels = [c.splitlines()[0] for c, _ in chunks]
    assert any("function alpha" in label for label in labels)
    assert any("class Thing" in label for label in labels)
    assert any("module top-level" in label for label in labels)


def test_python_keeps_whole_function_bodies_together():
    chunks = chunk_python(SOURCE, "mod.py")
    fn = next(c for c, _ in chunks if "function alpha" in c)
    assert "def alpha(x):" in fn
    assert "return x" in fn


def test_python_splits_large_classes_into_methods():
    chunks = chunk_python(SOURCE, "mod.py", max_chars=20)
    labels = [c.splitlines()[0] for c, _ in chunks]
    assert any("method Thing.method_a" in label for label in labels)
    assert any("method Thing.method_b" in label for label in labels)


def test_python_captures_leading_comments():
    src = "# explains why\ndef alpha():\n    return 1\n"
    chunk = chunk_python(src, "mod.py")[0][0]
    assert "# explains why" in chunk


def test_python_falls_back_to_markdown_on_syntax_error():
    chunks = chunk_python("def broken(:\n", "mod.py")
    assert chunks                      # degraded, not crashed


def test_chunk_file_routes_by_extension():
    assert any("function alpha" in c for c, _ in chunk_file("mod.py", SOURCE))
    assert not any("function" in c for c, _ in chunk_file("doc.md", "# H\ntext\n"))


# ---- the lexical signal --------------------------------------------------

def test_a_dotted_name_is_tokenised_whole_and_in_parts():
    assert tokenize("Printf.eprintf fmt") == {"printf.eprintf", "printf",
                                              "eprintf", "fmt"}


def test_snake_case_is_left_alone():
    """Splitting it would make `check` a token in every harness chunk, which
    is worth nothing, and the dotted split already covers qualified names."""
    assert tokenize("pc_check(x)") == {"pc_check", "x"}


@pytest.fixture
def api_store(tmp_path):
    """Docs where the embedder is blind: the fake scores only alpha/beta/gamma,
    so an API-symbol query has cosine 0 everywhere and the lexical signal is
    the only thing that can find anything. That is the real case in
    miniature -- embeddings are worst at exactly the queries this tool gets
    most."""
    d = tmp_path / "docs"
    d.mkdir()
    # "the" appears in both on purpose: it is the ubiquitous-token case.
    (d / "printf.md").write_text(
        "# Output\nPrintf.eprintf writes the formatted string to stderr.\n")
    (d / "stdlib.md").write_text(
        "# Stdlib\nThe exit function ends the program with a status code.\n")
    s = DocStore(FakeEmbedder(), path=str(tmp_path / "idx"))
    s.ingest_dir(str(d), verbose=False)
    return s


def test_an_exact_symbol_retrieves_its_page_when_cosine_is_blind(api_store):
    assert [src for _, src, _ in api_store.search("Printf.eprintf")] == ["printf.md"]
    # Nothing was found by similarity: every cosine is 0, so the hit is the
    # lexical signal's alone.
    assert all(cosine == 0.0
               for _, _, cosine, _ in api_store.explain("Printf.eprintf"))


def test_the_bare_name_finds_the_qualified_one(api_store):
    assert [src for _, src, _ in api_store.search("eprintf")] == ["printf.md"]


def test_a_word_the_corpus_never_saw_matches_nothing(api_store):
    """No division by zero, and no trivial 1.0 either: a query made only of
    unknown tokens is not a match for everything."""
    assert api_store.search("mutex") == []


def test_a_query_of_only_ubiquitous_words_clears_nothing(api_store):
    """`the` is in every chunk, so it distinguishes nothing and must weigh
    nothing. Weighted the other way, a stopword query scores near 1 against
    any chunk containing it and walks straight through the gate."""
    assert api_store.search("the") == []


def test_explain_separates_the_two_signals(api_store):
    source, combined, cosine, lexical = api_store.explain("Printf.eprintf")[0]
    assert source == "printf.md"
    assert cosine == 0.0 and lexical == 1.0
    assert combined == pytest.approx(0.5)


def test_only_chunks_holding_a_query_token_are_scored(api_store):
    """The lexical index is inverted -- token -> the chunks holding it -- so a
    rare symbol touches the chunks that contain it and nothing else. Walking
    every chunk gave identical answers and cost 400x more at 7500 chunks.
    """
    scores = api_store._lexical("Printf.eprintf")
    assert list(scores).count(0.0) == len(api_store.chunks) - 1
    assert scores.max() == 1.0


def test_a_token_the_corpus_lacks_reaches_no_postings(api_store):
    """The lookup must miss cleanly rather than raise on an absent key."""
    assert not api_store._lexical("mutex").any()


def test_the_store_carries_the_names_the_docs_use(api_store):
    assert "Printf.eprintf" in api_store.symbols


def test_the_symbol_library_is_not_built_until_something_needs_it(api_store):
    """Its only consumer is the did-you-mean hint, reached solely from a failed
    run. A generation that works first time should not pay a full pass over
    the chunks for it."""
    assert api_store._symbols is None
    assert api_store.symbols                 # first use builds it
    assert api_store._symbols is not None


def test_the_symbol_library_survives_a_round_trip(api_store):
    """Derived from the chunks like the lexical index, and for the same
    reason: a third file on disk is a third thing that can drift."""
    api_store.save()
    reloaded = DocStore(FakeEmbedder(), path=api_store.path).load()
    assert reloaded.symbols == api_store.symbols


def test_the_review_names_the_modules_it_found(tmp_path):
    """The fastest way to tell a docs directory that covers the API from one
    that does not: if these are not the library's modules, neither is the
    index."""
    d = tmp_path / "docs"
    d.mkdir()
    (d / "api.md").write_text("# API\nPrintf.eprintf and Printf.sprintf\n")
    assert "Printf (2)" in render_plan(plan_ingest(str(d)))


def test_the_lexical_index_survives_a_round_trip(api_store):
    """It is rebuilt from the chunks rather than stored, so the scores after a
    load must equal the scores before a save -- otherwise `ask` ranks
    differently from `ingest` and nothing says so."""
    before = api_store.search("Printf.eprintf")
    api_store.save()
    after = DocStore(FakeEmbedder(), path=api_store.path).load().search(
        "Printf.eprintf")
    assert before == after


# ---- store + gate, with a fake embedder ---------------------------------

@pytest.fixture
def store(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "a.md").write_text("# Alpha\nalpha alpha alpha\n")
    (d / "b.md").write_text("# Beta\nbeta beta beta\n")
    s = DocStore(FakeEmbedder(), path=str(tmp_path / "idx"))
    s.ingest_dir(str(d), verbose=False)
    return s


def test_ingest_indexes_every_file(store):
    assert len(store.chunks) == 2
    assert store.vectors.shape[0] == 2


def test_ingest_raises_when_nothing_matches(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError):
        DocStore(FakeEmbedder(), path=str(tmp_path / "x")).ingest_dir(str(empty))


def test_search_ranks_the_relevant_chunk_first(store):
    hits = store.search("alpha", k=2, min_score=0.0)
    assert "alpha" in hits[0][0].lower()


def test_gate_returns_nothing_below_threshold(store):
    """The retrieve-when-needed gate: irrelevant query -> inject nothing."""
    assert store.search("delta", k=3, min_score=0.3) == []
    assert retrieve_context(store, "delta") == ""


def test_gate_injects_context_when_relevant(store):
    ctx = retrieve_context(store, "alpha")
    assert ctx.startswith("Relevant documentation:")
    assert "a.md" in ctx


def test_retrieve_context_returns_nothing_when_no_block_fits(store):
    """A header with no documentation under it is still truthy.

    `cmd_ask` reads the return value as "was anything injected?", so this used
    to print "[rag] injected 25 chars" and hand the model the sentence
    "Relevant documentation:" followed by nothing at all.
    """
    assert retrieve_context(store, "alpha", max_chars=10) == ""


def test_an_oversized_top_hit_does_not_discard_the_ones_below_it(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    # Ranked apart on purpose: big.md is pure alpha and scores 1.0, small.md
    # dilutes with beta and comes second. Only the ordering matters here.
    (d / "big.md").write_text("# Alpha\n" + "alpha " * 100)
    (d / "small.md").write_text("# Alpha two\nalpha beta\n")
    s = DocStore(FakeEmbedder(), path=str(tmp_path / "idx"))
    s.ingest_dir(str(d), verbose=False)

    ctx = retrieve_context(s, "alpha", k=2, max_chars=120)
    assert "small.md" in ctx        # the big one is skipped, not the rest


# ---- the index on disk ---------------------------------------------------

def test_save_and_load_round_trip(store):
    store.save()
    reloaded = DocStore(FakeEmbedder(), path=store.path).load()
    assert reloaded.chunks == store.chunks
    assert reloaded.sources == store.sources
    assert np.allclose(reloaded.vectors, store.vectors)


def test_load_refuses_an_index_whose_counts_disagree(store):
    """The one failure retrieval must never have: silent, and wrong.

    `search` looks a chunk up by its vector's row index. Drop a chunk from the
    metadata and nothing raises -- it pairs each chunk with the next one's
    source and score, and injects documentation under a filename it never came
    from.
    """
    store.save()
    meta_path = store.path + ".json"
    with open(meta_path) as f:
        meta = json.load(f)
    meta["chunks"] = meta["chunks"][:-1]
    with open(meta_path, "w") as f:
        json.dump(meta, f)

    with pytest.raises(StoreError, match="inconsistent"):
        DocStore(FakeEmbedder(), path=store.path).load()


def test_load_refuses_an_index_built_by_another_model(store):
    """Vectors from two models are not comparable, and at equal dimensions the
    mismatch is invisible at query time -- the scores are simply noise."""
    store.save()
    meta_path = store.path + ".json"
    with open(meta_path) as f:
        meta = json.load(f)
    meta["embedder"] = "BAAI/bge-large-en-v1.5"
    with open(meta_path, "w") as f:
        json.dump(meta, f)

    named = FakeEmbedder()
    named.model_name = "BAAI/bge-small-en-v1.5"
    with pytest.raises(StoreError, match="built with"):
        DocStore(named, path=store.path).load()


def test_an_unnamed_embedder_can_still_read_the_index(store):
    """The identity check compares only when both sides name themselves, so an
    injectable fake -- the reason this whole file runs without a GPU -- stays
    usable."""
    store.save()
    assert DocStore(FakeEmbedder(), path=store.path).load().chunks


def test_an_index_predating_model_tracking_loads_with_a_note(store, capsys):
    """Real indexes exist on disk from before the field. Unverifiable is not
    the same as corrupt: say so once rather than bricking them."""
    store.save()
    meta_path = store.path + ".json"
    with open(meta_path) as f:
        meta = json.load(f)
    del meta["embedder"]
    with open(meta_path, "w") as f:
        json.dump(meta, f)

    assert DocStore(FakeEmbedder(), path=store.path).load().chunks
    assert "predates model tracking" in capsys.readouterr().out


def test_load_names_a_missing_index_instead_of_raising_oserror(tmp_path):
    with pytest.raises(StoreError, match="no index at"):
        DocStore(FakeEmbedder(), path=str(tmp_path / "absent")).load()


def test_load_names_a_corrupt_index(store):
    store.save()
    with open(store.path + ".json", "w") as f:
        f.write("{not json")
    with pytest.raises(StoreError, match="not a readable index"):
        DocStore(FakeEmbedder(), path=store.path).load()


def test_saving_before_ingesting_is_refused(tmp_path):
    """np.save writes None as a pickled object and np.load then refuses it with
    a message about pickles that names nothing the user did."""
    with pytest.raises(StoreError, match="ingest before saving"):
        DocStore(FakeEmbedder(), path=str(tmp_path / "x")).save()


def test_save_leaves_no_partial_files_behind(store, tmp_path):
    store.save()
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_search_refuses_a_query_of_the_wrong_dimension(store):
    class WiderEmbedder(FakeEmbedder):
        def embed_query(self, text):
            return np.zeros(768)

    store.embedder = WiderEmbedder()
    with pytest.raises(StoreError, match="different model"):
        store.search("alpha")


def test_search_on_empty_store_returns_nothing(tmp_path):
    assert DocStore(FakeEmbedder(), path=str(tmp_path / "x")).search("alpha") == []


# ---- what ingest walks ---------------------------------------------------

def test_ingest_prunes_caches_and_vendored_dependencies(tmp_path, capsys):
    """Pointing this at a project root is the documented use. `.venv` alone can
    outnumber the real docs a thousand to one, and the index still looks fine
    -- every answer just comes from site-packages."""
    d = tmp_path / "project"
    (d / ".venv" / "lib").mkdir(parents=True)
    (d / "__pycache__").mkdir()
    (d / "docs").mkdir()
    (d / ".venv" / "lib" / "vendored.py").write_text("def alpha(): pass\n")
    (d / "__pycache__" / "stale.py").write_text("def beta(): pass\n")
    (d / "docs" / "real.md").write_text("# Alpha\nalpha\n")

    s = DocStore(FakeEmbedder(), path=str(tmp_path / "idx"))
    s.ingest_dir(str(d))
    assert all("venv" not in src and "pycache" not in src for src in s.sources)
    assert "[rag] skipped 2 directories" in capsys.readouterr().out


def test_ingest_skips_a_binary_file_wearing_a_text_extension(tmp_path, capsys):
    """The reader passes errors="ignore", so a blob does not fail -- it becomes
    mojibake, gets embedded, and sits in the index at whatever score it
    happens to earn."""
    d = tmp_path / "docs"
    d.mkdir()
    (d / "real.md").write_text("# Alpha\nalpha\n")
    (d / "blob.txt").write_bytes(b"PK\x03\x04\x00\x00garbage\x00\xff\xfe")

    s = DocStore(FakeEmbedder(), path=str(tmp_path / "idx"))
    s.ingest_dir(str(d))
    assert s.sources == ["real.md"]
    assert "1 binary files" in capsys.readouterr().out


def test_ingest_indexes_identical_text_once(tmp_path, capsys):
    """Duplicates compete with themselves for the k slots the gate has to
    spend: one passage vendored twice can fill every slot and crowd out the
    rest of the answer."""
    d = tmp_path / "docs"
    (d / "vendor").mkdir(parents=True)
    (d / "a.md").write_text("# Alpha\nalpha alpha\n")
    (d / "vendor" / "a-copy.md").write_text("# Alpha\nalpha alpha\n")

    s = DocStore(FakeEmbedder(), path=str(tmp_path / "idx"))
    s.ingest_dir(str(d))
    assert len(s.chunks) == 1
    assert "dropped 1 duplicate chunks" in capsys.readouterr().out

    # Dedupe shortens the list before it is embedded, so chunks and vectors
    # must still agree. This is exactly the drift `load` refuses, checked on
    # the one path that now shortens anything.
    s.save()
    assert DocStore(FakeEmbedder(), path=s.path).load().chunks == s.chunks


def test_an_empty_query_matches_nothing(store):
    assert store.search("   ") == []
    assert retrieve_context(store, "") == ""


# ---- planning, before anything is embedded -------------------------------

@pytest.fixture
def project(tmp_path):
    d = tmp_path / "project"
    (d / "docs").mkdir(parents=True)
    (d / "internal").mkdir()
    (d / ".venv").mkdir()
    (d / "docs" / "guide.md").write_text("# Alpha\nalpha\n")
    (d / "docs" / "api.md").write_text("# Beta\nbeta\n")
    (d / "internal" / "notes.md").write_text("# Gamma\ngamma\n")
    (d / ".venv" / "vendored.md").write_text("# Delta\ndelta\n")
    return d


def test_a_plan_reports_what_it_would_index_without_embedding_it(project):
    """The Embedder is what costs; chunking is free. Planning has to be
    separable from embedding or the review can only happen after the bill."""
    plan = plan_ingest(str(project))
    assert dict(plan.per_file) == {"docs/guide.md": 1, "docs/api.md": 1,
                                   "internal/notes.md": 1}
    assert plan.skipped_dirs == (".venv",)


def test_a_plan_can_leave_a_path_out(project):
    plan = plan_ingest(str(project), exclude=("internal",))
    assert "internal/notes.md" not in dict(plan.per_file)
    assert plan.excluded == ("internal/notes.md",)


def test_exclusion_takes_a_glob(project):
    plan = plan_ingest(str(project), exclude=("docs/*.md",))
    assert list(dict(plan.per_file)) == ["internal/notes.md"]


def test_excluding_everything_is_an_error_not_an_empty_index(project):
    with pytest.raises(ValueError, match="no files matched"):
        plan_ingest(str(project), exclude=("*",))


def test_the_review_names_every_file_and_what_was_left_out(project):
    text = render_plan(plan_ingest(str(project), exclude=("internal",)))
    assert "docs/guide.md" in text
    assert "skipped 1 directories" in text
    assert "excluded 1 files" in text


def test_an_accepted_plan_is_what_gets_embedded(project):
    plan = plan_ingest(str(project), exclude=("internal",))
    s = DocStore(FakeEmbedder(), path=str(project / "idx"))
    s.ingest_plan(plan, verbose=False)
    assert s.sources == list(plan.sources)
    assert s.vectors.shape[0] == len(plan.chunks)


def test_a_pruned_directory_can_be_asked_for_explicitly(tmp_path):
    """The skip list is a default, not a rule: whoever keeps docs in an
    unusual directory needs a way to say so."""
    d = tmp_path / "project"
    (d / "venv").mkdir(parents=True)
    (d / "venv" / "notes.md").write_text("# Alpha\nalpha\n")

    s = DocStore(FakeEmbedder(), path=str(tmp_path / "idx"))
    s.ingest_dir(str(d), verbose=False, skip_dirs=frozenset())
    assert s.chunks


# ---- multi-language chunking (tree-sitter) -------------------------------

CPP_SOURCE = """\
#include <vector>

// adds two numbers
int add(int a, int b) {
    return a + b;
}

class Counter {
  public:
    void bump() { n++; }
    int value() const { return n; }
  private:
    int n = 0;
};
"""

RUST_SOURCE = """\
use std::collections::HashMap;

fn add(a: i32, b: i32) -> i32 {
    a + b
}

struct Counter { n: i32 }
"""

OCAML_SOURCE = """\
let add a b = a + b

let rec fact n = if n <= 1 then 1 else n * fact (n - 1)
"""


def _labels(chunks):
    return [c[0].splitlines()[0] for c in chunks]


def test_a_cpp_function_becomes_its_own_chunk():
    """The gap this closes: a language's own code samples used to be chunked as
    prose, so retrieval over C++ docs cut functions in half at 800 characters."""
    pytest.importorskip("tree_sitter_language_pack")
    chunks = rag.chunk_code(CPP_SOURCE, "demo.cpp", "cpp")
    assert any("add" in label for label in _labels(chunks))
    body = next(c[0] for c in chunks if "add" in c[0].splitlines()[0])
    assert "return a + b;" in body
    assert "// adds two numbers" in body, "the comment above it is context"


def test_a_cpp_class_keeps_its_methods_together_when_small():
    pytest.importorskip("tree_sitter_language_pack")
    chunks = rag.chunk_code(CPP_SOURCE, "demo.cpp", "cpp")
    counter = [c for c in chunks if "Counter" in c[0].splitlines()[0]]
    assert counter, _labels(chunks)
    assert "bump" in counter[0][0] and "value" in counter[0][0]


def test_a_large_class_splits_into_methods():
    """Same rule the Python chunker follows: a class over the budget is worth
    more as one chunk per method than as one truncated chunk."""
    pytest.importorskip("tree_sitter_language_pack")
    filler = "\n".join(f"    void m{i}() {{ /* {'x' * 60} */ }}" for i in range(20))
    src = "class Big {\n  public:\n" + filler + "\n};\n"
    labels = _labels(rag.chunk_code(src, "big.cpp", "cpp", max_chars=400))
    assert any("m0" in label for label in labels), labels
    assert any("m19" in label for label in labels), labels


def test_top_level_statements_group_into_a_preamble():
    pytest.importorskip("tree_sitter_language_pack")
    chunks = rag.chunk_code(RUST_SOURCE, "demo.rs", "rust")
    assert any("use std::collections::HashMap;" in c[0] for c in chunks)


def test_ocaml_is_chunked_by_definition_not_by_paragraph():
    """The case that motivated it. OCaml is what `learn` was first run on, and
    its docs are exactly where prose chunking hurt most."""
    pytest.importorskip("tree_sitter_language_pack")
    labels = _labels(rag.chunk_code(OCAML_SOURCE, "demo.ml", "ocaml"))
    assert any("add" in label for label in labels), labels
    assert any("fact" in label for label in labels), labels


def test_source_that_does_not_parse_still_produces_chunks():
    """tree-sitter recovers from errors rather than raising, but a file that is
    mostly garbage must still be retrievable rather than dropped."""
    pytest.importorskip("tree_sitter_language_pack")
    chunks = rag.chunk_code("int add(int a, int b) { return a + ", "x.cpp", "cpp")
    assert chunks and any("add" in c[0] for c in chunks)


def test_a_grammar_nobody_carries_falls_back_to_prose():
    """A missing grammar must cost prose chunks, never a failed ingest. (The
    pack turns out to carry Zig, which is why this asks for a name nobody
    has.)"""
    assert rag.chunk_code("some text\n", "x.xyz", "not-a-real-language") == \
        rag.chunk_markdown("some text\n", "x.xyz")


def test_chunk_file_routes_a_code_extension_to_its_grammar():
    pytest.importorskip("tree_sitter_language_pack")
    labels = _labels(rag.chunk_file("demo.cpp", CPP_SOURCE))
    assert any("add" in label for label in labels), labels
    # Python keeps its own AST chunker: it is stdlib, exact, and already tested.
    py = rag.chunk_file("demo.py", "def add(a, b):\n    return a + b\n")
    assert "function add" in py[0][0]


def test_ingest_sees_the_code_files_it_can_now_chunk(tmp_path):
    """The wiring that would have made the chunker pointless: `ingest` only
    matched .py/.md/.txt/.rst, so an OCaml docs directory full of .ml samples
    was skipped entirely before anything could chunk it."""
    (tmp_path / "lib.ml").write_text("let add a b = a + b\n")
    (tmp_path / "demo.cpp").write_text("int add(int a, int b) { return a + b; }\n")
    (tmp_path / "notes.md").write_text("# Notes\nprose\n")
    plan = plan_ingest(str(tmp_path))
    names = {os.path.basename(src) for src in plan.sources}
    assert {"lib.ml", "demo.cpp", "notes.md"} <= names, names


OCAML_INTERFACE = '''\
(** Option values. *)

type 'a t = 'a option = None | Some of 'a

(** [none] is [None]. *)
val none : 'a option

(** [some v] is [Some v]. *)
val some : 'a -> 'a option
'''


def test_an_ocaml_interface_is_chunked_by_declaration():
    """Found live against the real stdlib .mli files. `val` declarations parse
    as `value_specification`, which the suffix list did not carry -- so all
    sixteen of them fell into the preamble and were cut into prose fragments
    like `()] otherwise. *)`. An interface file is nothing BUT declarations,
    which made the chunker useless on exactly the corpus it was built for."""
    pytest.importorskip("tree_sitter_language_pack")
    chunks = rag.chunk_code(OCAML_INTERFACE, "option.mli", "ocaml")
    labels = _labels(chunks)
    assert any("none" in label for label in labels), labels
    assert any("some" in label for label in labels), labels


def test_a_declaration_keeps_the_doc_comment_above_it():
    """In an interface file the comment IS the documentation -- losing it
    leaves a signature with nothing to retrieve on."""
    pytest.importorskip("tree_sitter_language_pack")
    chunks = rag.chunk_code(OCAML_INTERFACE, "option.mli", "ocaml")
    body = next(c[0] for c in chunks if "none" in c[0].splitlines()[0])
    assert "[none] is [None]" in body


def test_a_type_is_labelled_by_its_own_name_not_its_first_constructor():
    """`type 'a t = ... None | Some of 'a` was labelled `None`: the walk found
    a constructor before the type constructor, because `type_constructor` was
    not counted as a name."""
    pytest.importorskip("tree_sitter_language_pack")
    labels = _labels(rag.chunk_code(OCAML_INTERFACE, "option.mli", "ocaml"))
    assert any(label.endswith("t in option.mli") for label in labels), labels


def test_a_long_section_is_split_on_line_boundaries_not_mid_word():
    """Live finding, over 61 real OCaml tutorials: 445 of 3044 chunks began
    mid-word -- `de effect`, `rom the left hand end`, `htening to illustrate`.
    The window was sliced by CHARACTER, so a chunk could open in the middle of
    a token and the retrieved context handed the model a fragment."""
    section = "# Heading\n" + "\n".join(
        f"sentence number {i} explaining something about lists" for i in range(60))
    chunks = rag.chunk_markdown(section, "doc.md", max_chars=300, overlap=60)
    assert len(chunks) > 1, "the section should have been split at all"
    for text, _ in chunks:
        assert text.startswith(("#", "sentence")), repr(text[:40])


def test_splitting_still_covers_the_whole_section():
    """A boundary-respecting split must not silently drop text."""
    lines = [f"line {i} with several words in it" for i in range(40)]
    chunks = rag.chunk_markdown("\n".join(lines), "doc.md", max_chars=200,
                                overlap=40)
    joined = " ".join(t for t, _ in chunks)
    for i in (0, 17, 39):
        assert f"line {i} with" in joined, i


def test_a_single_line_longer_than_the_window_is_still_emitted():
    """A minified line or a very long URL has no boundary to split on. It must
    still be indexed rather than dropped."""
    long_line = "x" * 900
    chunks = rag.chunk_markdown(long_line, "doc.md", max_chars=300, overlap=50)
    assert chunks
    assert sum(len(t) for t, _ in chunks) >= 900


def test_an_overlap_at_or_above_the_window_does_not_explode():
    """The old character-sliced chunker guarded this with `stride = max(1,
    max_chars - overlap)`, which stopped it hanging. The line-based one
    terminates without that guard but degenerates instead: it carries the whole
    previous window forward, advances one line at a time, and emits a
    near-duplicate chunk per line -- 500 lines became 476 chunks of 25 lines
    each, a 25x blow-up in an index nobody would notice was wrong."""
    text = "x\n" * 500
    chunks = rag.chunk_markdown(text, "d.md", max_chars=50, overlap=99)
    assert len(chunks) < 60, len(chunks)


def test_the_overlap_still_overlaps_at_sane_settings():
    lines = [f"line {i} of the section" for i in range(40)]
    chunks = rag.chunk_markdown("\n".join(lines), "d.md", max_chars=200,
                                overlap=60)
    texts = [c for c, _ in chunks]
    assert len(texts) > 2
    shared = [t for t in texts[1:]
              if any(ln in texts[texts.index(t) - 1] for ln in t.splitlines())]
    assert shared, "consecutive chunks share no line at all"


def test_one_incidental_token_does_not_make_a_perfect_lexical_match(api_store):
    """The bug behind "the gate never refuses". The score was the share of the
    query's KNOWN tokens a chunk holds, so tokens the corpus has never seen
    were dropped from the denominator rather than counted against the match.
    Measured on 3044 chunks of OCaml documentation: `cheapest flights to Lisbon
    in March` scored a perfect 1.000 lexical -- one incidental token existed,
    and being unable to explain the other five cost nothing."""
    full = api_store._lexical("Printf.eprintf").max()
    incidental = api_store._lexical(
        "Printf.eprintf lisbon flights cheapest march rattling").max()
    assert full == 1.0
    assert incidental < 0.5, incidental


def test_a_query_of_entirely_unknown_tokens_still_scores_zero(api_store):
    assert not api_store._lexical("lisbon flights cheapest").any()


def test_the_gate_now_refuses_a_query_the_corpus_cannot_answer(api_store):
    """The gate exists to refuse, and until the lexical fix it could not: every
    query scored above the threshold. With unknown tokens counted against the
    match, a question the corpus has nothing to say about falls below it."""
    assert api_store.search("Printf.eprintf")
    assert api_store.search("lisbon flights cheapest march") == []


def test_the_default_threshold_is_the_calibrated_one():
    """0.3 was inherited from when the score was cosine alone. The hybrid runs
    past 1.0, so the old default could not refuse anything."""
    import inspect

    default = inspect.signature(rag.DocStore.search).parameters["min_score"].default
    assert default >= 0.8


def test_an_embedder_that_cannot_fit_on_the_gpu_falls_back_to_cpu(monkeypatch,
                                                                  capsys):
    """Found by giving the LLM more context. At 16k tokens and full offload the
    server takes 5.5 GB of a 6 GB card, and the embedder -- which needs about
    275 MB, mostly torch's CUDA context -- dies with a raw
    `torch.OutOfMemoryError` traceback in the middle of an ingest. The card is
    shared; the small model is the one that should yield."""
    calls = []

    class FakeST:
        def __init__(self, name, device="cuda"):
            calls.append(device)
            if device == "cuda":
                raise RuntimeError("CUDA out of memory. Tried to allocate 20 MiB")

    import sentence_transformers
    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", FakeST)
    embedder = rag.Embedder(device="cuda")
    assert calls == ["cuda", "cpu"]
    assert embedder.device == "cpu"
    assert "cpu" in capsys.readouterr().out.lower()


def test_a_failure_that_is_not_about_memory_still_raises(monkeypatch):
    """Falling back on every error would hide a wrong model name behind a
    silent, very slow CPU run."""
    class FakeST:
        def __init__(self, name, device="cuda"):
            raise RuntimeError("model not found on the hub")

    import sentence_transformers
    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", FakeST)
    with pytest.raises(RuntimeError, match="not found"):
        rag.Embedder(device="cuda")
