"""
purecoder/languages.py

What PureCoder can generate, and -- more to the point -- what it can prove.

The pipeline used to be Python end to end without saying so: the writer prompt
was hardcoded, the executor ran the candidate with `sys.executable`, and the
scaffolder wrote Makefiles full of pip. Asked for a C++ implementation it
produced `import heapq`, which is worse than a wrong answer -- it is an answer
of the wrong KIND, produced silently.

A language is now data. One `LanguageSpec` declares how to build it, how to run
it, how its tests assert, and what a project of it looks like; nothing else in
the codebase needs to know the language exists.

The rule that governs the whole registry: **if it cannot be executed, it is not
emitted.** A missing toolchain is refused with the binary named. A language
with no local runner at all is refused permanently, with the reason. There is
no "generated but unchecked" tier, because that is the claim this project
exists not to make.
"""

import shutil
import subprocess
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProjectSpec:
    """What a scaffolded project of this language looks like on disk."""

    entry: str                  # "main.cpp"
    install: str                # the `make install` recipe
    run: str                    # the `make run` recipe
    test: str                   # the `make test` recipe
    clean: str = "rm -rf build"


@dataclass(frozen=True)
class LanguageSpec:
    """Everything the pipeline needs to know about one language.

    `preamble` and `epilogue` wrap the candidate so that checks are counted
    without parsing the language: the harness supplies the assertion helper and
    the tester prompt tells the model to use it. That is why non-Python
    languages need no AST support -- see `counts_checks`.
    """

    name: str                            # the --lang value
    extension: str                       # ".cpp"
    probe: tuple = ()                    # ("g++", "--version")
    build: tuple = ()                    # ("g++", "{src}", "-o", "{bin}")
    run: tuple = ()                      # ("{bin}",)
    preamble: str = ""                   # emitted above the code
    epilogue: str = ""                   # emitted below the tests
    test_system: str = ""                # assertion idiom for the tester
    writer_system: str = ""              # extra instruction for the writer
    project: ProjectSpec | None = None
    unvalidatable: str = ""              # non-empty: refuse permanently, w/ reason
    counts_checks: bool = True           # can it prove a check actually ran?
    check_call: str = ""                 # textual marker the gate counts
    aliases: tuple = field(default=())

    # ---- assembly -------------------------------------------------------

    def assemble(self, code: str, tests: str) -> str:
        """One source file: harness, implementation, tests, then the tail that
        fails the run if no check executed."""
        parts = [p for p in (self.preamble, code.rstrip(), tests.rstrip(),
                             self.epilogue) if p.strip()]
        return "\n\n".join(parts) + "\n"

    # ---- availability ---------------------------------------------------

    def available(self):
        """(ok, reason). Probed, never assumed -- a registry entry is a claim
        about what we could run, not a promise that the machine has it."""
        if self.unvalidatable:
            return False, self.unvalidatable
        # A placeholder entry can pass its probe by accident: `ocaml` is
        # installed on this machine, but the entry has no run command, no test
        # idiom and no check helper. Having the binary is not the same as being
        # wired, and reporting otherwise trades a clear refusal for a confusing
        # runtime failure.
        if not self.run or not self.test_system:
            return False, (f"{self.name} is declared but not implemented yet "
                           f"-- no runner or test idiom is defined for it")
        if not self.probe:
            return True, ""
        binary = self.probe[0]
        if shutil.which(binary) is None:
            return False, (f"{binary!r} is not installed, so {self.name} code "
                           f"cannot be compiled or run here")
        try:
            subprocess.run(self.probe, capture_output=True, timeout=15,
                           check=True)
        except (subprocess.SubprocessError, OSError) as e:
            return False, f"{binary!r} is present but did not run: {e}"
        return True, ""


# ---- Python: the reference entry ----------------------------------------
#
# Python keeps bare `assert` and is counted by rewriting its AST, because
# lint_tests already parses Python and the idiom is what the model reaches for
# unprompted. Every other language uses the harness helper instead.

PYTHON = LanguageSpec(
    name="python",
    extension=".py",
    probe=(),                            # the interpreter running us
    run=("{python}", "{src}"),
    test_system=(
        "You write Python assert-based tests for a described function or "
        "class. Output ONLY test code: assert statements and setup. No prose, "
        "no fences. Assume the thing under test is already defined in the same "
        "file; call it directly. For an expected exception use try/except/else: "
        "call it inside 'try', then 'except ThatError: pass', then 'else: "
        "assert False'. Never put 'assert False' inside the try -- your own "
        "except would catch it and the test would pass on code that raised "
        "nothing."
    ),
    writer_system="You output only python code",
    project=ProjectSpec(
        entry="main.py",
        install="pip install -r requirements.txt",
        run="python main.py",
        test="pytest",
        clean="rm -rf __pycache__ *.pyc",
    ),
    aliases=("py",),
)


