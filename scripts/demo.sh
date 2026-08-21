#!/bin/bash
# One command, one table. What PureCoder does, end to end, on this machine.
#
# Starts the model server if it is not already up, generates one function per
# language against the real toolchains, and prints what happened -- including
# which role a failed run stopped on, which is the thing a verdict alone
# cannot tell you.
#
#   scripts/demo.sh                  # every runnable language
#   scripts/demo.sh python ocaml     # just these
#
# Environment:
#   MODEL       gguf path            (default ~/models/Qwen3-Coder-30B-A3B-Instruct-Q3_K_M.gguf)
#   URL         llama-server         (default http://localhost:8080)
#   RETRIES     attempts per task    (default 4)
#   KEEP        1 to leave a server this script started running
set -u

cd "$(dirname "$0")/.."
URL=${URL:-http://localhost:8080}
MODEL=${MODEL:-$HOME/models/Qwen3-Coder-30B-A3B-Instruct-Q3_K_M.gguf}
RETRIES=${RETRIES:-4}
OUT=$(mktemp -d)
STARTED=""

PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

cleanup () {
  [ -n "$STARTED" ] && [ "${KEEP:-0}" != 1 ] && {
    echo; echo "stopping the llama-server this script started"
    kill "$STARTED" 2>/dev/null
  }
  rm -rf "$OUT"
}
trap cleanup EXIT

# ---- the model server ----------------------------------------------------
#
# Reuse whatever is already listening. Starting a second one would fail on the
# port and, worse, a half-started server answers /health long before it can
# answer a completion.
if curl -s -m 3 "$URL/health" 2>/dev/null | grep -q '"ok"'; then
  echo "model server: already up at $URL"
elif [ -f "$MODEL" ]; then
  echo "model server: starting (this takes ~30s to load)"
  ./llama.cpp/build/bin/llama-server -m "$MODEL" \
    -ngl 99 --cpu-moe -c 16384 -fa on -ctk q8_0 -ctv q8_0 \
    --host 127.0.0.1 --port 8080 > "$OUT/llama.log" 2>&1 &
  STARTED=$!
  for _ in $(seq 1 90); do
    sleep 2
    curl -s -m 2 "$URL/health" 2>/dev/null | grep -q '"ok"' && break
  done
  curl -s -m 2 "$URL/health" 2>/dev/null | grep -q '"ok"' || {
    echo "  it never came up. Last lines:" >&2
    tail -5 "$OUT/llama.log" >&2
    exit 2
  }
else
  # Refuse rather than run something that will fail for one reason ten times.
  echo "No model server at $URL, and no weights at:" >&2
  echo "  $MODEL" >&2
  echo >&2
  echo "Start a server yourself, or set MODEL. \`purecoder status\` prints the" >&2
  echo "exact command for both the 30B and the 7B." >&2
  exit 2
fi

# ---- which languages ------------------------------------------------------
#
# Probed, never assumed: the registry reports what this machine can actually
# build and run, and a demo that tries the rest would be showing failures that
# say nothing about the pipeline.
mapfile -t RUNNABLE < <("$PY" - <<'EOF'
import purecoder.languages as L
for name in L.names():
    if L.get(name).available()[0]:
        print(name)
EOF
)

if [ $# -gt 0 ]; then
  WANTED=("$@")
else
  WANTED=("${RUNNABLE[@]}")
fi

# One task, phrased once, named in each language's convention. The same spec
# everywhere is what makes the column comparable.
declare -A SPEC=(
  [python]="a function sum_list that returns the sum of a list of integers; the empty list returns 0"
  [c++]="a function sum_list that returns the sum of a vector<int>; the empty vector returns 0"
  [javascript]="a function sumList that returns the sum of an array of integers; the empty array returns 0"
  [rust]="a function sum_list that returns the sum of a Vec<i64>; the empty vector returns 0"
  [c#]="a function SumList that returns the sum of a List<int>; the empty list returns 0"
  [ocaml]="a function sum_list that takes an int list and returns the sum of its elements; the empty list returns 0"
  [sql]="a table totals(n INTEGER) and a query summing every n into a single row; an empty table sums to 0"
)

printf '\n%-12s %-8s %-9s %-10s %s\n' LANGUAGE VERDICT ATTEMPTS STOPPED-ON EVIDENCE
printf '%.0s-' {1..76}; echo

pass=0 fail=0 skip=0
for lang in "${WANTED[@]}"; do
  spec=${SPEC[$lang]:-}
  if [ -z "$spec" ]; then
    printf '%-12s %-8s %-9s %-10s %s\n' "$lang" skip - - "no demo task for this language"
    skip=$((skip + 1)); continue
  fi
  if ! printf '%s\n' "${RUNNABLE[@]}" | grep -qx "$lang"; then
    printf '%-12s %-8s %-9s %-10s %s\n' "$lang" skip - - "toolchain not available here"
    skip=$((skip + 1)); continue
  fi

  log=$OUT/$lang.log
  "$PY" -m purecoder --lang "$lang" --retries "$RETRIES" --no-docs \
      code "$spec" > "$log" 2>&1
  code=$?

  verdict=$(grep -oE 'ok=(True|False)' "$log" | tail -1)
  # The printed verdict and the exit code have to agree. They did not until
  # 2026-08-15 -- a refused run said ok=False and exited 0 -- and a demo that
  # reads only one of them would not notice if that came back.
  if { [ "$verdict" = "ok=True" ] && [ "$code" != 0 ]; } ||
     { [ "$verdict" = "ok=False" ] && [ "$code" = 0 ]; }; then
    echo "warning: $lang printed $verdict and exited $code -- these disagree" >&2
  fi
  attempts=$(grep -oE 'attempts=[A-Za-z0-9]+' "$log" | tail -1 | cut -d= -f2)
  stopped=$(grep -oE 'stopped on: [a-z]+' "$log" | tail -1 | awk '{print $3}')
  # How each role spent, e.g. "tester 2 / writer 4" -- the stop alone is
  # misleading, since the role that runs out is not always what was wrong.
  spend=$(awk '/^roles:/{f=1;next} f && /^  (ok|no) /{printf "%s %s/", $2, $3} /^  stopped/{f=0}' "$log" | sed 's:/$::')

  if [ "$verdict" = "ok=True" ]; then
    pass=$((pass + 1))
    printf '%-12s %-8s %-9s %-10s %s\n' "$lang" ok "$attempts" - "compiled, ran, checks executed"
  else
    fail=$((fail + 1))
    why=$(grep -E '^error: ' "$log" | tail -1 | cut -c8- | cut -c1-30)
    printf '%-12s %-8s %-9s %-10s %s\n' "$lang" refused "${attempts:-?}" \
      "${stopped:--}" "${spend:+$spend -- }${why:-see $log}"
    cp "$log" "./demo-$lang.log"
  fi
done

echo
echo "$pass ran and passed, $fail refused, $skip skipped."
echo
echo "Nothing above was accepted on the model's say-so: every 'ok' means the"
echo "code was compiled where the language needs it, executed in a sandbox, and"
echo "proved that a check actually ran. A refusal is the pipeline working --"
echo "there is no tier where unvalidated code is emitted."
[ "$fail" -gt 0 ] && echo "Transcripts for the refusals are in ./demo-<lang>.log"
exit 0
