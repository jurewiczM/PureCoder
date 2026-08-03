"""Chunking, retrieval and the retrieve-when-needed gate.

The Embedder is injectable precisely so this runs without a GPU or a model
download -- a deterministic fake stands in for sentence-transformers.
"""

import json

import numpy as np
import pytest

from purecoder.rag import (
    DocStore,
    StoreError,
    chunk_file,
    chunk_markdown,
    chunk_python,
    retrieve_context,
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


# ---- store + gate, with a fake embedder ---------------------------------

class FakeEmbedder:
    """Bag-of-words unit vectors: similar text -> high cosine, no model needed."""

    VOCAB = ["alpha", "beta", "gamma", "delta"]

    def _vec(self, text):
        v = np.array([float(text.lower().count(w)) for w in self.VOCAB])
        norm = np.linalg.norm(v)
        return v / norm if norm else v

    def embed_docs(self, texts):
        return np.vstack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)


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


def test_a_pruned_directory_can_be_asked_for_explicitly(tmp_path):
    """The skip list is a default, not a rule: whoever keeps docs in an
    unusual directory needs a way to say so."""
    d = tmp_path / "project"
    (d / "venv").mkdir(parents=True)
    (d / "venv" / "notes.md").write_text("# Alpha\nalpha\n")

    s = DocStore(FakeEmbedder(), path=str(tmp_path / "idx"))
    s.ingest_dir(str(d), verbose=False, skip_dirs=frozenset())
    assert s.chunks
