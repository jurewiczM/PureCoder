# Containerised execution: one image, seven toolchains — Design

_2026-08-09_

**Status: designed, unbuilt.** Nothing below has been run. The seam it depends
on exists and has been read; the image has not been built and the probes have
not been executed anywhere.

## Problem

Two claims this project makes are weaker than they read.

**"If it cannot be executed, it is not emitted."** True, and the executor's
isolation is a temp directory and a process group. STATUS.md already says so —
*"that is a closed door, not a sandbox"* — but the disclaimer is doing work a
boundary should be doing. Model-authored code currently runs as the user, with
the user's filesystem and the user's network. Nothing observed has been
malicious; the recorded incidents are a server that never returns, orphaned
child processes, and a port held in `TIME_WAIT`. Accidents, all handled. What
is missing is the difference between *stopping accidents* and *denying access*.

**"N tests, no GPU, no server needed."** Also true, and CLAUDE.md immediately
qualifies it: *"toolchain-dependent tests self-skip when a compiler is absent,
so a green run on a machine without `ocamlc` proves less than it looks."* A
language's `available()` asks `shutil.which` about whatever happens to be
installed, so the same commit means different things on two machines, and a
benchmark row does not record which. On 2026-08-09 the ten-task benchmark ran
six languages here and would run two in CI, and nothing in the output says so.

Both are the same shape: **the environment is an unrecorded input.**

## Scope

One image, all toolchains, used for execution only. Generation stays on the
host, because it needs the GPU and a 13.7 GB model, and because `_spawn` is
already the line where the split falls.

Not in scope: llama-server in the image; a devcontainer for developing
PureCoder itself (a separate, smaller thing that could reuse the same
Dockerfile); running CI inside the image (a follow-up, see *Deliberately
later*).

## The seam

`purecoder/execute.py:112`

```python
def _spawn(argv, cwd, timeout):
```

This is the **only** place generated code ever runs. Build and run both go
through it, in every language. Containerising the executor is therefore a
change to how one argv is constructed, not a change to the pipeline.

That is the whole reason this is affordable, and it is worth stating plainly
because the obvious alternative — teaching each `LanguageSpec` to know about
containers — would put the same knowledge in eleven places and guarantee
drift.

## Design

### 1. A backend, chosen explicitly and recorded

```
--sandbox docker | host        (default: docker if the image is present, else host)
```

Two rules, and the second matters more than the first:

**Requested and unavailable is a refusal, not a fallback.** `--sandbox docker`
with no image, or no docker, fails with the reason and the build command. A
silent fall back to the host would make "reproducible" mean "reproducible when
it happened to work", which is precisely the class of claim this project keeps
catching itself making.

**Every result records which backend produced it.** `run_candidate` returns it,
`generate_validated_python` passes it through, the benchmark writes it in the
TSV row, and `purecoder status` prints it. A score that does not say where it
ran cannot be compared with another score, and this session has already twice
produced numbers that meant something other than they appeared to.

The default is deliberately *not* "always docker": a machine without it must
keep working, because refusing to run at all on a laptop with no Docker is a
worse failure than the one being fixed.

### 2. `available()` must answer about the machine that will run the code

Today:

```python
if shutil.which(binary) is None:
    return False, f"{binary!r} is not installed, so {self.name} code cannot be..."
```

Under a container backend that is answering about the wrong machine, and this
project **refuses** languages rather than guessing — so a wrong answer here is
a wrong refusal, which is worse than a wrong guess elsewhere.

`available()` gains the backend as a parameter. The host backend keeps
`shutil.which`. The docker backend probes inside the image:

```
docker run --rm <image> sh -c 'command -v g++'
```

Probing seven languages is seven container starts, so results are cached for
the process lifetime. The cache is keyed by image reference, so changing the
image invalidates it rather than silently reusing a previous answer.

### 3. Two host paths that mean nothing in a container

**`{python}` substitutes `sys.executable`.** Inside the image that path does
not exist. The substitution becomes the backend's interpreter — the image's
`python3` under docker, `sys.executable` under host.

**`code --with numpy` probes the sandbox interpreter before any model call.**
That check becomes *more* honest under a container, not less: today it probes
the venv PureCoder happens to be running in and calls it "the sandbox", which
is true only because the executor uses `sys.executable`. Under docker it probes
the interpreter that will actually run the code.

This is the part of the design most likely to produce a surprise, because it is
the only place where a host and container answer can differ *silently* today.

### 4. Isolation settings, and why each one

