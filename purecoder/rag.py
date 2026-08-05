"""
purecoder/rag.py

Minimal doc/code-retrieval RAG over ONE library or project, for a tight 6 GB card.

Pipeline: chunk source (code-aware for .py, markdown-aware for docs) -> embed
with a small model -> store vectors on disk -> at generation time retrieve the
top-k relevant chunks if they clear a similarity threshold (the "retrieve when
needed" gate), injecting just that slice to stay inside context. Over 3044
chunks of real documentation, relevant questions score 1.10-1.34 and unrelated
ones 0.50-0.70; the gate sits at 0.8. Those ranges used to overlap, because a
token the corpus had never seen was dropped from the lexical denominator rather
than counted against the match -- see `_lexical`.

Ranking uses two signals. Cosine similarity answers "is this about the same
thing"; an IDF-weighted lexical score answers "does this contain the exact name
you asked for". Embeddings are worst at precisely the queries this tool gets
most -- an API symbol, spelled exactly -- so the second signal is not a
refinement, it is the one that decides those.

The Embedder is injectable so store/chunk logic is testable without a GPU.
"""

import ast
import fnmatch
import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass

import numpy as np

from .symbols import extract_symbols, modules

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
            # Split on line boundaries. It used to slice by character, and over
            # 61 real OCaml tutorials that opened 445 of 3044 chunks mid-word
            # -- `de effect`, `rom the left hand end` -- so retrieval handed the
            # model a fragment starting in the middle of a token.
            #
            # An overlap at or above the window makes the stride zero or
            # negative -- the loop never advances and the process hangs with no
            # output. `chunk_markdown(text, src, max_chars=100)` against the
            # default overlap of 150 is enough to do it.
            # An overlap at or above the window is what made the old
            # character-sliced version hang, and it degenerates this one
            # instead: the whole previous window is carried forward, the walk
            # advances a line at a time, and 500 lines become 476 near-identical
            # chunks. Half the window is the most that can be carried while
            # still guaranteeing real forward progress.
            carry = min(overlap, max_chars // 2)
            stride = max(1, max_chars - carry)
            window = []

            # Bound as defaults: both are per-section values, and a closure over
            # the loop's variables is a trap even where it happens to work.
            def flush(window=window, carry=carry):
                joined = "\n".join(window).strip()
                if joined:
                    chunks.append(joined)
                # Carry the tail of this window into the next one, by whole
                # lines, until the carried text reaches the overlap budget.
                keep, size = [], 0
                for line in reversed(window):
                    if size + len(line) + 1 > carry:
                        break
                    keep.insert(0, line)
                    size += len(line) + 1
                window[:] = keep

            for line in sec.split("\n"):
                # A single line longer than the whole window has no boundary to
                # respect -- a minified file, a very long URL, a table row. It
                # is sliced by character, which is what the old code did to
                # everything.
                if len(line) > max_chars:
                    flush()
                    window.clear()
                    for start in range(0, len(line), stride):
                        chunks.append(line[start:start + max_chars].strip())
                    continue
                if window and sum(len(w) + 1 for w in window) + len(line) > max_chars:
                    flush()
                window.append(line)
            flush()
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


# ---- every other language (tree-sitter) ---------------------------------
#
# Python is chunked by its own AST: stdlib, exact, and the only language whose
# parser this project can assume. Everything else was chunked as PROSE, which
# is the wrong shape by definition -- a paragraph break has nothing to do with
# where a function ends, so an 800-character window cut C++ and OCaml samples
# in half. That mattered most exactly where the project cares most: a learned
# language's documentation is full of code in a language nothing here parses.
#
# tree-sitter is an optional install for the same reason retrieval is: it is
# only needed by `ingest`, and a missing grammar degrades to the prose chunker
# rather than failing the run.

# Extension -> grammar name in tree-sitter-language-pack. `.py` is absent on
# purpose: the AST chunker above is better and costs no dependency.
CODE_LANGUAGES = {
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".h": "cpp",
    ".js": "javascript", ".mjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".rs": "rust", ".cs": "csharp", ".go": "go",
    ".java": "java", ".ml": "ocaml", ".mli": "ocaml", ".sql": "sql",
    ".rb": "ruby", ".swift": "swift", ".c": "c",
}

# A node worth its own chunk. Matched on a SUFFIX rather than a fixed list of
# node types, because every grammar names these differently -- C++ has
# `function_definition`, JavaScript `function_declaration`, Rust
# `function_item`, OCaml `value_definition` -- and a per-language table would
# be the per-language surface this whole design exists to avoid.
# `_specification` is here because of a live run: OCaml `.mli` files -- an
# interface file is nothing BUT declarations -- parse every `val` as
# `value_specification`, so leaving it out sent all sixteen of `option.mli`'s
# entries to the prose fallback and produced chunks like `()] otherwise. *)`.
# The chunker was useless on exactly the corpus it was written for.
_DEFINITION_SUFFIXES = ("_definition", "_declaration", "_specification",
                        "_item", "_specifier", "_binding")


def _ts_parser(lang: str):
    """A parser for one grammar, or None if it is not installed."""
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        return None
    try:
        return get_parser(lang)
    except Exception:
        # The pack raises its own download/lookup errors for a grammar it does
        # not carry. Not knowing a language is an ordinary outcome here.
        return None


# What the various grammars call an identifier.
# `type_constructor` is OCaml's name for the name in `type 'a t = ...`. Without
# it the walk reached `None` in the variant declaration first and labelled the
# type after its own first constructor -- also from the live run.
_NAME_TYPES = ("identifier", "type_identifier", "field_identifier",
               "property_identifier", "value_name", "constructor_name",
               "type_constructor")


def _ts_name(node, source: bytes, max_depth: int = 4) -> str:
    """The identifier a definition declares, as best the grammar will say.

    Breadth-first, so the SHALLOWEST identifier wins: C++ buries `add` one
    level down in a `function_declarator` whose next sibling is the parameter
    list, and a depth-first walk would happily return a parameter's name.
    Bodies are skipped for the same reason -- the first identifier inside a
    function is a local, not the function.

    Generic on purpose. `name`, `declarator` and `pattern` are three different
    fields for the same idea across C++, OCaml and Rust, and a per-language
    table is the surface this design exists to avoid. A definition whose name
    cannot be found still becomes a chunk: the label is worth less, the text is
    worth the same.
    """
    queue = [(node, 0)]
    while queue:
        current, depth = queue.pop(0)
        if depth and current.type in _NAME_TYPES:
            return source[current.start_byte:current.end_byte].decode(
                "utf8", "replace")
        if depth >= max_depth:
            continue
        for child in current.named_children:
            if child.type == "comment" or child.type.endswith(("_body", "block")):
                continue
            queue.append((child, depth + 1))
    return ""


def _ts_definitions(node):
    """Direct children of `node` that are definitions worth a chunk."""
    return [c for c in node.named_children
            if c.type.endswith(_DEFINITION_SUFFIXES)]


def chunk_code(source, filename, lang, max_chars=1200):
    """Split code into definition-sized chunks with tree-sitter.

    Same shape as `chunk_python`: one chunk per top-level definition, a large
    one split into its own inner definitions, and everything else gathered into
    a preamble. Falls back to the prose chunker when the grammar is missing --
    the point is better chunks where a parser exists, never a failed ingest
    where one does not.
    """
    parser = _ts_parser(lang)
    if parser is None:
        return chunk_markdown(source, filename, max_chars=max_chars)

    data = source.encode("utf8")
    root = parser.parse(data).root_node
    chunks, preamble = [], []

    def text_of(node):
        return data[node.start_byte:node.end_byte].decode("utf8", "replace")

    def emit(text, label):
        text = text.strip()
        if text:
            chunks.append((f"# {label}\n{text}", filename))

    # A comment is a top-level node of its own, so without this the docstring
    # above a function lands in the preamble and the function loses the only
    # prose written about it -- the same rule the Python chunker follows with
    # its leading-comment scan, expressed in the grammar's own terms.
    pending_comments = []

    for node in root.named_children:
        if node.type == "comment":
            pending_comments.append(text_of(node))
            continue
        if not node.type.endswith(_DEFINITION_SUFFIXES):
            preamble.extend(pending_comments)
            pending_comments = []
            preamble.append(text_of(node))
            continue

        name = _ts_name(node, data) or node.type
        body = text_of(node)
        if pending_comments:
            body = "\n".join(pending_comments) + "\n" + body
            pending_comments = []

        inner = [c for c in _ts_definitions(node)
                 if c.type.endswith(("_definition", "_declaration", "_item"))]
        # A class-like node also nests its members under a body node, which is
        # where C++ and C# keep their methods.
        for child in node.named_children:
            if child.type.endswith(("_body", "_list", "block")):
                inner += _ts_definitions(child)

        if len(body) <= max_chars or not inner:
            emit(body, f"{node.type} {name} in {filename}".strip())
            continue

        header_end = min(c.start_byte for c in inner)
        emit(data[node.start_byte:header_end].decode("utf8", "replace"),
             f"{node.type} {name} (header) in {filename}")
        for member in inner:
            emit(text_of(member),
                 f"member {name}.{_ts_name(member, data) or member.type} "
                 f"in {filename}")

    preamble.extend(pending_comments)
    if preamble:
        pre = "\n".join(preamble).strip()
        if len(pre) <= max_chars:
            emit(pre, f"top-level of {filename}")
        else:
            chunks += chunk_markdown(pre, filename, max_chars=max_chars)
    return chunks


def chunk_file(path, source, max_chars_code=1200, max_chars_docs=800):
    """Route by extension: .py -> the AST chunker, a known grammar ->
    tree-sitter, everything else -> markdown."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".py":
        return chunk_python(source, path, max_chars=max_chars_code)
    if ext in CODE_LANGUAGES:
        return chunk_code(source, path, CODE_LANGUAGES[ext],
                          max_chars=max_chars_code)
    return chunk_markdown(source, path, max_chars=max_chars_docs)


# ---- embedder (needs sentence-transformers + a model) -------------------

class MissingRetrieval(ImportError):
    """sentence-transformers is not installed. It is not in the base install."""


class StoreError(ValueError):
    """The index on disk cannot be trusted -- absent, unreadable, or mismatched."""


class Embedder:
    def __init__(self, model_name="BAAI/bge-small-en-v1.5", device="cuda",
                 query_prefix="Represent this sentence for searching "
                              "relevant passages: ",
                 doc_prefix=""):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            # The retrieval extra pulls in torch (~2 GB), so it is deliberately
            # optional -- which makes this the first wall a new user hits. It
            # deserves the install command, not an import traceback.
            raise MissingRetrieval(
                "retrieval needs sentence-transformers, which the base install "
                'does not include (it pulls in torch, ~2 GB).\n  pip install -e ".[rag]"'
            ) from e
        self.model = SentenceTransformer(model_name, device=device)
        # Kept so an index can name the model that built it: vectors from two
        # different models are not comparable, and at equal dimensions nothing
        # about that mismatch is visible at query time.
        self.model_name = model_name
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

# Caches, VCS metadata and vendored dependencies -- never the documentation
# anyone meant to index. Deliberately excludes `build` and `dist`: generated
# docs live there often enough that pruning them would be the wrong default.
# Overridable per call for the directory this list gets wrong.
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".ipynb_checkpoints", ".tox", ".eggs", "node_modules",
    ".venv", "venv", "env", "site-packages",
})


# ---- the lexical signal -------------------------------------------------

# How much an exact-name match is worth next to cosine similarity. At 0.5,
# against the default min_score of 0.3, a chunk containing every rare token of
# the query clears the gate on the lexical signal alone. That is the point:
# `Printf.eprintf` should retrieve the page defining it even when a page merely
# *about* output formatting embeds closer.
LEXICAL_WEIGHT = 0.5

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


def tokenize(text):
    """Lowercased tokens; a dotted name is kept whole AND split.

    `Printf.eprintf` yields {printf.eprintf, printf, eprintf}, so a query hits
    whether it spells the name qualified or bare. snake_case and camelCase are
    deliberately not split: `pc_check` -> `pc` + `check` makes `check` worthless
    as a signal and buys nothing the dotted split does not already give.
    """
    out = set()
    for match in _TOKEN.findall(text.lower()):
        token = match.strip(".")
        if not token:
            continue
        out.add(token)
        if "." in token:
            out.update(part for part in token.split(".") if part)
    return out


def _is_binary(path, sniff=8192):
    """A NUL byte in the first block. The same test `file` and grep use."""
    try:
        with open(path, "rb") as f:
            return b"\x00" in f.read(sniff)
    except OSError:
        return True          # unreadable is not something to index either


@dataclass(frozen=True)
class IngestPlan:
    """What an index WOULD contain -- everything except the embedding.

    Chunking is cheap and embedding is not, so the split is where the user gets
    to look. Nothing here has touched a model, which is what makes the review
    step free and lets it be re-run after an exclusion without paying twice.
    """
    root: str
    chunks: tuple
    sources: tuple
    skipped_dirs: tuple
    binaries: tuple
    duplicates: int
    excluded: tuple

    @property
    def per_file(self):
        """(path, chunk count), commonest first -- what the review shows."""
        return Counter(self.sources).most_common()


# Prose, Python, and every extension the tree-sitter chunker can parse.
# Before the chunker existed there was no reason to index a .ml or a .cpp
# -- they would have been cut into paragraphs -- and an OCaml docs
# directory full of samples was skipped entirely, which is the shape of
# gap this project keeps finding between a capability and its wiring.
INGEST_PATTERN = (r".*\.(py|md|markdown|txt|rst|"
                  + "|".join(sorted(e.lstrip(".") for e in CODE_LANGUAGES))
                  + r")$")


def plan_ingest(docs_dir, pattern=INGEST_PATTERN,
                skip_dirs=SKIP_DIRS, exclude=()):
    pairs, rx, skipped, binaries, dropped = [], re.compile(pattern), [], [], []
    for root, dirs, files in os.walk(docs_dir):
        # Pruned in place -- os.walk reads `dirs` back to decide where to
        # descend. Pointing this at a project root is the documented use,
        # and .venv alone can outnumber the real docs a thousand to one:
        # the index still looks fine, and every answer comes from
        # site-packages.
        if pruned := [d for d in dirs if d in skip_dirs]:
            skipped += [os.path.relpath(os.path.join(root, d), docs_dir)
                        for d in sorted(pruned)]
            dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fn in sorted(files):
            if not rx.match(fn):
                continue
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, docs_dir)
            if _excluded(rel, exclude):
                dropped.append(rel)
                continue
            if _is_binary(p):
                # errors="ignore" turns a binary file into text rather than
                # refusing it, so a .txt that is really a blob became chunks
                # of mojibake sitting in the index at whatever score they
                # happen to earn.
                binaries.append(rel)
                continue
            with open(p, encoding="utf-8", errors="ignore") as f:
                pairs += chunk_file(rel, f.read())
    if not pairs:
        raise ValueError(f"no files matched under {docs_dir}")

    # Identical text indexed twice competes with itself for the k slots the
    # gate has to spend. A vendored copy of a doc, or the same licence header
    # on forty files, can fill every slot with one passage and crowd out the
    # rest. First occurrence keeps the source.
    seen, unique = set(), []
    for chunk, source in pairs:
        if chunk not in seen:
            seen.add(chunk)
            unique.append((chunk, source))

    return IngestPlan(
        root=docs_dir,
        chunks=tuple(c for c, _ in unique),
        sources=tuple(s for _, s in unique),
        skipped_dirs=tuple(skipped),
        binaries=tuple(binaries),
        duplicates=len(pairs) - len(unique),
        excluded=tuple(dropped),
    )


def _excluded(rel, patterns):
    """A glob against the path, or a directory prefix of it."""
    for pat in patterns:
        bare = pat.rstrip("/" + os.sep)
        if fnmatch.fnmatch(rel, pat) or rel == bare \
                or rel.startswith(bare + os.sep):
            return True
    return False


def plan_notices(plan):
    """What the walk decided on its own, one line each."""
    out = []
    if plan.skipped_dirs:
        shown = ", ".join(plan.skipped_dirs[:5])
        more = (f" (+{len(plan.skipped_dirs) - 5} more)"
                if len(plan.skipped_dirs) > 5 else "")
        out.append(f"[rag] skipped {len(plan.skipped_dirs)} directories: {shown}{more}")
    if plan.binaries:
        out.append(f"[rag] skipped {len(plan.binaries)} binary files: "
                   f"{', '.join(plan.binaries[:5])}")
    if plan.excluded:
        out.append(f"[rag] excluded {len(plan.excluded)} files: "
                   f"{', '.join(plan.excluded[:5])}")
    if plan.duplicates:
        out.append(f"[rag] dropped {plan.duplicates} duplicate chunks")
    return out


def render_plan(plan, limit=25):
    """The review: every file that would be indexed, and what was left out."""
    lines = [f"\n{len(plan.chunks)} chunks from {len(plan.per_file)} files "
             f"under {plan.root}"]
    for path, count in plan.per_file[:limit]:
        lines.append(f"  {count:>4}  {path}")
    if len(plan.per_file) > limit:
        lines.append(f"  ... {len(plan.per_file) - limit} more files")
    lines += ["  " + n for n in plan_notices(plan)]

    # The names the index will know. Shown because it is the fastest way to
    # tell a docs directory that covers the API from one that does not: if the
    # modules listed here are not the library's, neither is the index.
    names = extract_symbols(plan.chunks)
    if names:
        top = ", ".join(f"{mod} ({n})" for mod, n in modules(names)[:6])
        lines.append(f"  [rag] {len(names)} qualified names -- {top}")
    return "\n".join(lines)


class DocStore:
    def __init__(self, embedder, path="docstore"):
        self.embedder = embedder
        self.path = path
        self.vectors = None
        self.chunks = []
        self.sources = []
        self._postings = {}
        self._idf = {}
        # Every qualified name the docs mention, derived on demand. Like the
        # lexical index it is rebuilt from the chunks rather than stored --
        # another file on disk is another thing that can drift out of step with
        # the vectors -- but unlike it, nothing needs it until something fails.
        self._symbols = None

    def ingest_plan(self, plan, verbose=True):
        """Embed a reviewed plan. The expensive half of ingesting."""
        self.chunks, self.sources = list(plan.chunks), list(plan.sources)
        self.vectors = self.embedder.embed_docs(self.chunks).astype("float32")
        self._build_lexical()
        self._symbols = None
        if verbose:
            print(f"[rag] ingested {len(self.chunks)} chunks from {plan.root}")
        return len(self.chunks)

    def ingest_dir(self, docs_dir, pattern=INGEST_PATTERN,
                   verbose=True, skip_dirs=SKIP_DIRS, exclude=()):
        """Plan and embed in one step, for callers with nobody to ask."""
        plan = plan_ingest(docs_dir, pattern=pattern, skip_dirs=skip_dirs,
                           exclude=exclude)
        if verbose:
            for notice in plan_notices(plan):
                print(notice)
        return self.ingest_plan(plan, verbose=verbose)

    # ---- lexical index ---------------------------------------------------

    @property
    def symbols(self):
        """Every qualified name the docs mention.

        Computed on first use, not at load: it costs a full pass over the
        chunks, and its only consumer is the did-you-mean hint, which is
        reached solely from a failed run. A generation that works first time
        should not pay for it.
        """
        if self._symbols is None:
            self._symbols = extract_symbols(self.chunks)
        return self._symbols

    def _build_lexical(self):
        """Rebuilt from the chunks rather than stored.

        Persisting it would add a third file to keep in step with the other
        two, and a file that can drift is the thing `load` exists to refuse.
        Recomputing costs a pass over text already in memory.

        The result is an INVERTED index -- token -> the chunks holding it --
        not a token set per chunk. Both answer the same question; only one
        answers it without visiting every chunk.
        """
        postings = {}
        for i, chunk in enumerate(self.chunks):
            for token in tokenize(chunk):
                postings.setdefault(token, []).append(i)
        n = len(self.chunks)
        # log((n+1)/(df+1)) rather than log(1 + n/df): a token in EVERY chunk
        # weighs exactly zero. Without that, a query of nothing but stopwords
        # scores near 1 against any chunk containing them, and clears the gate
        # on words that distinguish nothing.
        self._idf = {t: math.log((n + 1) / (len(rows) + 1))
                     for t, rows in postings.items()}
        self._postings = {t: np.asarray(rows, dtype=np.intp)
                          for t, rows in postings.items()}

    def _lexical(self, query):
        """Share of the query's rare tokens each chunk contains, in [0, 1].

        Absolute, not normalised per query -- which is what lets it share a
        threshold with cosine. A token the corpus has never seen weighs
        nothing, so a query of only unknown tokens scores 0 rather than
        dividing by zero or trivially scoring 1.

        Only chunks that actually contain a query token are touched. Every
        other chunk scores zero by construction, and the old version proved
        that by visiting all of them: a rare symbol reached three chunks out of
        seven thousand and paid for the other 7487.
        """
        # Accumulated at float64 and cast once at the end. Summing weights
        # into a float32 array instead put a full match at 0.99999988, which
        # is harmless against a threshold and needlessly untidy in `explain`.
        scores = np.zeros(len(self.chunks), dtype="float64")
        # A token the corpus has never seen counts in the DENOMINATOR at the
        # weight of the rarest token it does know. Dropping it instead was the
        # bug behind "the gate never refuses": the score became the share of a
        # query's KNOWN tokens, so `cheapest flights to Lisbon in March` scored
        # a perfect 1.000 against OCaml documentation -- one incidental token
        # existed, and failing to explain the other five cost nothing. An
        # unseen token is as informative as the rarest seen one; not matching
        # it has to cost something.
        rarest = max(self._idf.values(), default=0.0)
        scores_seen = 0.0
        total = 0.0
        for token in tokenize(query):
            weight = self._idf.get(token, 0.0)
            if not weight:
                total += rarest
                continue
            scores_seen += weight
            total += weight
            # Safe as a plain scatter because a chunk contributes each token
            # once: postings are built from a per-chunk token SET, so no index
            # repeats and none of numpy's buffered-duplicate trap applies.
            scores[self._postings[token]] += weight
        # A query with nothing the corpus knows scores zero everywhere rather
        # than dividing by zero -- or, worse, trivially scoring 1.
        if not total or not scores_seen:
            return np.zeros(len(self.chunks), dtype="float32")
        return (scores / total).astype("float32")

    def _scores(self, query):
        """(combined, cosine, lexical) for every chunk."""
        qv = self.embedder.embed_query(query).astype("float32")
        # Two models at the same dimension produce comparable-looking garbage;
        # at different dimensions numpy raises about shapes and says nothing
        # about why. Either way the index was built by something else.
        if qv.shape[0] != self.vectors.shape[1]:
            raise StoreError(
                f"this index holds {self.vectors.shape[1]}-dimensional vectors "
                f"and the embedder produces {qv.shape[0]} -- it was built by a "
                f"different model. Re-run `ingest`."
            )
        cosine = self.vectors @ qv
        lexical = self._lexical(query)
        return cosine + LEXICAL_WEIGHT * lexical, cosine, lexical

    def search(self, query, k=3, min_score=0.8, min_lexical=0.9):
        """Top k above the gate. -> [(chunk, source, score)].

        0.8 rather than the 0.3 this inherited from when the score was cosine
        alone. Measured over 3044 chunks of real documentation with eleven
        queries, five of them deliberately unrelated: relevant queries land at
        1.10-1.34 and unrelated ones at 0.50-0.70, so anything in that gap
        separates them. The separation is the measured part; the exact number
        is a judgement, and it is set nearer the junk end because a too-tight
        gate drops documentation the model needed and does it silently.

        `min_lexical` keeps the one thing the higher threshold would otherwise
        have killed: a chunk containing essentially every rare token of the
        query is retrieved even with a cosine of zero, which is the case the
        lexical signal exists for and the case embeddings are worst at. That
        clause is only safe now -- before unknown tokens counted against a
        match, an unrelated question scored a perfect 1.0 lexical and would
        have walked straight through it.
        """
        # An empty query embeds to something, and that something scores against
        # every chunk. Whatever comes back is not a match for anything.
        if self.vectors is None or not self.chunks or not query.strip():
            return []
        scores, _, lexical = self._scores(query)
        order = np.argsort(-scores)[:k]
        return [(self.chunks[i], self.sources[i], float(scores[i]))
                for i in order
                if scores[i] >= min_score or lexical[i] >= min_lexical]

    def explain(self, query, k=5):
        """Top k with both signals separated -- why a hit ranked where it did.

        The two answer different questions, and which one carried a hit is the
        thing worth seeing: cosine says "about the same subject", lexical says
        "contains the name you typed".
        """
        if self.vectors is None or not self.chunks or not query.strip():
            return []
        scores, cosine, lexical = self._scores(query)
        return [(self.sources[i], float(scores[i]), float(cosine[i]),
                 float(lexical[i])) for i in np.argsort(-scores)[:k]]

    def save(self):
        # Saving an un-ingested store writes `None` into the .npy as a pickled
        # object, and np.load then refuses it with a message about pickles that
        # names nothing real. Refuse here, where the cause is still visible.
        if self.vectors is None:
            raise StoreError("nothing to save -- ingest before saving")

        meta = {"chunks": self.chunks, "sources": self.sources,
                "embedder": getattr(self.embedder, "model_name", "")}

        # Write beside the target and rename. Two renames are not atomic as a
        # pair, so this shrinks the window rather than closing it -- what slips
        # through is a vector/chunk count mismatch, which `load` refuses.
        # The temp name ends in .npy on purpose: np.save appends the suffix to
        # any name that lacks it, so `idx.npy.tmp` would land on disk as
        # `idx.npy.tmp.npy` and the rename would find nothing.
        npy_tmp, json_tmp = self.path + ".tmp.npy", self.path + ".json.tmp"
        np.save(npy_tmp, self.vectors)
        with open(json_tmp, "w") as f:
            json.dump(meta, f)
        os.replace(npy_tmp, self.path + ".npy")
        os.replace(json_tmp, self.path + ".json")

    def load(self, verbose=True):
        npy, meta_path = self.path + ".npy", self.path + ".json"
        for p in (npy, meta_path):
            if not os.path.exists(p):
                raise StoreError(f"no index at {p} -- run `ingest` first")

        try:
            # allow_pickle stays off (the numpy default): an index is plain
            # floats, and a store file is exactly the kind of thing that should
            # not be able to execute on load.
            vectors = np.load(npy)
            with open(meta_path) as f:
                meta = json.load(f)
            chunks, sources = meta["chunks"], meta["sources"]
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as e:
            raise StoreError(f"{self.path} is not a readable index: {e}") from e

        # THE check. `search` looks chunks up by a vector's row index, so a
        # count mismatch does not crash -- it pairs a chunk with someone else's
        # source and someone else's score, and injects documentation under a
        # filename it never came from. Silent, and wrong in the one direction
        # retrieval must never be wrong in.
        if vectors.ndim != 2 or len(chunks) != vectors.shape[0] \
                or len(sources) != len(chunks):
            raise StoreError(
                f"{self.path} is inconsistent: {getattr(vectors, 'shape', '?')} "
                f"vectors against {len(chunks)} chunks and {len(sources)} "
                f"sources. Re-run `ingest`."
            )

        # An index built by another model scores nothing correctly. Compared
        # only when both sides name themselves, so an injected fake embedder
        # stays usable.
        built_by, mine = meta.get("embedder", ""), getattr(self.embedder, "model_name", "")
        if built_by and mine and built_by != mine:
            raise StoreError(
                f"{self.path} was built with {built_by}, this run embeds with "
                f"{mine} -- the scores would be meaningless. Re-run `ingest`."
            )
        # Indexes written before the field existed are not corrupt, just
        # unverifiable. Say so once rather than bricking them.
        if "embedder" not in meta and verbose:
            print(f"[rag] {self.path} predates model tracking -- "
                  f"cannot confirm it was built with this embedder")

        self.vectors, self.chunks, self.sources = vectors, chunks, sources
        self._build_lexical()
        self._symbols = None
        return self


# ---- retrieve-when-needed gate + injection ------------------------------

def retrieve_context(store, query, k=3, min_score=0.3, max_chars=1500):
    hits = store.search(query, k=k, min_score=min_score)
    if not hits:
        return ""
    blocks, used = [], 0
    for chunk, source, _score in hits:
        block = f"# doc: {source}\n{chunk}"
        # `continue`, not `break`: one oversized top hit used to discard every
        # smaller one below it. Prompt order stops matching score order, which
        # is the cheaper of the two costs.
        if used + len(block) > max_chars:
            continue
        blocks.append(block)
        used += len(block)
    # A header with nothing under it is truthy, so the caller reported
    # injecting context and the model was handed a promise of documentation
    # followed by none of it.
    if not blocks:
        return ""
    return "Relevant documentation:\n\n" + "\n\n".join(blocks)
