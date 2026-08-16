#!/usr/bin/env python3
"""Run the corpus and report what the pipeline DID, not how clever the model is.

The ten-task set saturates: corrected for harness defects, five of six
languages score 10/10 and only OCaml discriminates. Re-running it for a pass
rate would produce a number that looks like a capability measurement and is
not one, which is the mistake this directory exists to avoid.

So this measures the harness instead, which is the part that is actually under
development:

  * how many attempts a passing run costs -- the 30B's whole case rests on
    halving this, not on the score
  * how often the pipeline refuses, and which role ran out when it did
  * how often the role it ran out on is the tester, which every previous
    measurement has pointed at

The last one is the interesting column. `benchlog.py` tries to answer it by
classifying finished transcripts and got four of seven wrong on its first live
run. This reads the ledger the loop wrote while the run was happening.

    scripts/bench/attribution.py python ocaml
    scripts/bench/attribution.py --tasks sum_list,gcd python

Needs a live llama-server. Writes a transcript per task under $BENCH.
"""

import argparse
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from purecoder import languages  # noqa: E402
from purecoder.client import PureCoder  # noqa: E402
from purecoder.execute import generate_validated_python  # noqa: E402

CORPUS = pathlib.Path(__file__).with_name("tasks.tsv")

# The same casing rule batch.sh applies, and for the same reason: the contract
# is derived from prose by a model that is never told the language, so a spec
# naming `is_palindrome` yields that target everywhere and a C# suite calling
# `IsPalindrome` fails the gate on every attempt. That is a naming confound,
# not a result.
CASE = {"javascript": "camel", "c#": "pascal"}

# Every task in this corpus asks for a FUNCTION, and SQLite has no user-defined
# functions without a host language -- so the tester writes a check calling
# `sum_list(...)` and every attempt dies on `no such function: sum_list`.
#
# Measured before this skip existed: sql scored 0/10, all ten "stopped on
# writer". That number is a fact about the task set, not about the model or the
# harness, and publishing it as a score would be exactly the misattribution
# this directory exists to prevent. SQL passes its own five bootstrap probes
# and a hand-written task in scripts/demo.sh; what it cannot do is be asked for
# a function.
CANNOT_TAKE_FUNCTION_TASKS = {
    "sql": "SQLite has no user-defined functions, and every task here asks for "
           "one -- the corpus is the wrong shape for this language, not the "
           "language the wrong tool for the corpus",
}


def cased(name: str, convention: str) -> str:
    if convention == "snake":
        return name
    head, *rest = name.split("_")
    out = head + "".join(w.capitalize() for w in rest)
    return out[0].upper() + out[1:] if convention == "pascal" else out


def tasks():
    for line in CORPUS.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        name, spec = line.split("\t", 1)
        yield name.strip(), spec.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("languages", nargs="*", default=[])
    ap.add_argument("--tasks", default="", help="comma-separated subset")
    ap.add_argument("--retries", type=int, default=4)
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("--out", default=os.environ.get(
        "BENCH", os.path.expanduser("~/models/bench")))
    args = ap.parse_args()

    pc = PureCoder(base_url=args.url)
    outdir = pathlib.Path(args.out) / "attribution"
    outdir.mkdir(parents=True, exist_ok=True)

    wanted = args.languages or [n for n in languages.names()
                                if languages.get(n).available()[0]]
    only = {t.strip() for t in args.tasks.split(",") if t.strip()}
    corpus = [(n, s) for n, s in tasks() if not only or n in only]

    rows, started = [], time.time()
    for lang in wanted:
        spec = languages.get(lang)
        ok, why = spec.available()
        if not ok:
            print(f"{lang}: skipped -- {why}")
            continue
        if lang == "ocaml":
            # batch.sh REFUSES this: OCaml is the ignorant case retrieval
            # exists for, so an ungrounded column cannot be distinguished from
            # a regression. That guard was written for the 7B. This runner
            # produces the column and says so rather than silently disagreeing
            # with the other tool -- the two have not been reconciled.
            print("ocaml: ungrounded -- batch.sh refuses this measurement; "
                  "not comparable to grounded OCaml numbers")
        if lang in CANNOT_TAKE_FUNCTION_TASKS:
            print(f"{lang}: skipped -- {CANNOT_TAKE_FUNCTION_TASKS[lang]}")
            continue
        for name, template in corpus:
            fn = cased(name, CASE.get(lang, "snake"))
            prompt = template.replace("{fn}", fn)
            t0 = time.time()
            res = generate_validated_python(
                pc, prompt, spec=spec, max_retries=args.retries,
                verbose=False, context="")
            row = {
                "language": lang, "task": name,
                "ok": bool(res["ok"]), "attempts": res["attempts"],
                "seconds": round(time.time() - t0, 1),
                "stopped_on": res["agents"]["stopped_on"],
                "roles": {r["name"]: r["attempts"]
                          for r in res["agents"]["roles"]},
                "error": res["error"][:160],
            }
            rows.append(row)
            (outdir / f"{lang.replace('#', 'sharp')}-{name}.log").write_text(
                f"# {lang} / {name}\n# {prompt}\n\n{res['text']}\n\n"
                f"--- tests ---\n{res['tests']}\n\n--- {row} ---\n")
            mark = "ok " if row["ok"] else "REF"
            print(f"  {mark} {lang:11} {name:15} {row['attempts']} attempts "
                  f"{row['seconds']:5.1f}s  {row['stopped_on']}")

    report(rows, time.time() - started)
    (outdir / "rows.json").write_text(json.dumps(rows, indent=2))
    print(f"\nrows -> {outdir / 'rows.json'}")
    return 0


def report(rows, elapsed):
    if not rows:
        print("nothing ran")
        return
    print(f"\n{'language':12} {'ran':>4} {'passed':>7} {'median':>7} "
          f"{'refused':>8}  stopped on")
    print("-" * 64)
    langs = sorted({r["language"] for r in rows})
    for lang in langs:
        mine = [r for r in rows if r["language"] == lang]
        passed = [r for r in mine if r["ok"]]
        att = sorted(r["attempts"] for r in passed)
        median = att[len(att) // 2] if att else 0
        refused = [r for r in mine if not r["ok"]]
        blame = {}
        for r in refused:
            blame[r["stopped_on"]] = blame.get(r["stopped_on"], 0) + 1
        who = ", ".join(f"{k or 'before any role'} x{v}"
                        for k, v in sorted(blame.items())) or "-"
        print(f"{lang:12} {len(mine):>4} {len(passed):>7} {median:>7} "
              f"{len(refused):>8}  {who}")

    total = len(rows)
    passed = sum(r["ok"] for r in rows)
    first = sum(1 for r in rows if r["ok"] and r["attempts"] == 1)
    print("-" * 64)
    print(f"{total} runs, {passed} passed, {first} of those on the first "
          f"attempt, in {elapsed / 60:.0f} min")
    print()
    print("Read the pass column with the caveat this corpus carries: it")
    print("SATURATES. Five of six languages scored 10/10 once the harness")
    print("defects were fixed, so a high number here is evidence the tasks are")
    print("easy, not that the model is good. The attempts and the attribution")
    print("are the columns that still move.")


if __name__ == "__main__":
    raise SystemExit(main())
