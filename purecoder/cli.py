#!/usr/bin/env python3
"""
PureCoder CLI -- one entry point over the whole pipeline.

A constrained, code-only agentic coder that runs on ~6 GB of VRAM:
grammar-constrained generation, real-tool validation, execution-tested
code, doc-grounded retrieval, and whole-project scaffolding.

Usage:
    purecoder code   "<spec>"                 # execution-validated function
    purecoder env    "<spec>"                 # grammar-valid .env
    purecoder make   "<spec>"                 # validated Makefile
    purecoder project <name> "<spec>" [dir]   # scaffold a whole project
    purecoder ingest <docs_dir> [--store S]   # build a RAG index, reviewed
    purecoder ask    "<spec>" [--store S]     # code, doc-grounded via RAG
    purecoder learn  <name> <docs_dir>        # draft a language, prove it, keep its docs
    purecoder status                          # print system status
    purecoder serve [--port 8100]             # the pipeline over local HTTP

A language learned by `learn` keeps the index of its own documentation, so
`code --lang <name>` is doc-grounded with no second ingest and no --store.
"""

import argparse
import os
import sys

from . import languages
from .client import PureCoder
from .execute import generate_validated_python, unsupported_language
from .validate import generate_validated

# Where `ingest` writes and `ask` reads when nothing else says otherwise.
# `--store` defaults to None rather than this, so that "the user named an
# index" stays distinguishable from "use whatever is appropriate" -- which is
# what lets a learned language supply its own.
DEFAULT_STORE = "docstore"


def language_for(lang: str, spec_text: str = ""):
    """(LanguageSpec, reason). The decision, with nothing printed.

    Two refusals, both deliberate. A language we cannot execute is refused
    outright -- there is no "generated but unchecked" tier, because emitting
    something unverified is the claim this project exists not to make. And a
    spec that asks for one language while --lang says another is refused
    rather than silently resolved: that mismatch is how `--lang python` plus
    "a C++ Dijkstra" used to yield `import heapq`.

    Split from the printing so the HTTP surface can return the same refusals
    as text instead of reimplementing them -- two copies of this would drift,
    and the copy that drifts is the one that stops refusing.
    """
    try:
        spec = languages.get(lang)
    except KeyError as e:
        # KeyError's str() is the repr of its argument, quotes and all.
        return None, e.args[0]

    ok, why = spec.available()
    if not ok:
        runnable = [n for n in languages.names() if languages.get(n).available()[0]]
        return None, (f"Cannot generate {spec.name}: {why}.\n"
                      f"Available right now: {', '.join(runnable)}.")

    # The spec's own words override the flag only to catch a contradiction.
    asked = unsupported_language(spec_text or "")
    if asked and asked not in (spec.name, *spec.aliases):
        return None, (f"--lang is {spec.name}, but the spec asks for {asked}. "
                      f"Rerun with --lang {asked} if that is what you meant.")
    return spec, ""


def resolve_language(args):
    """The LanguageSpec for this run, or None if we must not proceed."""
    spec, why = language_for(getattr(args, "lang", "python"),
                             getattr(args, "spec", "") or "")
    if spec is None:
        print(why)
    return spec


def _docs_opts(args) -> dict:
    """The three retrieval settings a command carries, read off its namespace."""
    return {"store": getattr(args, "store", None),
            "device": getattr(args, "device", "cuda"),
            "no_docs": bool(getattr(args, "no_docs", False))}


def resolve_contract(args, default):
    """Most specific wins: explicit flag, then PURECODER_CONTRACT, then the
    per-command default."""
    if args.contract is not None:
        return args.contract
    env = os.environ.get("PURECODER_CONTRACT")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    return default


def _print_result(res, show_tests=False):
    contract = res.get("contract")
    if contract:
        from .contract import render_contract
        print(render_contract(contract))
    print("-" * 60)
    print(res["text"])
    print("-" * 60)
    ok = res.get("ok")
    print(f"ok={ok}  attempts={res.get('attempts')}")
    if show_tests and res.get("tests"):
        print("\n[tests used]\n" + res["tests"])
    if not ok and res.get("error"):
        print(f"error: {res['error']}")


