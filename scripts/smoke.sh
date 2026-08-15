#!/bin/bash
# Six live checks against a running llama-server, in about a minute.
#
# What this is for: `pytest` proves the harness behaves against a scripted fake
# model, and every live session so far has found defects it could not see. This
# script is the cheapest version of that -- one pass down the artifact types,
# each one asserted, transcripts kept. It is not a benchmark and says nothing
# about capability; scripts/bench/batch.sh is where scores come from.
#
#   scripts/smoke.sh                 # all checks
#   SMOKE_DIR=/tmp/smoke scripts/smoke.sh
#
# Environment:
#   URL        llama-server base URL   (default http://localhost:8080)
#   SMOKE_DIR  where transcripts go    (default ~/models/bench/smoke)
#   TIMEOUT    seconds per check       (default 300)
set -u

URL=${URL:-http://localhost:8080}
SMOKE_DIR=${SMOKE_DIR:-$HOME/models/bench/smoke}
TIMEOUT=${TIMEOUT:-300}
cd "$(dirname "$0")/.."
mkdir -p "$SMOKE_DIR"

PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

if ! curl -s -m 3 "$URL/health" | grep -q '"ok"'; then
  echo "smoke.sh: no server at $URL. Start one with:" >&2
  "$PY" -m purecoder --url "$URL" status 2>/dev/null | sed -n '/llama-server/,$p' >&2
  exit 2
fi

passed=0 failed=0 skipped=0

# Two assertions per check, deliberately: the printed verdict AND the exit
# code. They were out of step until 2026-08-15 -- `ok=False` exited 0 -- so a
# script trusting either one alone would have reported that run as clean.
check () {
  local name=$1 want=$2; shift 2
  local log=$SMOKE_DIR/$name.log out code
  out=$(timeout "$TIMEOUT" "$PY" -m purecoder --url "$URL" "$@" 2>&1); code=$?
  { echo "# smoke: $name"; echo "# argv: $*"; echo "# exit: $code"; echo;
    printf '%s\n' "$out"; } > "$log"

  local verdict
  verdict=$(printf '%s' "$out" | grep -oE "ok=(True|False) +attempts=[A-Za-z0-9]+" | tail -1)
  if [ "$verdict" = "" ]; then
    echo "[FAIL] $name -- no verdict line ($log)"; failed=$((failed + 1)); return
  fi
  case "$verdict" in
    ok=True*)  [ "$want" = ok ] && [ "$code" = 0 ] && {
      echo "[ok]   $name  $verdict"; passed=$((passed + 1)); return; } ;;
    ok=False*) [ "$want" = fail ] && [ "$code" != 0 ] && {
      echo "[ok]   $name  $verdict (expected)"; passed=$((passed + 1)); return; } ;;
  esac
  echo "[FAIL] $name -- $verdict, exit $code, wanted $want ($log)"
  failed=$((failed + 1))
}

# 1-2. The two config artifacts. Both are grammar-constrained, and both have
# failed live by rambling past n_predict rather than by writing anything wrong.
check env  ok env  "a web app with a database url, a port and a debug flag"
check make ok make "Makefile for a python project: install, test, lint, clean"

# 3. Python end to end: contract off, tests designed, code run, checks counted.
check code-python ok code \
  "a function word_count(text) returning the number of whitespace-separated words in text"

# 4. A compiled language, which exercises a path Python never touches -- a
# build step whose failure is ordinary fix-loop feedback.
if command -v ocamlc >/dev/null; then
  check code-ocaml ok --lang ocaml code \
    "a function sum_list : int list -> int returning the sum of a list of integers"
else
  echo "[skip] code-ocaml -- ocamlc not installed"; skipped=$((skipped + 1))
fi

# 5. The contract seam, asserted on derivation and NOT on the verdict.
# `contract.gbnf` once failed to parse in llama.cpp at all and every run fell
# back silently, which is the regression worth catching. The run itself may
# legitimately fail: the model writes `parse_ports('80,443') -> [443, 80]` for
# a spec that says sorted, and the suite then fails correct code. That is a
# documented boundary, not a smoke failure.
log=$SMOKE_DIR/contract.log
timeout "$TIMEOUT" "$PY" -m purecoder --url "$URL" --contract code \
  "a function parse_ports(s) returning a sorted list of unique valid ports (1-65535) from a comma-separated string, raising ValueError on bad input" \
  > "$log" 2>&1
if grep -q "\[contract\] derived on attempt" "$log"; then
  echo "[ok]   contract  derived (verdict not asserted)"; passed=$((passed + 1))
else
  echo "[FAIL] contract -- no contract derived, the grammar fell back ($log)"
  failed=$((failed + 1))
fi

# 6. The refusal path: an unwired language must refuse before any model call,
# and must not report that refusal as success.
out=$("$PY" -m purecoder --url "$URL" --lang go code "anything" 2>&1); code=$?
if [ "$code" != 0 ] && printf '%s' "$out" | grep -q "not implemented"; then
  echo "[ok]   refusal  go refused, exit $code"; passed=$((passed + 1))
else
  echo "[FAIL] refusal -- go exited $code: $(printf '%s' "$out" | head -1)"
  failed=$((failed + 1))
fi

echo
echo "smoke: $passed passed, $failed failed, $skipped skipped -> $SMOKE_DIR"
[ "$failed" = 0 ]
