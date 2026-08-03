"""
purecoder/symbols.py

What the indexed documentation actually names, and what to do with it.

A qualified name -- `Printf.eprintf`, `List.fold_left`, `os.path.join` -- is
the one thing in generated code that can be matched against the docs without a
parser for the language. Extracting them is easy. Using them is where the
design is.

**The obvious use does not work, and it was measured before it was believed.**
The first version flagged names whose module the docs describe but whose member
they never mention -- `List.fold` when the docs show `List.fold_left`. Run
against this project's own source with its own docs as the corpus, that
produced **45 findings, every one of them wrong**: `re.escape`, `ast.walk`,
`json.dump` are all real, and the docs simply had no reason to mention them.
The rule assumes documentation *enumerates* a module. Prose documentation never
does, so the check cannot tell an invented name from an undocumented one.

**The sound version inverts who decides.** The toolchain already knows when a
name is wrong -- "Unbound value List.fold", "module 're' has no attribute". It
just cannot say what to use instead, because it has never read the docs. So the
compiler rules on wrongness and the symbol library only answers "did you mean",
which needs no completeness assumption at all. Worst case it finds no close
match and says nothing.

The second use has no failure mode either: telling the writer which names the
docs *do* define is grounding, not policing.
"""

import difflib
import re

# A dotted chain of identifiers. Deliberately not anchored to capitalisation:
# `Printf.eprintf` and `os.path.join` are the same shape and both matter.
_QUALIFIED = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")

# `README.md` and `arxiv.org` are the same shape as `List.map`. Documentation
# is full of both, and a filename admitted as a symbol pollutes the library
# with a module that does not exist.
_NOT_A_MEMBER = frozenset({
    "md", "markdown", "txt", "rst", "py", "js", "ts", "json", "yml", "yaml",
    "toml", "cfg", "ini", "npy", "gbnf", "ml", "mli", "cpp", "cc", "h", "hpp",
    "rs", "go", "java", "cs", "sh", "html", "css", "png", "svg", "jpg", "gif",
    "pdf", "csv", "lock", "log", "org", "com", "io", "net", "dev", "gov",
})

# Whatever a compiler puts in quotes is the thing it is complaining about --
# every toolchain here does it, and it needs no per-language parsing.
_QUOTED = re.compile(r"[`'\"]([A-Za-z_][A-Za-z0-9_.]*)[`'\"]")


def qualified_names(text):
    """Every dotted identifier chain in a string, minus the ones that are not
    names at all -- filenames, domains, and single-letter locals."""
    out = set()
    for name in _QUALIFIED.findall(text):
        head, _, tail = name.rpartition(".")
        if tail.lower() in _NOT_A_MEMBER or len(head.rsplit(".", 1)[-1]) < 2:
            continue
        out.add(name)
    return out


def extract_symbols(chunks):
    """Every qualified name the documentation mentions.

    Mention is the bar, not definition: prose about an API still evidences that
    the API exists, and nothing downstream treats this set as complete.
    """
    names = set()
    for chunk in chunks:
        names |= qualified_names(chunk)
    return frozenset(names)


def modules(names):
    """(module, member count) for what was found, commonest first -- the
    summary a user reads to see whether the index caught the real API."""
    counts = {}
    for name in names:
        prefix = name.rsplit(".", 1)[0]
        counts[prefix] = counts.get(prefix, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def did_you_mean(error, names, limit=3, cutoff=0.7):
    """Names the toolchain complained about, answered from the docs.

    Only names the error itself raises are considered, so this can never
    contradict a working run -- it is reached solely when something already
    failed, about a name the toolchain already rejected.
    """
    if not error or not names:
        return ""
    quoted = _QUOTED.findall(error)
    # Some toolchains quote the module and the member separately -- Python's
    # "module 're' has no attribute 'escap'" is two quotes describing one name.
    # Joining adjacent pairs reassembles it without knowing whose message it is.
    joined = {f"{a}.{b}" for a, b in zip(quoted, quoted[1:], strict=False)}
    suspects = sorted((set(quoted) | joined | qualified_names(error))
                      - set(names))
    lines = []
    for suspect in suspects:
        near = difflib.get_close_matches(suspect, names, n=limit, cutoff=cutoff)
        if near:
            lines.append(f"  {suspect} -> the documentation defines "
                         f"{', '.join(near)}")
    if not lines:
        return ""
    return "The documentation does not contain every name in that error:\n" \
           + "\n".join(lines)
