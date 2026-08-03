"""
purecoder/rag.py

Minimal doc/code-retrieval RAG over ONE library or project, for a tight 6 GB card.

Pipeline: chunk source (code-aware for .py, markdown-aware for docs) -> embed
with a small model -> store vectors on disk -> at generation time retrieve the
top-k relevant chunks ONLY IF they clear a similarity threshold (the "retrieve
when needed" gate), injecting just that slice to stay inside context.

The Embedder is injectable so store/chunk logic is testable without a GPU.
"""

import ast
import json
import os
import re

import numpy as np

# ---- markdown / prose chunking ------------------------------------------

def chunk_markdown(text, source, max_chars=800, overlap=150):
    lines = text.split("\n")
    sections, cur = [], []
    for line in lines:
        if re.match(r"^#{1,6}\s", line) and cur:
            sections.append("\n".join(cur))
            cur = [line]
        else:
            cur.append(line)
    if cur:
        sections.append("\n".join(cur))
    chunks = []
    for sec in sections:
        sec = sec.strip()
        if not sec:
            continue
        if len(sec) <= max_chars:
            chunks.append(sec)
        else:
            # An overlap at or above the window makes the stride zero or
            # negative -- the loop never advances and the process hangs with no
            # output. `chunk_markdown(text, src, max_chars=100)` against the
            # default overlap of 150 is enough to do it.
            stride = max(1, max_chars - overlap)
            start = 0
            while start < len(sec):
                chunks.append(sec[start:start + max_chars].strip())
                start += stride
    return [(c, source) for c in chunks if c.strip()]


# ---- code-aware chunking (Python, via AST) ------------------------------

def _node_source(lines, node):
    start = node.lineno
    if getattr(node, "decorator_list", None):
        start = min([start] + [d.lineno for d in node.decorator_list])
    return "\n".join(lines[start - 1:node.end_lineno]), start, node.end_lineno


def _leading_comments(lines, start_1based):
    out, i = [], start_1based - 2
    while i >= 0 and lines[i].lstrip().startswith("#"):
        out.insert(0, lines[i])
        i -= 1
    return out


def chunk_python(source, filename, max_chars=1200):
    """Split Python into function/class/method chunks via the AST. Large
    classes split into per-method chunks; module-level statements group into
    one preamble. Falls back to markdown chunking if the file won't parse."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return chunk_markdown(source, filename, max_chars=max_chars)

    lines = source.split("\n")
    chunks, preamble = [], []

    def emit(text, label):
        text = text.strip()
        if text:
            chunks.append((f"# {label}\n{text}", filename))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            src, s, _ = _node_source(lines, node)
            comments = _leading_comments(lines, s)
            body = ("\n".join(comments) + "\n" + src) if comments else src
            emit(body, f"function {node.name} in {filename}")
        elif isinstance(node, ast.ClassDef):
            src, _, _ = _node_source(lines, node)
            methods = [n for n in node.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if len(src) <= max_chars or not methods:
                emit(src, f"class {node.name} in {filename}")
            else:
                first = min(min([m.lineno] + [d.lineno for d in m.decorator_list])
                            for m in methods)
                emit("\n".join(lines[node.lineno - 1:first - 1]),
                     f"class {node.name} (header) in {filename}")
                for m in methods:
                    msrc, _, _ = _node_source(lines, m)
                    emit(msrc, f"method {node.name}.{m.name} in {filename}")
        else:
            src, _, _ = _node_source(lines, node)
            preamble.append(src)

    if preamble:
        pre = "\n".join(preamble).strip()
        if len(pre) <= max_chars:
            emit(pre, f"module top-level of {filename}")
        else:
            chunks += chunk_markdown(pre, filename, max_chars=max_chars)
    return chunks


def chunk_file(path, source, max_chars_code=1200, max_chars_docs=800):
    """Route by extension: .py -> code chunker, else markdown chunker."""
    if os.path.splitext(path)[1].lower() == ".py":
        return chunk_python(source, path, max_chars=max_chars_code)
    return chunk_markdown(source, path, max_chars=max_chars_docs)


# ---- embedder (needs sentence-transformers + a model) -------------------

class Embedder:
    def __init__(self, model_name="BAAI/bge-small-en-v1.5", device="cuda",
                 query_prefix="Represent this sentence for searching "
                              "relevant passages: ",
                 doc_prefix=""):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model_name, device=device)
        self.query_prefix = query_prefix
        self.doc_prefix = doc_prefix

    def embed_docs(self, texts):
        return np.asarray(self.model.encode(
            [self.doc_prefix + t for t in texts],
            normalize_embeddings=True, batch_size=16, show_progress_bar=False))

    def embed_query(self, text):
        return np.asarray(self.model.encode(
            self.query_prefix + text, normalize_embeddings=True))


# ---- vector store (brute-force cosine) ----------------------------------

class DocStore:
    def __init__(self, embedder, path="docstore"):
        self.embedder = embedder
        self.path = path
        self.vectors = None
        self.chunks = []
        self.sources = []

    def ingest_dir(self, docs_dir, pattern=r".*\.(py|md|markdown|txt|rst)$",
                   verbose=True):
        pairs, rx = [], re.compile(pattern)
        for root, _, files in os.walk(docs_dir):
            for fn in files:
                if rx.match(fn):
                    p = os.path.join(root, fn)
                    with open(p, encoding="utf-8", errors="ignore") as f:
                        pairs += chunk_file(os.path.relpath(p, docs_dir), f.read())
        if not pairs:
            raise ValueError(f"no files matched under {docs_dir}")
        self.chunks = [c for c, _ in pairs]
        self.sources = [s for _, s in pairs]
        self.vectors = self.embedder.embed_docs(self.chunks).astype("float32")
        if verbose:
            print(f"[rag] ingested {len(self.chunks)} chunks from {docs_dir}")
        return len(self.chunks)

    def search(self, query, k=3, min_score=0.3):
        if self.vectors is None or not self.chunks:
            return []
        qv = self.embedder.embed_query(query).astype("float32")
        scores = self.vectors @ qv
        order = np.argsort(-scores)[:k]
        return [(self.chunks[i], self.sources[i], float(scores[i]))
                for i in order if scores[i] >= min_score]

    def save(self):
        np.save(self.path + ".npy", self.vectors)
        with open(self.path + ".json", "w") as f:
            json.dump({"chunks": self.chunks, "sources": self.sources}, f)

    def load(self):
        self.vectors = np.load(self.path + ".npy")
        with open(self.path + ".json") as f:
            meta = json.load(f)
        self.chunks, self.sources = meta["chunks"], meta["sources"]
        return self


# ---- retrieve-when-needed gate + injection ------------------------------

def retrieve_context(store, query, k=3, min_score=0.3, max_chars=1500):
    hits = store.search(query, k=k, min_score=min_score)
    if not hits:
        return ""
    blocks, used = [], 0
    for chunk, source, _score in hits:
        block = f"# doc: {source}\n{chunk}"
        if used + len(block) > max_chars:
            break
        blocks.append(block)
        used += len(block)
    return "Relevant documentation:\n\n" + "\n\n".join(blocks)