def open_docs(path, device, required=False):
    """Load an index, or explain why generation will go on without one.

    Retrieval is an optional install and an index is a file on disk, so every
    way this fails is ordinary. A learned language must still be usable on a
    machine with no `sentence-transformers` and no index -- the harness is what
    proves its output, and that needs neither. `required` is for `ask`, whose
    whole purpose is the documentation.
    """
    from .rag import DocStore, Embedder, MissingRetrieval

    missing = [p for p in (path + ".npy", path + ".json")
               if not os.path.exists(p)]
    if missing:
        if required:
            print(f"no index at {path} ({', '.join(missing)} missing).\n"
                  f"  purecoder ingest <docs_dir> --store {path}")
        return None
    try:
        return DocStore(Embedder(device=device), path=path).load()
    except MissingRetrieval as e:
        print(f"{e}\n  -- generating without the documentation")
        return None
    except ValueError as e:          # StoreError: present, but not trustworthy
        print(f"cannot use the index at {path}: {e}"
              f"\n  -- generating without the documentation")
        return None


def ground_in_docs(spec, query, store=None, device="cuda",
                   no_docs=False, required=False):
    """(context, error_hint, error_docs) for this run: blocks, not a task.

    One resolver for every command that generates code, so they cannot drift
    apart the way `ask` and `code` already did once. An explicit --store wins;
    otherwise a learned language reads the docs it was learned from, which is
    what `learn` keeping that index buys. A hand-written language with no
    --store has nothing to read and is unaffected.

    It hands back the context rather than a finished prompt because the caller
    is the only one who knows where it belongs. `project` proved why: folding
    documentation into the description sent it to the README prompt too, and
    prose cannot use it.

    Three things, because retrieval happens twice. `context` is keyed on the
    SPEC and grounds the first attempt. `error_docs` is keyed on the
    TOOLCHAIN'S ERROR and grounds a retry -- a different and usually better
    query, since `Unbound value List.fold` names exactly what the model did
    not know, where prose only says what the user wanted. It excludes whatever
    the first pass already injected, so a retry adds documentation instead of
    repeating it.

    "" means nothing was retrieved -- the hints are still live, since the
    symbol library does not depend on any chunk clearing the threshold. None
    means the documentation was required and is not there.
    """
    from .langstore import docs_index_path
    from .rag import retrieve
    from .symbols import did_you_mean

    if no_docs:
        return (None, None, None) if required else ("", None, None)

    path = store
    if path is None and spec is not None and spec.docs_store:
        path = str(docs_index_path(spec.docs_store))
        print(f"[rag] using the {spec.name} docs from `learn`")
    if path is None:
        if not required:
            return "", None, None
        path = DEFAULT_STORE

    opened = open_docs(path, device, required=required)
    if opened is None:
        return (None, None, None) if required else ("", None, None)

    ctx, used = retrieve(opened, query)
    print(f"[rag] {len(ctx)} chars of documentation" if ctx else
          "[rag] nothing above the threshold -- generating without context")

    def error_docs(error):
        """Documentation for what the toolchain just complained about.

        Half the budget of the first pass: this arrives in a retry prompt that
        already carries the previous attempt, the tests, the error and the
        quoted source, and this project's finding 4 is that too much context
        on a small card causes degeneration rather than coherence.
        """
        block, _ = retrieve(opened, error, k=2, max_chars=700, exclude=used,
                            header="Documentation for that error:")
        return block

    return ctx, lambda err: did_you_mean(err, opened.symbols), error_docs


def confirm_tests(tests, evidence, ask=input):
    """Show the suite and the proof that it fails, then ask. -> True to go on.

    The TDD ritual, made visible: this is the moment the user is looking at
    what will judge the implementation, before any implementation exists.
    Silence is a no, like every other confirmation here.
    """
    print("\n" + "-" * 60)
    print("These tests were written from your request, and they FAIL against "
          "an implementation that does nothing:")
    print("-" * 60)
    print(tests.rstrip())
    print("-" * 60)
    detail = (evidence or "").strip().splitlines()
    for line in detail[-4:]:
        print(f"  {line}")
    print("-" * 60)
    return ask("Write an implementation that satisfies them? [y/N] ").strip()\
        .lower() in ("y", "yes")


