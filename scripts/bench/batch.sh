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

# Refuse a language this machine cannot run, before spending ten tasks
# discovering it. Every task would land in `refused` with the same reason, and
# a column of ten identical refusals is a worse way to say "no toolchain" than
# saying it once. The registry already knows -- `available()` returns the
# reason -- so this asks rather than maintaining a second list that can drift.
if ! reason=$(.venv/bin/python -c '
import sys
from purecoder import languages
try:
    spec = languages.get(sys.argv[1])
except Exception as e:
    print(e); sys.exit(1)
ok, why = spec.available()
if not ok:
    print(why); sys.exit(1)
' "$TARGET" 2>&1); then
  echo "batch.sh: cannot benchmark $TARGET -- $reason" >&2
  .venv/bin/python -c '
from purecoder import languages
runnable = [n for n in languages.names() if languages.get(n).available()[0]]
print("  runnable here:", ", ".join(runnable), file=__import__("sys").stderr)' 2>&1 >&2
  exit 2
fi

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
declare -A counts=()
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

  # Who said no, not just that someone did. `ok=False attempts=4` reads the
  # same whether the model wrote bad code or a gate refused good code, and a
  # table of forty runs needs to point at the rows worth opening.
  #
  # A classifier is a convenience over the transcript, never a gate on it: if
  # it cannot run, fall back to the raw verdict line rather than lose the run.
  if line=$(printf '%s' "$out" | .venv/bin/python -c '
import sys
from purecoder.benchlog import classify
v = classify(sys.stdin.read())
print(v.verdict, "None" if v.attempts is None else v.attempts, v.reason)
' 2>/dev/null); then
    read -r v attempts reason <<< "$line"
  else
    # Branching on the classifier's exit status, not on `read` hitting EOF --
    # the fallback used to fire as a side effect of empty input, which is the
    # right behaviour arrived at by accident.
    v=$(printf '%s' "$out" | grep -oE "ok=(True|False) +attempts=[A-Za-z0-9]+" | tail -1)
    v=${v:-no-verdict}; attempts=?; reason=""
  fi

  case "$v" in ok) passed=$((passed + 1)) ;; esac
  total=$((total + 1))
  counts[$v]=$(( ${counts[$v]:-0} + 1 ))
  printf '[%-13s attempts=%-4s] %s/%s%s\n' "$v" "$attempts" "$slug" "$name" \
         "${reason:+   $reason}"
  printf '%s\t%s\t%s\t%s\t%s\n' "$slug" "$name" "$v" "$attempts" "$reason" \
         >> "$BENCH/$TAG-results.tsv"
done 3< "$CORPUS"

# The summary a cross-language set exists for. `unknown` is listed even at
# zero: a marker this classifier does not recognise must be visible, not
# quietly folded into the model's score.
summary=""
for k in writer suspect-tests gate contract stuck refused server timeout unknown; do
  summary+=$(printf ' %s %s' "$k" "${counts[$k]:-0}")
done
echo "$slug: $passed/$total  --$summary"
echo "rows appended to $BENCH/$TAG-results.tsv"
