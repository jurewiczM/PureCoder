"""
purecoder/execute.py

Execution-based validation: generate code, generate tests (code-blind),
RUN them together in a sandboxed subprocess, and feed real tracebacks back
into the fix loop. This is where "valid" (parses) becomes "correct" (works).

Key design choice, borrowed from AgentCoder: the test designer does NOT see
the code. Tests come from the SPEC. If the tester saw the buggy code it
might "confirm" the bug. Independent tests are what make the check honest.
"""

import ast
import os
import re
import signal
import subprocess
import sys
import tempfile
from collections import Counter

from .client import strip_fences
from .contract import derive_contract, render_contract, validate_contract
from .languages import PYTHON

# a test file with fewer than this many assertions isn't a test suite,
# it's a smoke check -- regenerate rather than trust it.
MIN_ASSERTIONS = 3

# the same assertion line repeated more than this is a generation spiral.
MAX_REPEATED_ASSERT = 5

# this many IDENTICAL consecutive failures means the feedback is not landing;
# further attempts only spend model calls on a decided outcome.
NO_PROGRESS_LIMIT = 2

# matches `except SomeError as e: assert str(e) == "..."` style brittleness.
_MSG_ASSERT = re.compile(
    r"assert\s+(str\(|.*\.args|.*\.message)", re.IGNORECASE)


# the runtime counter injected into instrumented tests. Named to be unlikely
# to collide with anything the model writes.
_COUNTER = "__purecoder_checks__"


class _CountChecks(ast.NodeTransformer):
    """Record every check that actually EXECUTES.

    Two idioms count as a check: an `assert`, and entering an `except` handler
    (the try/except/else form verifies by being caught, and its success path
    runs no assert at all). Appending to a module-level list works from inside
    a function without a `global` declaration.
    """

    def _tick(self):
        return ast.parse(f"{_COUNTER}.append(1)").body[0]

    def visit_Assert(self, node):
        self.generic_visit(node)
        return [node, self._tick()]

    def visit_ExceptHandler(self, node):
        self.generic_visit(node)
        node.body = [self._tick(), *node.body]
        return node


def instrument_tests(tests: str, require: int = 1):
    """Wrap tests so the run proves checks ran, not merely that it exited 0.

    Returns instrumented source, or the source unchanged if it will not parse
    (the executor still runs it and the SyntaxError surfaces normally).

    This exists because exit code 0 is not evidence. A suite whose assertions
    all sit inside a `def test_x():` nobody calls exits 0 having verified
    nothing, and so does an empty suite -- both observed on real output.
    """
    try:
        tree = ast.parse(tests)
    except SyntaxError:
        return tests

    tree = _CountChecks().visit(tree)
    ast.fix_missing_locations(tree)
    body = ast.unparse(tree)

    return (
        f"{_COUNTER} = []\n"
        f"{body}\n"
        f"if len({_COUNTER}) < {require}:\n"
        f"    raise AssertionError(\n"
        f"        f'no checks ran: {{len({_COUNTER})}} executed, {require} required -- '\n"
        f"        'assertions defined but never reached (is a test function never called?)')\n"
    )


