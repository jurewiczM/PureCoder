# The machine generated code is executed on.
#
# One image, every toolchain the registry can run, so that `available()` stops
# depending on what a particular laptop happens to have installed. A language
# that is runnable here is runnable everywhere this image runs, and a language
# that is skipped is skipped for a reason someone wrote down.
#
# .NET is the base rather than an apt package because C# here is a *file-based
# app* -- `dotnet run candidate.cs`, no .csproj -- which needs SDK 10 or newer.
# Debian ships nothing close, and that one requirement decides the base image.
FROM mcr.microsoft.com/dotnet/sdk:10.0

# Pinned deliberately. An image that drifts is a slower version of the problem
# this exists to fix: the point is that two runs of the same commit mean the
# same thing, and `apt-get install g++` on two dates does not.
RUN apt-get update && apt-get install -y --no-install-recommends \
        g++ \
        make \
        nodejs \
        ocaml-nox \
        python3 \
        python3-pip \
        python3-venv \
        rustc \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Generated code runs as nobody in particular, with no home worth reaching.
RUN useradd --create-home --shell /usr/sbin/nologin runner
USER runner
WORKDIR /work

# Telemetry and first-run setup write to $HOME and print a banner into the
# output the fix loop reads. Both are noise in a diagnostic.
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 \
    PYTHONDONTWRITEBYTECODE=1

# The proof, not a comment: every binary the registry probes must answer here.
# `docker build` fails if one is missing, so a broken image cannot be published
# and then discovered by a skipped test.
RUN set -eu; \
    for b in g++ make node ocamlc python3 rustc dotnet; do \
        command -v "$b" >/dev/null || { echo "MISSING: $b"; exit 1; }; \
        printf '%-8s %s\n' "$b" "$("$b" --version 2>&1 | head -1)"; \
    done