def cmd_code(pc, args):
    spec = resolve_language(args)
    if spec is None:
        return 1
    context, hint, docs_for_error = ground_in_docs(spec, args.spec,
                                                   **_docs_opts(args))
    tdd = bool(getattr(args, "tdd", False))
    # Test-first cannot stub without a name, and the name comes from the
    # contract -- so the flag implies it rather than failing later on a
    # combination the user did not know was required.
    extra = {}
    if tdd:
        extra = {"tdd": True,
                 "confirm_tests": None if getattr(args, "yes", False)
                 else confirm_tests}
    _print_result(generate_validated_python(
        pc, args.spec, context=context, max_retries=args.retries, spec=spec,
        use_contract=True if tdd else resolve_contract(args, default=False),
        error_hint=hint, error_docs=docs_for_error,
        # `--with` lives on this subcommand alone, so `getattr` is the honest
        # read rather than a default nobody set.
        packages=tuple(getattr(args, "packages", None) or ()),
        **extra),
        show_tests=args.show_tests)


def cmd_env(pc, args):
    _print_result(generate_validated(pc, "env", args.spec,
                                     max_retries=args.retries))


def cmd_make(pc, args):
    _print_result(generate_validated(pc, "makefile", args.spec,
                                     max_retries=args.retries))


def cmd_project(pc, args):
    spec = resolve_language(args)
    if spec is None:
        return 1
    from .scaffold import scaffold_project
    # Grounds the code artifact only -- see scaffold_project for why the
    # Makefile, .env and README are deliberately left out of it.
    context, hint, docs_for_error = ground_in_docs(spec, args.spec,
                                                   **_docs_opts(args))
    r = scaffold_project(pc, args.name, args.spec,
                         outdir=args.outdir or args.name,
                         max_retries=args.retries, spec=spec,
                         use_contract=resolve_contract(args, default=True),
                         docs=context, error_hint=hint,
                         error_docs=docs_for_error)
    print(f"\nscaffold {'complete' if r['ok'] else 'incomplete'} -> {r['outdir']}/")


def review_plan(plan_for, exclude, interactive=True):
    """Show what would be indexed and let the user correct it.

    Planning is free -- it chunks, it does not embed -- so this runs before a
    model is loaded and an exclusion can be applied without paying twice. The
    same trust boundary `learn` uses for its commands: nothing expensive or
    persistent happens until someone has looked.

    Returns the accepted plan, or None if the user abandoned it.
    """
    from .rag import render_plan

    while True:
        try:
            plan = plan_for(tuple(exclude))
        except ValueError as e:
            print(f"nothing to index: {e}")
            return None
        print(render_plan(plan))
        if not interactive:
            return plan

        choice = input("\n[y] index this  [e] exclude paths  [n] abort: ").strip().lower()
        if choice in ("", "y"):
            return plan
        if choice == "n":
            print("nothing indexed.")
            return None
        if choice == "e":
            patterns = input("paths or globs to leave out: ").split()
            if patterns:
                exclude = list(exclude) + patterns
                # Printed so a session spent narrowing the index by hand can be
                # replayed without repeating it. A prompt that cannot be turned
                # back into a command is a dead end.
                flags = " ".join(f"--exclude {p}" for p in exclude)
                print(f"  replay non-interactively with: {flags}")


def cmd_ingest(pc, args):
    from .rag import DocStore, Embedder, MissingRetrieval, plan_ingest

    # --yes is for scripts; the isatty check is for pipelines that never had a
    # keyboard. `echo y | purecoder ...` is how this project is tested, and a
    # prompt blocking on a closed stdin would break that.
    interactive = not args.yes and sys.stdin.isatty()
    plan = review_plan(lambda ex: plan_ingest(args.docs_dir, exclude=ex),
                       args.exclude or [], interactive=interactive)
    if plan is None:
        return 1

    try:
        store = DocStore(Embedder(device=args.device),
                         path=args.store or DEFAULT_STORE)
        n = store.ingest_plan(plan)
        store.save()
    except MissingRetrieval as e:
        print(e)
        return 1
    except ValueError as e:      # StoreError included -- it is a ValueError
        print(f"nothing indexed: {e}")
        return 1
    print(f"indexed {n} chunks -> {args.store or DEFAULT_STORE}"
          f".npy / .json")


