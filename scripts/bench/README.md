# The five-task OCaml benchmark

What produced the tables in `docs/live-runs/2026-08-06-*` and `2026-08-07-*`.
Small, and deliberately so: five pure functions in a language the model has
little of in its weights, so the run depends on retrieval and on the harness
rather than on recall.

## The corpus

The index is built from ocaml.org's own docs. This exact recipe reproduced a
destroyed index to the chunk (3017), so it is worth following literally:

```bash
D=~/models/bench/ocaml-web            # NOT /tmp -- a cleanup ate the first one
mkdir -p $D && cd $D
curl -sSL -o tut.tar.gz https://github.com/ocaml/ocaml.org/archive/refs/heads/main.tar.gz
tar xzf tut.tar.gz --strip-components=3 \
    ocaml.org-main/data/tutorials ocaml.org-main/data/cookbook
rm -f tut.tar.gz
cp /usr/lib/ocaml/{list,string,option,hashtbl,printf}.mli $D/

cd <repo> && .venv/bin/python -m purecoder.cli \
    --store ~/models/bench/ocaml-idx2 ingest $D -y
```

## Running it

```bash
scripts/bench/moe-probe.sh                  # find a working GPU/RAM split
scripts/bench/ocaml-batch.sh <tag>          # five tasks, transcripts kept
```

`ocaml-batch.sh` writes a full transcript per task to `$BENCH/<tag>-<task>.log`.
**Keep them.** A run reports `ok=False attempts=4` whether the model wrote bad
code or the harness refused good code, and every defect in the 2026-08-07 write-up
was found in a transcript, not in a score. An earlier version of this script
discarded them and a batch of correct implementations was nearly recorded as a
capability result.

Both scripts read `$BENCH` (default `~/models/bench`) for the index and logs.