REGISTRY: dict[str, LanguageSpec] = {PYTHON.name: PYTHON}


def register(spec: LanguageSpec) -> LanguageSpec:
    REGISTRY[spec.name] = spec
    return spec


def names() -> list:
    """Every language the CLI will accept, available or not. An unavailable one
    still resolves so the refusal can explain itself."""
    return sorted(REGISTRY)


def get(name: str) -> LanguageSpec:
    """Resolve a --lang value. Raises KeyError listing the alternatives."""
    key = (name or "").strip().lower()
    if key in REGISTRY:
        return REGISTRY[key]
    for spec in REGISTRY.values():
        if key in spec.aliases:
            return spec
    raise KeyError(f"unknown language {name!r} -- known: {', '.join(names())}")


# ---- the harness helper -------------------------------------------------
#
# Every non-Python language supplies its own check helper and the tester prompt
# names it. That buys two things at once: the gate can count checks without
# parsing the language, and the run can PROVE a check executed rather than
# inferring it from exit code 0 -- the false green that this pipeline shipped
# for months in Python.
#
# The helper prints the failed expression to stderr before bailing. The fix
# loop is only as good as the error text it feeds back; a bare non-zero exit
# tells the model nothing.

register(LanguageSpec(
    name="c++",
    extension=".cpp",
    probe=("g++", "--version"),
    build=("g++", "-std=c++17", "-O0", "-w", "{src}", "-o", "{bin}"),
    run=("{bin}",),
    preamble=(
        "#include <cstdio>\n"
        "#include <cstdlib>\n"
        "static int pc_checks = 0;\n"
        "#define PC_CHECK(x) do { \\\n"
        "    if (!(x)) { std::fprintf(stderr, \"CHECK FAILED: %s (line %d)\\n\", \\\n"
        "                             #x, __LINE__); std::exit(1); } \\\n"
        "    pc_checks++; \\\n"
        "} while (0)\n"
    ),
    epilogue=(
        "int main() {\n"
        "    pc_tests();\n"
        "    if (pc_checks < 1) {\n"
        "        std::fprintf(stderr, \"no checks ran\\n\");\n"
        "        return 2;\n"
        "    }\n"
        "    return 0;\n"
        "}\n"
    ),
    test_system=(
        "You write C++ tests for a described function. Output ONLY the body of "
        "a function with the exact signature `void pc_tests()` -- including "
        "that signature and its braces, and nothing else. No main(), no "
        "includes, no prose, no fences. Assert with PC_CHECK(expr), which is "
        "already defined: e.g. PC_CHECK(add(1, 2) == 3);. Use PC_CHECK and "
        "nothing else -- never assert(), never cassert, never a test framework."
    ),
    writer_system="You output only C++ code",
    project=ProjectSpec(
        entry="main.cpp",
        install="@echo nothing to install",
        run="g++ -std=c++17 main.cpp -o main && ./main",
        test="g++ -std=c++17 main.cpp -o main && ./main",
        clean="rm -f main",
    ),
    check_call="PC_CHECK",
    aliases=("cpp", "cxx"),
))

register(LanguageSpec(
    name="javascript",
    extension=".js",
    probe=("node", "--version"),
    run=("node", "{src}"),
    preamble=(
        "let pcChecks = 0;\n"
        "function PC_CHECK(cond, label) {\n"
        "  if (!cond) {\n"
        "    console.error('CHECK FAILED: ' + (label || ''));\n"
        "    process.exit(1);\n"
        "  }\n"
        "  pcChecks++;\n"
        "}\n"
    ),
    epilogue=(
        "if (pcChecks < 1) {\n"
        "  console.error('no checks ran');\n"
        "  process.exit(2);\n"
        "}\n"
    ),
    test_system=(
        "You write JavaScript tests for a described function. Output ONLY test "
        "statements. No prose, no fences, no require/import, no test "
        "framework. Assert with PC_CHECK(expr, 'label'), which is already "
        "defined: e.g. PC_CHECK(add(1, 2) === 3, 'add'). For deep equality use "
        "PC_CHECK(JSON.stringify(a) === JSON.stringify(b), 'label'). Assume the "
        "thing under test is already defined in the same file."
    ),
    writer_system="You output only JavaScript code",
    project=ProjectSpec(
        entry="main.js",
        install="npm install",
        run="node main.js",
        test="node main.js",
        clean="rm -rf node_modules",
    ),
    check_call="PC_CHECK",
    aliases=("js", "node"),
))

