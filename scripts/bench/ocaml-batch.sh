#!/bin/bash
# Same five tasks, same flags -- but keep the full transcript per task, so a
# failure is attributable to the test-design gate rather than merely counted.
BENCH=${BENCH:-$HOME/models/bench}
IDX=$BENCH/ocaml-idx2
TAG=${1:-run}
cd "$(dirname "$0")/../.."
run () {
  out=$(timeout 1800 .venv/bin/python -m purecoder.cli --retries 4 --store "$IDX" --lang ocaml code "$1" 2>&1)
  printf '%s\n' "$out" > "$BENCH/$TAG-$2.log"
  verdict=$(printf '%s' "$out" | grep -oE "ok=(True|False)  attempts=[0-9]+" | tail -1)
  echo "[$verdict] $2"
}
run "a function sum_list that takes an int list and returns the sum of its elements" sum_list
run "a function max_of_list that takes a non-empty int list and returns the largest element" max_of_list
run "a function rev_string that takes a string and returns it reversed" rev_string
run "a function is_palindrome that takes a string and returns true when it reads the same backwards" is_palindrome
run "a function insertion_sort that takes an int list and returns a new list sorted ascending" insertion_sort
