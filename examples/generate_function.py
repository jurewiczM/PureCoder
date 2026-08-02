#!/usr/bin/env python3
"""Execution-validated code generation: the core loop, on one function.

    python examples/generate_function.py

Equivalent to `purecoder code "<spec>"`, kept as a script because it shows
the returned dict (tests used, attempt count) rather than just the text.
"""

from purecoder import PureCoder, generate_validated_python

SPEC = (
    "A function parse_ports(s) that takes a comma-separated string of port "
    "numbers and returns a sorted list of unique ints, raising ValueError on "
    "any non-numeric or out-of-range (1-65535) entry."
)


def main():
    pc = PureCoder()
    result = generate_validated_python(pc, SPEC, max_retries=5)
    print(result["text"])
    print("passed:", result["ok"], "in", result["attempts"], "attempts")


if __name__ == "__main__":
    main()
