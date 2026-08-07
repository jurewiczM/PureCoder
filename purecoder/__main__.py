"""Entry point for `python -m purecoder`.

The exit code has to be propagated: the console script gets `sys.exit(main())`
from setuptools for free, and without the same here a refusal printed its
reason and still exited 0 -- so the two entry points the README calls
identical disagreed on the one thing a script would check.
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main() or 0)
