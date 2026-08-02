"""
PureCoder -- a constrained, code-only agentic coder for a 6 GB laptop GPU.

Every layer assumes the model can be wrong and catches it with something
external: a GBNF grammar, a real tool, a test run. See docs/ARCHITECTURE.md.
"""

from .anchors import anchor_tests
from .client import PureCoder, strip_fences
from .contract import derive_contract, render_contract, validate_contract
from .execute import generate_validated_python, lint_tests, run_python
from .scaffold import scaffold_project
from .status import print_status
from .validate import generate_validated, validate_env, validate_makefile, validate_python

__version__ = "0.1.0"

__all__ = [
    "PureCoder",
    "strip_fences",
    "anchor_tests",
    "derive_contract",
    "render_contract",
    "validate_contract",
    "generate_validated",
    "validate_env",
    "validate_makefile",
    "validate_python",
    "generate_validated_python",
    "run_python",
    "lint_tests",
    "scaffold_project",
    "print_status",
]