def cmd_ask(pc, args):
    # `ask` is `code` with retrieval in front of it, so it resolves the
    # language the same way. Without this, --lang was parsed and silently
    # dropped here -- the one command that would answer a C++ question in
    # Python, which is the exact failure the registry exists to stop.
    spec = resolve_language(args)
    if spec is None:
        return 1
    # `required`: an index is not an improvement here, it is the command.
    # Everything else -- which index, the did-you-mean hint, degrading on a
    # store that cannot be read -- is the same resolver `code` and `project`
    # use, so the three cannot drift apart again.
    context, hint, docs_for_error = ground_in_docs(spec, args.spec,
                                                   required=True,
                                                   **_docs_opts(args))
    if context is None:
        return 1
    _print_result(generate_validated_python(
        pc, args.spec, context=context, max_retries=args.retries, spec=spec,
        use_contract=resolve_contract(args, default=False),
        error_hint=hint, error_docs=docs_for_error),
        show_tests=args.show_tests)


def cmd_learn(pc, args):
    from .bootstrap import RESERVED_NAMES, learn_language
    from .langstore import docs_index_path
    from .rag import DocStore, Embedder, MissingRetrieval, retrieve_context

    # Before the ingest, not after it. `learn ocaml` embedded a whole docs
    # directory and only then reported that the name is reserved -- the work
    # was done, the index written, and the answer was always going to be no.
    # The scaffolder already refuses an unwired language before creating its
    # directory; this is the same rule on the same grounds.
    if args.name in RESERVED_NAMES:
        print(f"{args.name!r} is a reserved language -- it is either wired "
              f"already or refused on purpose, and a drafted spec may not "
              f"replace either")
        return 1

    # The index is built to draft the harness and then KEPT, so generating in
    # this language later can read the same documentation. It used to be thrown
    # away, which meant `ingest`ing the same directory a second time to get any
    # benefit from it.
    index = docs_index_path(args.name)
    try:
        store = DocStore(Embedder(device=args.device), path=str(index))
    except MissingRetrieval as e:
        print(e)
        return 1
    try:
        store.ingest_dir(args.docs_dir)
    except ValueError as e:
        # The chunker reads .py/.md/.markdown/.txt/.rst. A docs directory of
        # .html or .adoc is a plausible mistake and deserves a sentence, not a
        # traceback out of the middle of an ingest.
        print(f"no documentation to read: {e}")
        return 1

    res = learn_language(pc, args.name, args.ext, args.docs_dir,
                         retrieve=lambda q: retrieve_context(store, q),
                         live_check=not args.no_live,
                         max_retries=args.draft_retries,
                         docs_store=args.name,
                         want_project=not args.no_project)
    if not res["ok"]:
        print(f"\nnot registered: {res['error']}")
        # Naming the probe says WHICH check failed; its detail says why, and it
        # is the compiler's own diagnostic. Without this the refusal is the same
        # shape as an error the fix loop never gets to read.
        for probe in res["probes"]:
            if not probe.ok and probe.detail.strip():
                print(f"\n  {probe.name}:")
                for line in probe.detail.strip().splitlines()[:8]:
                    print(f"    {line}")
        return 1

    # Written only now. A failed run must not leave an index behind for a
    # language that was never registered -- the spec pointing at it does not
    # exist, so the files would be unreachable litter.
    index.parent.mkdir(parents=True, exist_ok=True)
    store.save()
    print(f"[learn] kept the docs index -> {index}.npy / .json")
    print(f"\n{args.name} is registered. It is a drafted entry, proven by "
          f"probe rather than written by hand -- try it on something small "
          f"first:\n  purecoder --lang {args.name} code \"...\"")
    # Two separate claims, so say which ones hold. A language with no layout is
    # fully usable by `code`; only `project` is out of reach.
    if languages.get(args.name).project is None:
        print(f"  It has no project layout, so `project --lang {args.name}` "
              f"will refuse. `code` and `ask` are unaffected.")
    else:
        print(f"  purecoder --lang {args.name} project demo \"...\"")


def cmd_measure(pc, args):
    """Run the contract measurement and print the table.

    Both arms are run here rather than by two invocations: the comparison is
    the measurement, and a model sampled across two separate runs of the CLI is
    a different experiment.
    """
    from .bench import format_report, measure

    report = measure(pc, repeats=args.repeats, max_retries=args.retries,
                     timeout=args.timeout)
    print(format_report(report["summary"]))
    diverged = report["summary"]
    # Non-zero when the grounded arm diverged at all: the run succeeded, but
    # the layer's claim did not hold cleanly, and a CI-style caller should be
    # able to see that without parsing the table.
    return 1 if diverged["grounded"]["diverged"] else 0


