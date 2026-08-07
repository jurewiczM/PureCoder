#!/bin/bash
# The ten-task corpus in scripts/bench/tasks.tsv, through one language.
#
# Same discipline as ocaml-batch.sh and for the same reason: the full
# transcript of every task is kept, because `ok=False attempts=4` reads
# identically whether the model wrote bad code or the harness refused good
# code, and those are opposite bugs.
#
#   scripts/bench/batch.sh python  q3-baseline
#   TASKS="sum_list roman" scripts/bench/batch.sh c++ smoke
#
# Environment:
#   BENCH         where transcripts go       (default ~/models/bench)
#   TASKS         space-separated subset      (default: all ten)
#   STORE         a RAG index to ground with  (default: ungrounded, see below)
#   RETRIES       attempts per task           (default 4)
#   TASK_TIMEOUT  seconds per task            (default 1800)
set -u

TARGET=${1:?usage: batch.sh <language> [tag]}
TAG=${2:-run}
BENCH=${BENCH:-$HOME/models/bench}
cd "$(dirname "$0")/../.."
CORPUS=scripts/bench/tasks.tsv

# Ungrounded by default, and deliberately so. ocaml-batch.sh attaches an index
# because the model has little OCaml in its weights; for a language it already
# knows, retrieval is measured to HURT -- `sum_list` passed on the first
# attempt ungrounded and failed four attempts with the OCaml docs attached,
# the retrieved tutorial text diluting the prompt. Set STORE to opt in.
if [ -n "${STORE:-}" ]; then
  ground=(--store "$STORE")
elif [ "$TARGET" = ocaml ]; then
  # The one language where that default is the wrong one, refused rather than
  # documented. OCaml is exactly the ignorant case retrieval exists for -- it
  # is the premise of ocaml-batch.sh -- so an ungrounded OCaml column would be
  # low for a reason no reader could distinguish from a regression.
  echo "batch.sh: ocaml ungrounded measures nothing comparable." >&2
  echo "  STORE=\$BENCH/ocaml-idx2 $0 ocaml $TAG   (or use ocaml-batch.sh)" >&2
  exit 2
else
  ground=(--no-docs)
fi

# The name the spec asks for, in the target language's convention.
#
# The contract is derived from prose by a model that is never told the
# language, so a spec naming `is_palindrome` yields that target in every
# language -- and the test gate matches it as a literal substring. A C# suite
# calling `IsPalindrome` therefore fails "tests never mention any of
# ['is_palindrome']" on every attempt, and the run dies at attempts=0 with
# nothing wrong with the model. Naming in convention removes that confound
# here rather than changing the gate underneath the thing being measured.
case "$TARGET" in
  javascript|js)          convention=camel  ;;
  c#|csharp|cs)           convention=pascal ;;
  *)                      convention=snake  ;;
esac

cased () {
  local raw=$1 out="" word
  [ "$convention" = snake ] && { printf '%s' "$raw"; return; }
  local IFS=_
  for word in $raw; do out+="${word^}"; done
  [ "$convention" = camel ] && out=${out,}
  printf '%s' "$out"
}

slug=$(printf '%s' "$TARGET" | tr 'A-Z' 'a-z' \
       | sed -e 's/c++/cpp/' -e 's/c#/csharp/' -e 's/[^a-z0-9]/_/g')
mkdir -p "$BENCH"

passed=0 total=0
while IFS=$'\t' read -r name spec <&3; do
  [ -z "$name" ] && continue
  case "$name" in \#*) continue ;; esac
  if [ -n "${TASKS:-}" ] && [[ " $TASKS " != *" $name "* ]]; then continue; fi

  fn=$(cased "$name")
  log=$BENCH/$TAG-$slug-$name.log
  prompt=${spec//\{fn\}/$fn}
  out=$(timeout "${TASK_TIMEOUT:-1800}" .venv/bin/python -m purecoder.cli \
          --retries "${RETRIES:-4}" "${ground[@]}" --lang "$TARGET" \
          code "$prompt" 2>&1)
  # The prompt heads the transcript: a log that does not say what was asked
  # cannot settle whether a failure was the spec or the answer.
  { echo "# lang=$TARGET task=$name"; echo "# spec: $prompt"; echo;
    printf '%s\n' "$out"; } > "$log"

  # Loose on spacing and on `attempts=None`. The verdict line is cli.py's
  # `ok={ok}  attempts={...}` -- a pattern tight enough to miss it would
  # report 0/10 against ten correct implementations, which is the failure
  # this whole directory exists to avoid.
  verdict=$(printf '%s' "$out" | grep -oE "ok=(True|False) +attempts=[A-Za-z0-9]+" | tail -1)
  case "$verdict" in ok=True*) passed=$((passed + 1)) ;; esac
  total=$((total + 1))
  echo "[${verdict:-no verdict}] $slug/$name -> $log"
done 3< "$CORPUS"

echo "$slug: $passed/$total"
