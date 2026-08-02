"""
purecoder/scaffold.py

The orchestrator: turns a project description into a whole small project on
disk by composing the per-artifact validated loops. One focused agent call
per artifact (low tokens, tight scope), generated in dependency order so
later artifacts stay consistent with the code.

Order matters: code first, then the Makefile/.env are shown the code so
their targets and keys actually match it. README last, purely descriptive.

Only the code artifact is contract-grounded. The Makefile, .env and README
are config and prose -- their validators already cover what a contract would
add, and a fifth model call per artifact is not worth it on a tight card.
"""

import os

from .client import strip_fences
from .execute import generate_validated_python
from .validate import generate_validated


def _write(outdir: str, filename: str, content: str) -> str:
    path = os.path.join(outdir, filename)
    with open(path, "w") as f:
        f.write(content.rstrip() + "\n")
    return path


def scaffold_project(pc, name, description, outdir="build",
                     entry="main.py", max_retries=5, verbose=True,
                     use_contract=True):
    os.makedirs(outdir, exist_ok=True)
    report = {}

    def log(msg):
        if verbose:
            print(msg)

    # 1. main code -- execution-validated (the hard one)
    log(f"\n=== {name}: {entry} (execution-validated) ===")
    code_res = generate_validated_python(
        pc,
        f"{description}\n\nThis is the main module `{entry}`.",
        use_contract=use_contract,
        max_retries=max_retries, verbose=verbose,
    )
    code = code_res["text"]
    _write(outdir, entry, code)
    report[entry] = code_res["ok"]

    # 2. Makefile -- config-validated, shown the code for coherent targets
    log("\n=== Makefile (parse + semantic validated) ===")
    mk_res = generate_validated(
        pc, "makefile",
        f"Makefile for a Python project '{name}'. Entry point is {entry}. "
        f"Targets: install (pip install -r requirements.txt), "
        f"run (python {entry}), test (pytest), and a simple clean target that "
        f"removes __pycache__ and *.pyc. Keep each recipe to one or two commands.",
        max_retries=max_retries, verbose=verbose,
    )
    _write(outdir, "Makefile", mk_res["text"])
    report["Makefile"] = mk_res["ok"]

    # 3. .env -- only the vars the code actually reads
    log("\n=== .env (structural validated) ===")
    env_res = generate_validated(
        pc, "env",
        f"A .env file listing only the environment variables this code reads. "
        f"If it reads none, output a single commented example line. "
        f"Code:\n\n{code}",
        max_retries=max_retries, verbose=verbose,
    )
    _write(outdir, ".env", env_res["text"])
    report[".env"] = env_res["ok"]

    # 4. README -- prose, no validator
    log("\n=== README.md ===")
    readme = pc.complete(
        system="You output only Markdown. No surrounding code fences.",
        user=(f"Short README.md for project '{name}': {description}. Include a "
              f"title, one-line description, and install/run steps using the "
              f"Makefile (make install, make run). Keep it brief."),
        grammar=None, n_predict=512,
    )
    _write(outdir, "README.md", strip_fences(readme["text"]))
    report["README.md"] = True   # not validated (prose)

    ok = all(report.values())
    if verbose:
        print(f"\n=== scaffold {'OK' if ok else 'INCOMPLETE'}: {outdir}/ ===")
        for fn, passed in report.items():
            print(f"  {'ok  ' if passed else 'FAIL'} {fn}")
    return {"outdir": outdir, "report": report, "ok": ok}
