#!/usr/bin/env python3
"""Scaffold a whole small project: code, Makefile, .env, README.

    python examples/scaffold_project.py [outdir]

The output of one such run is checked in at examples/portcheck/.
"""

import sys

from purecoder import PureCoder, scaffold_project

SPEC = (
    "A CLI tool that reads a comma-separated PORTS environment variable, "
    "parses it into a sorted list of unique valid port numbers (1-65535), "
    "and prints them one per line. Exit with an error message on invalid input."
)


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else "portcheck-out"
    pc = PureCoder()
    result = scaffold_project(pc, "portcheck", SPEC, outdir=outdir)
    print(f"\n-> {result['outdir']}/  ok={result['ok']}")


if __name__ == "__main__":
    main()