def cmd_status(pc, args):
    from .status import print_status
    print_status(pc)


def cmd_serve(pc, args):
    """The pipeline over HTTP, for callers that are not a terminal.

    Loopback by default and deliberately: `/code` runs model-authored code in a
    subprocess on this machine, so binding elsewhere would put that on the
    network. `--host` exists for a container's own interface, not as a
    suggestion.
    """
    from .server import serve
    serve(pc, host=args.host, port=args.port)
    return 0


def main():
    p = argparse.ArgumentParser(prog="purecoder", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://localhost:8080",
                   help="llama-server base URL")
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--device", default="cuda", help="embedding device (cuda/cpu)")
    p.add_argument("--store", default=None, metavar="PATH",
                   help=f"RAG index path (default: the index a learned "
                        f"language kept, else {DEFAULT_STORE})")
    p.add_argument("--no-docs", action="store_true",
                   help="ignore a learned language's own documentation")
    p.add_argument("--show-tests", action="store_true")
    p.add_argument("--lang", default="python", metavar="LANG",
                   help=f"language to generate and validate "
                        f"(default: python; known: {', '.join(languages.names())})")
    p.add_argument("--contract", dest="contract", action="store_true",
                   default=None,
                   help="derive a spec contract first (default: on for project)")
    p.add_argument("--no-contract", dest="contract", action="store_false",
                   help="skip contract derivation")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name in ("code", "env", "make", "ask"):
        parser = sub.add_parser(name)
        parser.add_argument("spec")
        if name == "code":
            parser.add_argument(
                "--tdd", action="store_true",
                help="test-first: derive a contract, write the tests, PROVE "
                     "they fail against a do-nothing implementation, and "
                     "confirm them before any code is written (python only)")
            parser.add_argument(
                "-y", "--yes", action="store_true",
                help="skip the test confirmation --tdd would ask for")
            parser.add_argument(
                "--with", dest="packages", action="append", metavar="PKG",
                help="a third-party package the code may import (repeatable). "
                     "Verified importable in the sandbox before anything is "
                     "generated; python only")
    sp = sub.add_parser("project")
    sp.add_argument("name")
    sp.add_argument("spec")
    sp.add_argument("outdir", nargs="?")
    si = sub.add_parser("ingest")
    si.add_argument("docs_dir")
    si.add_argument("-y", "--yes", action="store_true",
                    help="index without the review step")
    si.add_argument("--exclude", action="append", metavar="GLOB",
                    help="path or glob to leave out (repeatable)")
    sl = sub.add_parser("learn")
    sl.add_argument("name")
    sl.add_argument("docs_dir")
    sl.add_argument("--ext", required=True,
                    help="source file extension, e.g. .zig")
    sl.add_argument("--no-live", action="store_true",
                    help="skip the live generation round (probes only)")
    sl.add_argument("--no-project", action="store_true",
                    help="do not draft a project layout for this language")
    sl.add_argument("--draft-retries", type=int, default=2, metavar="N",
                    help="drafting attempts before giving up; each redraft "
                         "carries the failing probes' diagnostics (default: 2)")
    sm = sub.add_parser("measure")
    sm.add_argument("--repeats", type=int, default=1, metavar="N",
                    help="passes over the task set, both arms each (default: 1)")
    sm.add_argument("--timeout", type=int, default=10, metavar="SECONDS",
                    help="per-run execution timeout (default: 10)")
    sub.add_parser("status")
    ss = sub.add_parser("serve")
    ss.add_argument("--port", type=int, default=8100,
                    help="port to listen on (default: 8100)")
    ss.add_argument("--host", default="127.0.0.1",
                    help="interface to bind (default: 127.0.0.1 -- generated "
                         "code runs here, so do not expose it)")

    args = p.parse_args()
    pc = PureCoder(base_url=args.url)

    return {
        "code": cmd_code, "env": cmd_env, "make": cmd_make,
        "project": cmd_project, "ingest": cmd_ingest, "ask": cmd_ask,
        "learn": cmd_learn, "status": cmd_status, "measure": cmd_measure,
        "serve": cmd_serve,
    }[args.cmd](pc, args)


if __name__ == "__main__":
    sys.exit(main() or 0)