def _kill_group(proc):
    """Kill the candidate's whole process group, ignoring an already-dead one.

    Generated code may spawn threads, forks or servers. Killing only the direct
    child leaves those holding ports and file handles.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


# ---- the executor (model-independent, fully testable) -------------------

def _spawn(argv, cwd, timeout):
    """Run argv in its own process group and reap the group afterwards.

    start_new_session is what makes the timeout total rather than partial.
    Without it subprocess kills only the direct child: a generated server's
    worker threads or forks survive, hold their port, and the NEXT attempt
    fails with "Address already in use" -- which then gets fed back to the
    model as though its code were at fault. Observed live.

    Returns (returncode, stdout, stderr) or (None, "", "") on timeout.
    """
    proc = subprocess.Popen(
        argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        proc.communicate()
        return None, "", ""
    # Even on a clean exit the candidate may have left children running.
    _kill_group(proc)
    return proc.returncode, stdout, stderr


def run_candidate(spec, code: str, tests: str, timeout: int = 10,
                  require_checks: int = 0):
    """Build (if the language needs it) and run one candidate. -> (ok, error).

    Safe-ish sandbox: separate process group, temp dir as cwd, hard timeout so
    an infinite loop cannot hang the pipeline. Returns the compiler or runtime
    error on failure so the loop can feed it back to the model.

    require_checks > 0 demands evidence that a check actually executed. Python
    gets that by rewriting its test AST; every other language gets it from the
    harness helper its spec already injects, so there is nothing to do here.
    """
    if require_checks > 0 and spec.name == "python":
        tests = instrument_tests(tests, require=require_checks)

    script = spec.assemble(code, tests)
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, f"candidate{spec.extension}")
        binary = os.path.join(d, "candidate.bin")
        with open(src, "w") as f:
            f.write(script)

        subs = {"src": src, "bin": binary, "python": sys.executable}
        fill = lambda argv: [a.format(**subs) for a in argv]  # noqa: E731

        if spec.build:
            rc, _, berr = _spawn(fill(spec.build), d, timeout)
            if rc is None:
                return False, f"compilation timed out after {timeout}s"
            if rc != 0:
                # A compile error is an ordinary fix-loop failure, not an
                # abort: it is exactly the feedback the writer needs.
                return False, _trim(berr.strip() or f"compiler exited {rc}")

        rc, stdout, stderr = _spawn(fill(spec.run), d, timeout)

    if rc is None:
        return False, (f"execution timed out after {timeout}s "
                       f"(possible infinite loop, or a server that never "
                       f"returns)")
    if rc == 0:
        return True, ""
    # non-zero exit: an assert failed or an exception was raised.
    # Prefer stderr (the traceback); trim to the last few lines so the
    # feedback prompt stays small on a tight context budget.
    return False, _trim(stderr.strip() or stdout.strip() or f"exited {rc}")


# a compiler diagnostic line: "path:12:34: error: ..." / "warning:" / "note:"
_DIAGNOSTIC = re.compile(r"^\S*[^:]:\d+[:\d]*:\s*(error|warning|note)\b|"
                         r"^(error|warning)(\[[^\]]+\])?:", re.IGNORECASE)


def _trim(err: str) -> str:
    """Keep feedback small on a tight context budget.

    A Python traceback puts its signal in the LAST lines. A compiler puts it
    in the FIRST, then draws carets under the source -- so tailing a g++ error
    yields "|           ^" and nothing else. Observed live: the loop failed
    three identical times because that was the entire message the model got.
    """
    lines = err.splitlines()
    if len(lines) <= 12:
        return err
    diagnostics = [ln for ln in lines if _DIAGNOSTIC.search(ln.strip())]
    if diagnostics:
        return "\n".join(diagnostics[:12])
    return "\n".join(lines[-12:])


def run_python(code: str, tests: str, timeout: int = 10, require_checks: int = 0):
    """Python-specific wrapper kept so existing callers and tests are
    unchanged. New code should call run_candidate with an explicit spec."""
    return run_candidate(PYTHON, code, tests, timeout=timeout,
                         require_checks=require_checks)


# Languages the pipeline cannot produce or validate. The writer prompt is
# hardcoded to Python and the executor runs the file with sys.executable, so a
# request for any of these silently yields Python instead -- observed live,
# where "a C++ implementation of Dijkstra" produced `import heapq` and a
# Makefile full of pip.
FOREIGN_LANGUAGES = (
    "c++", "cpp", "c#", "java", "javascript", "typescript", "rust", "golang",
    "ruby", "php", "kotlin", "swift", "scala", "haskell", "power query",
    "powerquery", "vba", "matlab", "fortran", "cobol", "perl", "lua",
)


def unsupported_language(description: str):
    """The non-Python language a spec asks for, or None.

    Mentioning Python anywhere is taken as the user knowing what they want
    (e.g. "a Python function that parses Go source"), so only an unambiguous
    request trips this.
    """
    text = description.lower()
    if "python" in text:
        return None
    for name in FOREIGN_LANGUAGES:
        if re.search(rf"(?<![a-z0-9+#]){re.escape(name)}(?![a-z0-9+#])", text):
            return name
    return None


def missing_dependency(error: str):
    """The module name in a ModuleNotFoundError, or None.

    A missing third-party import is not a fault in the generated code and
    regenerating cannot fix it -- the sandbox simply has no such package.
    Retrying burns the whole budget on a verdict the executor can never reach.
    """
    m = re.search(r"ModuleNotFoundError: No module named '([^']+)'", error)
    return m.group(1) if m else None


def available_packages(names, python: str = sys.executable):
    """Which declared packages the sandbox can actually import. -> (ok, missing).

    Probed in the interpreter the executor will really use, because that is the
    only thing that decides whether generated code is validatable. Asking the
    *current* process would be a different question whenever a caller points the
    executor elsewhere.

    Checked before any model call. Producing three attempts' worth of correct
    code against a package the sandbox does not have is the most expensive way
    to arrive at "cannot validate".
    """
    names = tuple(names or ())
    if not names:
        return True, []
    missing = []
    for name in names:
        try:
            rc = subprocess.run([python, "-c", f"import {name}"],
                                capture_output=True, timeout=30).returncode
        except (subprocess.SubprocessError, OSError):
            rc = 1
        if rc != 0:
            missing.append(name)
    return not missing, missing


# SQLite's way of saying the implementation never built its own schema.
_NO_SUCH = re.compile(r"no such (table|view|column|index): (?:main\.)?"
                      r"([A-Za-z_][A-Za-z0-9_]*)")


def missing_relation(spec, error: str) -> str:
    """SQL's version of an attribution hint: what the code was meant to create.

    A database starts EMPTY. Every other language hands the writer an
    environment that already exists, so `no such table: orders` reads to a
    model like a fact about the machine rather than a job it was given -- and
    it read that way three attempts running in a live run, after which the
    loop's no-progress rule blamed the tests, which were fine.

    Saying so in `writer_system` was tried first and did not hold. That is the
    project's oldest lesson about layers: the `.env` rambling comment ignored a
    system prompt and yielded to a mechanical constraint. This is the
    mechanical one -- a hint on an already-failed run, in the same slot as the
    documentation and harness-collision hints, never a gate.
    """
    if spec.name != "sql":
        return ""
    match = _NO_SUCH.search(error)
    if not match:
        return ""
    kind, name = match.group(1), match.group(2)
    # A missing COLUMN is a different mistake from a missing table, and the
    # first version of this hint did not distinguish them: it answered "no such
    # column: id" with "CREATE TABLE id", and the model dutifully created a
    # table called `id`. Observed live, on the run that was verifying this very
    # hint.
    if kind == "column":
        return (f"\n\nNote: nothing declares a column named {name!r}. Add it "
                f"to the CREATE TABLE that your statements read, or select a "
                f"column that exists -- do not create a table named {name!r}.")
    return (f"\n\nNote: the database starts empty and nothing else creates "
            f"{name!r}. Your output must CREATE TABLE {name} (and INSERT the "
            f"rows it needs) before any {kind} that reads it.")


# Where a toolchain says the problem is. Two shapes cover everything this
# project runs: `path:12:34: error` (gcc, clang, rustc, node) and
# `File "x.ml", line 12, characters 3-4` (ocamlc, and Python's own traceback
# says `line 12` too).
_LINE_REF = re.compile(r"(?:^|[\s\"])(?:line |[^\s:]+:)(\d+)[:,\s]", re.MULTILINE)


def quoted_source(spec, code: str, tests: str, error: str, context: int = 2):
    """The source the diagnostic is pointing at. -> a block to append, or "".

    The fix loop showed the model `line 4, characters 39-40` and never showed it
    line 4. Observed live on an OCaml bubble sort: three attempts, three type
    errors, no convergence -- the writer was being asked to correct source it
    could not see, since its own previous output is not in the retry prompt.

    The numbers refer to the ASSEMBLED file -- harness, implementation, tests,
    tail -- so that is what gets quoted. A line inside the harness is worth
    seeing too: it tells the writer the error is about code it did not write.

    Deliberately a few lines rather than the whole file. Feeding full code
    forward is what this project found triggers degeneration on a small card;
    a diagnostic needs its own neighbourhood, not the module.
    """
    lines = spec.assemble(code, tests).split("\n")
    wanted = sorted({int(n) for n in _LINE_REF.findall(error)
                     if 0 < int(n) <= len(lines)})
    if not wanted:
        return ""

    shown, out = set(), []
    for number in wanted:
        lo = max(1, number - context)
        hi = min(len(lines), number + context)
        for i in range(lo, hi + 1):
            if i not in shown:
                shown.add(i)
                marker = ">>" if i == number else "  "
                out.append(f"{marker} {i:4d} | {lines[i - 1]}")
    return ("\n\nThose line numbers are the file the toolchain compiled -- "
            "your code with the test harness around it. Here is what it "
            "found there:\n" + "\n".join(out))


def lint_implementation(code: str):
    """Reject an implementation that has smuggled its tests inside itself.

    The fix loop shows the writer the tests its last attempt failed, and the
    model copies them into the module -- observed live, where main.py shipped
    with a `# Test cases` block that then ran twice, since the real tests are
    concatenated after the code.

    Only module-level asserts are rejected. An implementation has no business
    asserting at import time, and the check has no false positives: an assert
    inside a function body is untouched.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return True, ""          # the executor reports the SyntaxError itself

    for node in tree.body:
        if isinstance(node, ast.Assert):
            return False, ("the implementation asserts at module level -- "
                           "tests belong in the test file, not the module")
        # `try: f() except X: pass else: assert False` is the test idiom, and
        # at module level it is a test, not an implementation.
        if isinstance(node, ast.Try) and any(
                isinstance(n, ast.Assert) for n in ast.walk(node)):
            return False, ("the implementation contains a module-level "
                           "try/except test block -- output only the code")
    return True, ""


