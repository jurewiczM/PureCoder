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
    purecoder ingest <docs_dir> [--store S]   # build a RAG index
    purecoder ask    "<spec>" [--store S]      # code, doc-grounded via RAG
    purecoder status                          # print system status
"""

import argparse
import os
import sys

from . import languages
from .client import PureCoder
from .execute import generate_validated_python, unsupported_language
from .validate import generate_validated


def resolve_language(args):
    """The LanguageSpec for this run, or None if we must not proceed.

    Two refusals, both deliberate. A language we cannot execute is refused
    outright -- there is no "generated but unchecked" tier, because emitting
    something unverified is the claim this project exists not to make. And a
    spec that asks for one language while --lang says another is refused
    rather than silently resolved: that mismatch is how `--lang python` plus
    "a C++ Dijkstra" used to yield `import heapq`.
    """
    try:
        spec = languages.get(getattr(args, "lang", "python"))
    except KeyError as e:
        # KeyError's str() is the repr of its argument, quotes and all.
        print(e.args[0])
        return None

    ok, why = spec.available()
    if not ok:
        print(f"Cannot generate {spec.name}: {why}.")
        runnable = [n for n in languages.names() if languages.get(n).available()[0]]
        print(f"Available right now: {', '.join(runnable)}.")
        return None

    # The spec's own words override the flag only to catch a contradiction.
    asked = unsupported_language(getattr(args, "spec", "") or "")
    if asked and asked not in (spec.name, *spec.aliases):
        print(f"--lang is {spec.name}, but the spec asks for {asked}. "
              f"Rerun with --lang {asked} if that is what you meant.")
        return None
    return spec


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


def cmd_code(pc, args):
    spec = resolve_language(args)
    if spec is None:
        return 1
    _print_result(generate_validated_python(
        pc, args.spec, max_retries=args.retries, spec=spec,
        use_contract=resolve_contract(args, default=False)),
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
    r = scaffold_project(pc, args.name, args.spec,
                         outdir=args.outdir or args.name,
                         max_retries=args.retries, spec=spec,
                         use_contract=resolve_contract(args, default=True))
    print(f"\nscaffold {'complete' if r['ok'] else 'incomplete'} -> {r['outdir']}/")


def cmd_ingest(pc, args):
    from .rag import DocStore, Embedder
    store = DocStore(Embedder(device=args.device), path=args.store)
    n = store.ingest_dir(args.docs_dir)
    store.save()
    print(f"indexed {n} chunks -> {args.store}.npy / .json")


def cmd_ask(pc, args):
    # `ask` is `code` with retrieval in front of it, so it resolves the
    # language the same way. Without this, --lang was parsed and silently
    # dropped here -- the one command that would answer a C++ question in
    # Python, which is the exact failure the registry exists to stop.
    spec = resolve_language(args)
    if spec is None:
        return 1
    from .rag import DocStore, Embedder, retrieve_context
    store = DocStore(Embedder(device=args.device), path=args.store).load()
    ctx = retrieve_context(store, args.spec)
    if ctx:
        print(f"[rag] injected {len(ctx)} chars of context")
    else:
        print("[rag] no relevant docs above threshold -- generating without context")
    # doc-grounded, still execution-validated
    task = f"{ctx}\n\n{args.spec}" if ctx else args.spec
    _print_result(generate_validated_python(
        pc, task, max_retries=args.retries, spec=spec,
        use_contract=resolve_contract(args, default=False)),
        show_tests=args.show_tests)


def cmd_learn(pc, args):
    from .bootstrap import learn_language
    from .rag import DocStore, Embedder, retrieve_context

    store = DocStore(Embedder(device=args.device), path=args.store)
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
                         live_check=not args.no_live)
    if not res["ok"]:
        print(f"\nnot registered: {res['error']}")
        return 1
    print(f"\n{args.name} is registered. It is a drafted entry, proven by "
          f"probe rather than written by hand -- try it on something small "
          f"first:\n  purecoder --lang {args.name} code \"...\"")


def cmd_status(pc, args):
    from .status import print_status
    print_status(pc)


def main():
    p = argparse.ArgumentParser(prog="purecoder", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://localhost:8080",
                   help="llama-server base URL")
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--device", default="cuda", help="embedding device (cuda/cpu)")
    p.add_argument("--store", default="docstore", help="RAG index path")
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
        sub.add_parser(name).add_argument("spec")
    sp = sub.add_parser("project")
    sp.add_argument("name")
    sp.add_argument("spec")
    sp.add_argument("outdir", nargs="?")
    sub.add_parser("ingest").add_argument("docs_dir")
    sl = sub.add_parser("learn")
    sl.add_argument("name")
    sl.add_argument("docs_dir")
    sl.add_argument("--ext", required=True,
                    help="source file extension, e.g. .zig")
    sl.add_argument("--no-live", action="store_true",
                    help="skip the live generation round (probes only)")
    sub.add_parser("status")

    args = p.parse_args()
    pc = PureCoder(base_url=args.url)

    return {
        "code": cmd_code, "env": cmd_env, "make": cmd_make,
        "project": cmd_project, "ingest": cmd_ingest, "ask": cmd_ask,
        "learn": cmd_learn, "status": cmd_status,
    }[args.cmd](pc, args)


if __name__ == "__main__":
    sys.exit(main() or 0)
