"""Chunking, retrieval and the retrieve-when-needed gate.

The Embedder is injectable precisely so this runs without a GPU or a model
download -- a deterministic fake stands in for sentence-transformers.
"""

import numpy as np
import pytest

from purecoder.rag import (
    DocStore,
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


def test_retrieve_context_respects_char_budget(store):
    assert len(retrieve_context(store, "alpha", max_chars=10)) <= len(
        "Relevant documentation:\n\n")


def test_save_and_load_round_trip(store):
    store.save()
    reloaded = DocStore(FakeEmbedder(), path=store.path).load()
    assert reloaded.chunks == store.chunks
    assert reloaded.sources == store.sources
    assert np.allclose(reloaded.vectors, store.vectors)


def test_search_on_empty_store_returns_nothing(tmp_path):
    assert DocStore(FakeEmbedder(), path=str(tmp_path / "x")).search("alpha") == []