register(LanguageSpec(
    name="rust",
    extension=".rs",
    probe=("rustc", "--version"),
    build=("rustc", "-A", "warnings", "-o", "{bin}", "{src}"),
    run=("{bin}",),
    preamble=(
        "use std::process::exit;\n"
        "static mut PC_CHECKS: i32 = 0;\n"
        "macro_rules! pc_check {\n"
        "    ($cond:expr) => {{\n"
        "        if !$cond {\n"
        "            eprintln!(\"CHECK FAILED: {} (line {})\", "
        "stringify!($cond), line!());\n"
        "            exit(1);\n"
        "        }\n"
        "        unsafe { PC_CHECKS += 1; }\n"
        "    }};\n"
        "}\n"
    ),
    epilogue=(
        "fn main() {\n"
        "    pc_tests();\n"
        "    unsafe {\n"
        "        if PC_CHECKS < 1 {\n"
        "            eprintln!(\"no checks ran\");\n"
        "            exit(2);\n"
        "        }\n"
        "    }\n"
        "}\n"
    ),
    test_system=(
        "You write Rust tests for a described function. Output ONLY a function "
        "with the exact signature `fn pc_tests()` -- including that signature "
        "and its braces, and nothing else. No fn main(), no #[test], no "
        "modules, no use statements, no prose, no fences. Assert with "
        "pc_check!(expr), which is already defined: e.g. pc_check!(add(1, 2) "
        "== 3);."
    ),
    writer_system="You output only Rust code",
    project=ProjectSpec(
        entry="main.rs",
        install="@echo nothing to install",
        run="rustc -o main main.rs && ./main",
        test="rustc -o main main.rs && ./main",
        clean="rm -f main",
    ),
    check_call="pc_check!",
    aliases=("rs",),
))

register(LanguageSpec(
    name="c#",
    extension=".cs",
    probe=("dotnet", "--version"),
    # .NET 10 file-based apps: `dotnet run app.cs` needs no .csproj, which is
    # what makes C# viable here at all. First run is slow (SDK warm-up), hence
    # the longer default timeout callers should allow.
    run=("dotnet", "run", "{src}"),
    preamble=(
        # No `static`: in a file-based app these are top-level statements, and
        # a static field there is CS0106.
        "int PcChecks = 0;\n"
        "void PC_CHECK(bool cond, string label) {\n"
        "    if (!cond) {\n"
        "        System.Console.Error.WriteLine(\"CHECK FAILED: \" + label);\n"
        "        System.Environment.Exit(1);\n"
        "    }\n"
        "    PcChecks++;\n"
        "}\n"
    ),
    epilogue=(
        "if (PcChecks < 1) {\n"
        "    System.Console.Error.WriteLine(\"no checks ran\");\n"
        "    System.Environment.Exit(2);\n"
        "}\n"
    ),
    test_system=(
        "You write C# tests for a described function. Output ONLY test "
        "statements at top level. No class, no Main, no using directives, no "
        "test framework, no prose, no fences. Assert with PC_CHECK(expr, "
        "\"label\"), which is already defined: e.g. PC_CHECK(Add(1, 2) == 3, "
        "\"add\");."
    ),
    writer_system=(
        "You output only C# code as top-level statements and local functions "
        "-- no class wrapper, no Main method, no using directives"
    ),
    project=ProjectSpec(
        entry="main.cs",
        install="@echo nothing to install",
        run="dotnet run main.cs",
        test="dotnet run main.cs",
        clean="rm -rf bin obj",
    ),
    check_call="PC_CHECK",
    aliases=("csharp", "cs"),
))


# ---- declared, not yet runnable -----------------------------------------
#
# These resolve so the refusal can explain itself, and start working the moment
# their toolchain appears -- no code change, just a probe that begins to pass.

for _name, _ext, _bin, _alias in (
    ("go", ".go", "go", ("golang",)),
    ("java", ".java", "javac", ()),
    ("swift", ".swift", "swiftc", ()),
    ("ocaml", ".ml", "ocaml", ("ml",)),
):
    register(LanguageSpec(
        name=_name, extension=_ext, probe=(_bin, "version" if _name == "go"
                                           else "--version"),
        aliases=_alias,
    ))


# Power Query M runs inside Excel and Power BI. There is no local interpreter
# at any effort level, so this is a permanent refusal rather than a missing
# toolchain -- and saying so is more useful than pretending it might work.
register(LanguageSpec(
    name="powerquery",
    extension=".pq",
    unvalidatable=(
        "Power Query M only runs inside Excel or Power BI -- there is no local "
        "interpreter to validate against, so purecoder will not generate it"
    ),
    counts_checks=False,
    aliases=("m", "power-query"),
))

# SQL is deliberately absent for now. Python's stdlib sqlite3 could run it, but
# SQL has no assertion form, so the check idiom every other language gets for
# free needs real design here rather than a `1/0` trick. Left undeclared rather
# than declared-and-broken.