| flag | reason |
|---|---|
| `--network=none` | Nothing generated has ever needed the network. A missing import is meant to be refused with a `pip install` line, not fetched. This also makes the "no per-run venv" decision enforceable rather than merely intended. |
| `--read-only` + tmpfs work dir | The candidate writes its source and its binary and nothing else. |
| `--cap-drop=ALL`, non-root | Nothing here needs a capability. |
| `--pids-limit`, `--memory` | A fork bomb or a runaway allocation is a plausible *accident*, and the timeout does not cover either. |
| keep the process-group reaping | A container exit does not help the host-side timeout path, and that code exists because of an observed leak. Removing it because "the container handles it" would be reasoning from the architecture rather than from the incident. |

The mounts are: the work directory, writable; nothing else. No home, no repo,
no socket.

### 5. The image

One image, all seven toolchains — `python3`, `g++`, `node`, `rustc`, `dotnet`,
`ocamlc`, and the stdlib `sqlite3` module. Built from a pinned Dockerfile in
the repo.

One image rather than one per language, for the same reason a language is one
`LanguageSpec`: the project's unit of work is "a language is data", and seven
images would put the toolchain set somewhere other than the registry that
describes it. The cost is a large build dominated by dotnet and rust; the
benefit is that "which languages are runnable" has a single answer.

Versions are pinned, because an image that drifts is a slower version of the
problem this is fixing.

## Making a skip say something

The current failure mode is silence: a toolchain is absent, tests self-skip,
the suite is green, and the green means less than it looks. Installing every
toolchain in CI is one answer and it is the expensive one — the point of the
image is that CI should be able to *skip a language it does not need* and still
be honest about having done so.

So the skip becomes a checked claim rather than an absence:

**A test asserts that every registered language is accounted for.** For each
entry in the registry, exactly one of these must hold, and the test names the
language and the reason when none does:

1. runnable on this backend — its tests ran;
2. declared-not-implemented (`go`, `java`, `swift`) — no runner or test idiom
   exists, which the registry already states;
3. permanently unvalidatable (`powerquery`) — stated in the entry;
4. **runnable in principle, toolchain absent here** — skipped, and the skip is
   *reported* with the language and the missing binary.

Case 4 is the one that exists today and says nothing. The test turns it into a
line of output: `skipped c#: 'dotnet' not installed`. A CI run that skips four
languages says which four and why, and a reviewer can tell "we chose not to
install rustc" from "rustc silently vanished".

The binary name comes from the derivation already available — `probe`/`build`/`run`
argv, placeholders filtered — so it cannot drift from what actually runs.

**What this deliberately does not do:** fail CI when a language is skipped.
That would make the image mandatory, which contradicts the backend default
above. It makes the skip *visible and attributable*, which is the property that
was missing.

## Testing

Hermetic, because the suite must keep needing no GPU, no server, and now no
Docker either:

- backend selection: requested-and-missing refuses with the build command;
  absent-and-not-requested falls back to host; the chosen backend appears in
  the result.
- `available()` under a fake docker probe: a language present in the image and
  absent on the host resolves differently under the two backends, which is the
  whole point.
- `{python}` and `--with` resolve to the backend's interpreter.
- the argv the docker backend builds carries every isolation flag above — a
  test on the constructed command, so a dropped `--network=none` is caught
  without running anything.
- the accounting test above, driven by a fake registry, covering all four cases
  including a language that matches none of them.

Not hermetic, and honest about it: that the image *actually* contains seven
working toolchains is proven by building it and running the five bootstrap
probes per language inside it. That is a real run, it belongs in the write-up
rather than the suite, and until it happens this design's central claim is
unproven.

## What this does not fix

**A container is not a VM.** Kernel-sharing is real; this raises the cost of an
escape, it does not remove the possibility. The honest claim afterwards is
"generated code cannot reach your files or the network", not "generated code is
safe".

**Reproducible is not deterministic.** Pinning toolchains removes one variable.
The model is still sampled, and the 2026-08-09 run showed the same task passing
and failing across runs on identical code paths.

**It does not make the benchmark discriminating.** That instrument saturates
for a different reason and this changes nothing about it.

## Deliberately later

Running the test suite inside the image in CI. That is what would finally
retire the "self-skip" caveat entirely, and it changes every workflow run — a
bigger blast radius than the feature itself, and better done once the image has
been exercised locally.

A devcontainer for developing PureCoder. Same Dockerfile plus the venv and
tooling; useful, and a different problem from executing generated code.

## Order of work

1. The image and its Dockerfile, built and probed by hand. Nothing else is
   meaningful until seven toolchains demonstrably work inside it.
2. The backend seam at `_spawn`, with the host backend as a no-op refactor
   proven by the existing suite staying green.
3. The docker backend, its isolation argv, and the tests above.
4. `available()`, `{python}` and `--with` per backend.
5. Recording the backend in results, the benchmark TSV and `status`.
6. The accounting test, and the skip report.
7. A live run: the ten-task benchmark under `--sandbox docker`, compared
   against the host numbers from 2026-08-09. Any difference is a finding about
   the image, and the comparison is the point.