# An identifier applied to arguments and followed by a block: `int main() {`,
# `void pc_tests() {`, `fn main() {`. A call is not followed by one, which is
# the same distinction `bootstrap.dangling_calls` draws from the other side.
_DEFINITION = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\([^()]*\)\s*\{")

# Words that look like a definition under that regex and are not one.
_NOT_A_NAME = frozenset((
    "if", "else", "for", "while", "do", "switch", "catch", "using", "lock",
    "foreach", "return", "match", "when", "unsafe", "try", "with",
))


def _block_definitions(text: str) -> set:
    return {name for name in _DEFINITION.findall(text)
            if name not in _NOT_A_NAME}


def harness_collision(spec, code: str, tests: str = "") -> str:
    """What the implementation defines that the assembled file already has.

    The writer's demand (`bootstrap.writer_system_for`) tries to prevent this;
    this is what to say once it has happened anyway. The failure is reported at
    the wrong address by every toolchain involved: the harness's tail defines
    `main`, the writer defines one too, and the linker says "multiple definition
    of `main'" about a file the writer has never seen. Feeding that back
    unexplained sends the model to rewrite its own correct logic.

    Textual on purpose -- it runs for languages this project does not parse, and
    it only ever adds a sentence to a retry prompt that a failure already
    triggered. A miss costs nothing; a false positive costs one line of context,
    which is why keywords are excluded and Python (no harness at all) is exempt
    rather than approximated.
    """
    if not spec.preamble and not spec.epilogue:
        return ""

    # The tests count as part of the file, and they are where the likeliest
    # collision lives: a C++ tail calls `pc_tests()` without defining it, so a
    # writer reading the shape defines one, and the redefinition is against the
    # test snippet rather than against the harness. A forward declaration
    # (`int add(int,int);`) has no block and so cannot collide with the
    # implementation it declares.
    already = spec.preamble + "\n" + spec.epilogue + "\n" + tests
    clashes = sorted(_block_definitions(code) & _block_definitions(already))
    # The helper belongs to the harness and to the tests. An implementation
    # naming it at all is either asserting or redefining, and the writer prompt
    # already forbids the first.
    if spec.check_call and spec.check_call in code:
        clashes.append(spec.check_call)
    if not clashes:
        return ""

    names = ", ".join(repr(n) for n in clashes)
    return (f"\n\nNote: your implementation defines or uses {names}, which the "
            f"file it is pasted into already provides -- the harness supplies "
            f"the check helper and the entry point. Remove it and output only "
            f"the implementation.")


# ---- test-quality gate: "who tests the tester" --------------------------

def public_names(code: str):
    """Top-level function/class names defined by `code` -- what tests should call."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    return [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and not n.name.startswith("_")]


def _lint_tests_textual(tests, targets, min_assertions, spec):
    """The gate for languages we do not parse.

    Every non-Python harness injects its own check helper and the tester prompt
    names it, so counting calls to that helper is a faithful assertion count
    without needing a parser per language. The compiler catches malformed
    tests, which is what mode 1 does for Python -- so this checks the two
    things a compiler will not: that enough checks exist, and that they are
    aimed at the thing under test.
    """
    count = tests.count(spec.check_call)
    if count < min_assertions:
        return False, (f"only {count} check(s); need at least {min_assertions} "
                       f"-- assert with {spec.check_call}")

    # A malformation the language's own compiler would catch, but only after a
    # build -- and after the loop has spent an attempt on it. Declared per
    # language because the shape is per language: C++ writes
    # `PC_CHECK((a + b) == c, "x")` perfectly legitimately.
    for pattern, reason in spec.test_lint:
        if re.search(pattern, tests):
            return False, reason

    lines = [ln.strip() for ln in tests.splitlines()
             if spec.check_call in ln]
    if lines:
        repeats = Counter(lines).most_common(1)[0][1]
        if repeats > MAX_REPEATED_ASSERT:
            return False, (f"degenerate tests: identical check repeated "
                           f"{repeats} times -- likely a generation spiral")

    if targets and not any(t in tests for t in targets):
        return False, (f"tests never mention any of {sorted(targets)} -- "
                       f"they are not testing the target")
    return True, ""


def lint_tests(tests: str, targets=None, min_assertions=MIN_ASSERTIONS,
               spec=PYTHON):
    """Reject structurally bad tests BEFORE they get to judge code.

    Returns (ok, reason). Catches the five failure modes seen in development:
    doesn't parse, calls nothing under test, too few assertions (the floor is
    caller-tunable via min_assertions), degenerate repetition, and asserting
    on exact exception messages.

    It deliberately cannot catch a plausible-but-wrong expected value -- that
    is spec clarity's job, not this gate's. Said plainly rather than implied.
    """
    if not tests.strip():
        return False, "empty test output"

    if spec.name != "python":
        return _lint_tests_textual(tests, targets, min_assertions, spec)

    # mode 1: doesn't parse -- a test file that can't run proves nothing.
    try:
        tree = ast.parse(tests)
    except SyntaxError as e:
        return False, f"tests do not parse: {e.msg} at line {e.lineno}"

    # mode 2: too few assertions.
    asserts = [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
    if len(asserts) < min_assertions:
        return False, (f"only {len(asserts)} assertion(s); "
                       f"need at least {min_assertions}")

    # mode 3: degenerate repetition of one assertion line.
    lines = [ln.strip() for ln in tests.splitlines()
             if ln.strip().startswith("assert")]
    if lines:
        count = Counter(lines).most_common(1)[0][1]
        if count > MAX_REPEATED_ASSERT:
            return False, (f"degenerate tests: identical assertion repeated "
                           f"{count} times -- likely a generation spiral")

    # mode 4: never calls the thing under test.
    if targets:
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        called |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        if not called & set(targets):
            return False, (f"tests never call any of {sorted(targets)} -- "
                           f"they are not testing the target")

    # mode 5: asserting on exact exception text. Messages are not the contract;
    # a correct implementation with different wording would be failed.
    for i, raw in enumerate(tests.splitlines(), 1):
        if _MSG_ASSERT.search(raw.strip()):
            return False, (f"line {i}: asserts on exception message text; "
                           f"assert the exception TYPE instead")

    return True, ""


# ---- test designer (code-blind) -----------------------------------------

# Rules that hold whatever the language is. The per-language idiom (how to
# assert, what to output) lives on the LanguageSpec; these are about
# discipline, and every one of them was added because a live run broke it.
TEST_RULES = (
    "STRICT RULES: Only test behaviour the description explicitly states. Do "
    "not invent requirements. Do not test unspecified inputs. Never assert on "
    "exact exception or error message text -- assert the TYPE or the fact of "
    "the failure, never its wording. Respect every word of the spec (if it "
    "says 'sorted', the expected output must be sorted). Do not redefine the "
    "thing under test; it already exists in the same file."
)


def generate_tests(pc, description: str, n_predict: int = 512,
                   spec=PYTHON) -> str:
    """The tester prompt is the language's own idiom plus the rules that apply
    everywhere -- the spec supplies the former, TEST_RULES the latter.

    A spec with no test idiom cannot say how to assert, so there is nothing
    sensible to prompt with. `available()` refuses such a language before the
    CLI ever reaches here; this makes the same thing true for library callers.
    """
    if not spec.test_system:
        raise ValueError(f"{spec.name} declares no test idiom -- "
                         f"it cannot be validated, so tests are not generated")
    res = pc.complete(system=f"{spec.test_system} {TEST_RULES}", user=description,
                      grammar=None, n_predict=n_predict)
    return strip_fences(res["text"])


def design_tests(pc, description, targets=None, max_retries=3, verbose=True,
                 n_predict=512, min_assertions=MIN_ASSERTIONS, spec=PYTHON):
    """Generate tests and put them through the quality gate, regenerating with
    the reason fed back on rejection. Returns (tests, ok, reason).

    The designer stays code-blind: only the GATE ever sees names from the
    implementation, and only to check the tests call them at all.
    """
    task, tests, reason = description, "", ""
    for attempt in range(1, max_retries + 1):
        tests = generate_tests(pc, task, n_predict=n_predict, spec=spec)
        ok, reason = lint_tests(tests, targets=targets,
                                min_assertions=min_assertions, spec=spec)
        if ok:
            if verbose:
                print(f"[tests] accepted on attempt {attempt} "
                      f"({len(tests.splitlines())} lines)")
            return tests, True, ""
        if verbose:
            print(f"[tests] attempt {attempt} rejected: {reason} -> regenerating")
        task = (f"{description}\n\n"
                f"Your previous tests were rejected: {reason}\n"
                f"Output only corrected test code, nothing else.")
    return tests, False, reason


# ---- the execution fix loop ---------------------------------------------

def generate_validated_python(pc, description, tests=None, max_retries=3,
                              timeout=10, verbose=True, *, contract=None,
                              use_contract=False, spec=PYTHON,
                              error_hint=None, packages=(), **kw):
    """Generate code, run it against (code-blind) tests, retry on failure
    with the traceback fed back.

    With use_contract, the prose is first turned into a contract that both the
    writer and the test designer read. Returns {ok, text, tests, contract,
    attempts, error}.

    `error_hint(error) -> str` may add context to a failure the toolchain has
    already reported -- `ask` uses it to answer "did you mean" from the indexed
    docs. It is consulted only when a run has failed, and only its text reaches
    the retry prompt: the verdict stays the executor's, and the no-progress
    signal keeps reading the toolchain's own message.
    """
    # A language with no test idiom cannot be validated, so generating for it
    # would emit unverified code. The CLI refuses such a spec before reaching
    # here; a library caller (or scaffold_project, which has already made the
    # output directory) gets the loop's ordinary failure dict rather than an
    # exception out of the middle of a half-written project.
    ok, why = spec.available()
    if not ok:
        return {"ok": False, "text": "", "tests": "", "contract": None,
                "attempts": 0, "error": f"cannot generate {spec.name}: {why}"}

    # Declared packages, checked before a single model call. Two refusals, both
    # cheap: a language with no import story cannot honour the request at all,
    # and a package the sandbox cannot import makes every later verdict
    # unreachable however good the code is.
    packages = tuple(packages or ())
    if packages and spec.name != "python":
        return {"ok": False, "text": "", "tests": "", "contract": None,
                "attempts": 0,
                "error": f"cannot declare packages for {spec.name}: only the "
                         f"python path installs and imports them. Drop the "
                         f"declaration, or generate python."}
    have, missing = available_packages(packages)
    if not have:
        return {"ok": False, "text": "", "tests": "", "contract": None,
                "attempts": 0,
                "error": f"the sandbox cannot import {', '.join(missing)}, so "
                         f"nothing written against it could be validated. "
                         f"Install it first:\n  pip install {' '.join(missing)}"}

    if use_contract and contract is None:
        contract, cerr = derive_contract(pc, description,
                                         max_retries=max_retries,
                                         verbose=verbose)
        if contract is None and verbose:
            print(f"[contract] {cerr} -> continuing without one")
    elif contract is not None:
        # A caller-supplied contract skipped derivation and therefore skipped
        # validation. Degrade the same way a failed derivation does rather
        # than let a malformed dict raise out of render_contract.
        ok, cerr = validate_contract(contract)
        if not ok:
            if verbose:
                print(f"[contract] supplied contract rejected: {cerr} "
                      f"-> continuing without one")
            contract = None

    # Everything downstream reads the contract, never the implementation --
    # the test designer stays code-blind.
    grounded = description
    if contract is not None:
        grounded = f"{description}\n\n{render_contract(contract)}"
    # Folded into the shared task text rather than into the writer's system
    # prompt, because the TESTER needs the same permission: a writer allowed
    # numpy and a designer that is not produces assertions that cannot run.
    if packages:
        allowed = ", ".join(packages)
        grounded += (f"\n\nYou may use these third-party packages, which are "
                     f"installed: {allowed}. Nothing else outside the standard "
                     f"library.")

    if tests is None:
        designed, gate_ok, gate_reason = design_tests(
            pc, grounded, max_retries=max_retries, verbose=verbose, spec=spec)
        if not gate_ok:
            # The gate rejected every attempt. Using the last one anyway is how
            # a zero-assertion suite reaches the executor and reports success.
            if verbose:
                print(f"[tests] gate never satisfied: {gate_reason} -> giving up")
            return {"ok": False, "text": "", "tests": designed,
                    "contract": contract, "attempts": 0,
                    "error": f"test design failed the quality gate: {gate_reason}"}
    else:
        designed = tests

    task = grounded
    code, error = "", ""
    regated = False          # the target-name check runs once, after code exists
    redesigned = False       # the tests get one second chance, not a loop
    asked_stdlib = False     # the stdlib-only nudge is offered at most once
    last_error = None
    repeats = 0              # identical failures in a row -- the loop is stuck
    # Constraints discovered mid-loop must survive later rebuilds of `task`.
    # Without this the stdlib nudge is lost the moment any other failure
    # rewrites the prompt, and the next attempt re-imports the same package.
    constraints = ""

    for attempt in range(1, max_retries + 1):
        res = pc.code(task, language=spec.name,
                      writer_system=spec.writer_system, **kw)
        code = res["text"]

        if res["truncated"]:
            error = "output was cut off (hit n_predict)"
            task = (f"{grounded}{constraints}\n\n"
                    f"Previous output was cut off. Be complete but concise.")
            if verbose:
                print(f"[attempt {attempt}] truncated -> retrying")
            continue

        # Now that an implementation exists, the gate can also check the tests
        # actually call it -- the one check that needs a name from the code.
        if not regated:
            regated = True
            targets = public_names(code)
            if targets:
                gate_ok, gate_reason = lint_tests(designed, targets=targets,
                                                  spec=spec)
                # Tests the caller supplied are theirs. Redesigning them here
                # discarded the one thing they asked the code to be checked
                # against, and then reported success against tests they never
                # wrote -- a green result that is not evidence of the request.
                if not gate_ok and tests is not None:
                    if verbose:
                        print(f"[tests] supplied tests fail the gate: {gate_reason}")
                    return {"ok": False, "text": code, "tests": designed,
                            "contract": contract, "attempts": attempt,
                            "error": f"the tests you supplied fail the quality "
                                     f"gate: {gate_reason}"}
                if not gate_ok:
                    if verbose:
                        print(f"[tests] post-code gate: {gate_reason} -> redesigning")
                    designed, redo_ok, redo_reason = design_tests(
                        pc, grounded, targets=targets, max_retries=max_retries,
                        verbose=verbose, spec=spec)
                    if not redo_ok:
                        if verbose:
                            print(f"[tests] gate never satisfied: {redo_reason} "
                                  f"-> giving up")
                        return {"ok": False, "text": code,
                                "tests": designed,
                                "contract": contract, "attempts": attempt,
                                "error": f"test design failed the quality gate: "
                                         f"{redo_reason}"}

        # Reject an implementation carrying its own tests before running it --
        # they would execute alongside the real ones and pollute the artifact.
        # lint_implementation parses Python. For other languages the harness
        # owns main()/pc_tests(), so a stray test block is a compile error the
        # executor already reports clearly.
        impl_ok, impl_reason = (lint_implementation(code)
                                if spec.name == "python" else (True, ""))
        if not impl_ok:
            if verbose:
                print(f"[attempt {attempt}] {impl_reason} -> retrying")
            error = impl_reason
            task = (f"{grounded}{constraints}\n\n"
                    f"Your previous output was rejected: {impl_reason}\n"
                    f"Output only the implementation, nothing else.")
            continue

        full = designed
        ok, error = run_candidate(spec, code, full, timeout=timeout,
                                  require_checks=1)

        dep = missing_dependency(error)
        if dep:
            # Regenerating the same way cannot install a package, so retrying
            # blind burns the budget. But the sandbox CAN validate stdlib code,
            # and there is usually a stdlib route (http.server for flask, a
            # hand-written SVG for matplotlib). Ask once, then stop.
            if not asked_stdlib:
                asked_stdlib = True
                if verbose:
                    print(f"[attempt {attempt}] missing dependency {dep!r} -- "
                          f"retrying with a standard-library-only constraint")
                # A declared package survives this. Without the exception a
                # numpy run that hit a DIFFERENT missing import was told to
                # drop numpy too -- the loop taking back a permission the
                # caller had checked and granted.
                allowed = (f" You may still use {', '.join(packages)}, which "
                           f"is installed." if packages else "")
                constraints = (
                    f"\n\nHARD CONSTRAINT: use ONLY the {spec.name} standard "
                    f"library.{allowed} {dep!r} is not installed and cannot be "
                    f"validated here. No other third-party imports at all, in "
                    f"any later attempt.")
                task = f"{grounded}{constraints}"
                continue
            if verbose:
                print(f"[attempt {attempt}] still missing {dep!r} -- "
                      f"cannot validate, stopping")
            return {"ok": False, "text": code, "tests": full,
                    "contract": contract, "attempts": attempt,
                    "error": f"cannot validate: the sandbox has no module "
                             f"{dep!r}, and the stdlib-only retry still imported "
                             f"it. Install it, or ask for stdlib-only code."}

        if ok:
            if verbose:
                print(f"[attempt {attempt}] all tests passed")
            return {"ok": True, "text": code, "tests": full,
                    "contract": contract, "attempts": attempt, "error": ""}

        # An identical failure means the feedback is not moving the model.
        # Observed live: 5x "API endpoint is unreachable", 4x the same
        # AttributeError, 3x the same KeyError -- twelve wasted calls across
        # three runs, all after the outcome was already decided.
        first = error.splitlines()[-1] if error else "unknown"
        repeats = repeats + 1 if first == last_error else 0
        last_error = first
        if repeats >= NO_PROGRESS_LIMIT:
            # The tests do not change between attempts. An identical failure
            # across DIFFERENT generated code is therefore evidence that the
            # tests are at fault, not the implementation -- and for a compiled
            # language they share a build, so a tester that emits invalid
            # source fails the writer's work. Observed live on OCaml: the
            # tester wrote `bubble_sort([||]) = ||`, the compiler said "Syntax
            # error", and that went to the writer three times.
            #
            # Blaming by line number would need a diagnostic format per
            # language. This needs none: it is the same no-progress signal the
            # loop already computes, read for what it actually implies.
            if tests is None and not redesigned:
                redesigned = True
                repeats, last_error = 0, None
                if verbose:
                    print(f"[attempt {attempt}] the same failure on different "
                          f"code -- suspecting the tests, redesigning them")
                designed, redo_ok, redo_reason = design_tests(
                    pc, grounded, targets=public_names(code),
                    max_retries=max_retries, verbose=verbose, spec=spec)
                if not redo_ok:
                    return {"ok": False, "text": code, "tests": designed,
                            "contract": contract, "attempts": attempt,
                            "error": f"test design failed the quality gate: "
                                     f"{redo_reason}"}
                task = f"{grounded}{constraints}"
                continue

            if verbose:
                print(f"[attempt {attempt}] same failure {repeats + 1}x in a "
                      f"row -- the fix loop is not converging, stopping")
            return {"ok": False, "text": code, "tests": full,
                    "contract": contract,
                    "attempts": attempt,
                    "error": f"stopped after {repeats + 1} identical "
                             f"failures: {error}"}

        if verbose:
            print(f"[attempt {attempt}] tests failed: {first} -> retrying")
        # Appended to the prompt, never to `error`: the no-progress check above
        # reads the toolchain's last line, and enriching it would make an
        # unchanging failure look like a moving one.
        hint = error_hint(error) if error_hint else ""
        if hint and verbose:
            print(f"[docs] {hint.splitlines()[0]}")
        # Added to the same slot and for the same reason: a hint offered only
        # after the toolchain has already refused, never a gate of its own.
        hint += harness_collision(spec, code, full)
        hint += missing_relation(spec, error)
        # A compiler that says "line 4" is talking about a file the writer has
        # never seen: its own previous output is not in this prompt, and the
        # harness around it never was.
        hint += quoted_source(spec, code, full, error)
        # The tests are shown so the model knows what it must satisfy, but
        # left unqualified it copies them into the module -- caught twice in
        # one live run by lint_implementation. Say plainly that they are run
        # separately.
        # Showing the writer its own last attempt is what makes this a FIX loop
        # rather than a regeneration loop. Without it the model starts from the
        # spec every time and rediscovers its own bug -- observed live on an
        # OCaml bubble sort, four attempts, four variants of one mistake, while
        # the failing check named the case each time.
        #
        # Bounded, because the opposite failure is this project's finding 4:
        # feeding full code forward for coherence triggered degeneration on a
        # small card. A large implementation is described by its error alone.
        previous = (f"\n\nYour previous implementation, which failed:\n{code}"
                    if len(code) <= 2000 else "")
        task = (f"{grounded}{constraints}{previous}\n\n"
                f"It failed these tests, which are run separately and must NOT "
                f"appear in your output:\n{full}\n\n"
                f"With this error:\n{error}\n"
                f"{hint}\n\n"
                f"Output only the corrected implementation, nothing else.")

    return {"ok": False, "text": code, "tests": designed,
            "contract": contract,
            "attempts": max_retries, "error": error}
