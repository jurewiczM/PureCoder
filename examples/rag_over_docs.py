#!/usr/bin/env python3
"""Ground generation in a library's own docs via retrieval.

    python examples/rag_over_docs.py <docs_dir> "<spec>" [store_path]

Embedding is the slow part, so the index is persisted: pass a docs_dir once,
then reuse the same store path on later runs (see --store on the CLI).
"""

import os
import sys

from purecoder import PureCoder, generate_validated_python
from purecoder.rag import DocStore, Embedder, retrieve_context


def main():
    if len(sys.argv) < 3:
        print(__doc__.strip())
        return 1

    docs_dir, spec = sys.argv[1], sys.argv[2]
    store_path = sys.argv[3] if len(sys.argv) > 3 else "docstore"

    embedder = Embedder(model_name="BAAI/bge-small-en-v1.5", device="cuda")
    store = DocStore(embedder, path=store_path)

    if os.path.exists(store_path + ".npy"):
        store.load()                      # already embedded -- skip the slow part
        print(f"[rag] loaded {len(store.chunks)} chunks from {store_path}")
    else:
        store.ingest_dir(docs_dir)
        store.save()

    pc = PureCoder()
    # Retrieval grounds the prompt; it does not excuse the code from being
    # run. This mirrors `purecoder ask`, which is the point of the example --
    # printing a raw completion here would contradict the project's one rule.
    ctx = retrieve_context(store, spec)
    task = f"{ctx}\n\n{spec}" if ctx else spec
    result = generate_validated_python(pc, task)
    print(result["text"])
    print("passed:", result["ok"], "in", result["attempts"], "attempts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
