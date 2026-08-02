# Examples

Runnable scripts. Each needs `llama-server` up (see the root README); the
RAG one additionally needs `pip install -e ".[rag]"`.

| script | what it shows |
|---|---|
| `generate_function.py` | the core loop on one function, with the tests it used |
| `scaffold_project.py`  | a whole project written to disk |
| `rag_over_docs.py`     | doc-grounded generation over a persisted index |

## `portcheck/` — real output, not a polished sample

`portcheck/` is checked in exactly as `scaffold_project.py` produced it. It is
kept unedited because it shows the system's boundary as honestly as its wins:

- ✅ the code runs and its generated tests pass
- ✅ the Makefile parses and its targets match the entry point
- ⚠️ `parse_ports` **silently drops** out-of-range ports instead of raising on
  them — the spec said "raising ValueError on any out-of-range entry", and the
  code-blind tests agreed with the implementation's reading of it
- ⚠️ `main()` is defined but never called
- ⚠️ the `.env` degenerates into one enormous comment line, and is cut off
  mid-sentence where generation hit `n_predict`

Both warnings sit exactly where [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)
says the boundaries are, and neither is a validator bug:

- The test gate catches *structurally* bad tests, but a plausible-but-wrong
  expected value gets through. The cause is spec ambiguity, not the writer.
  Sharpening the spec ("must raise, must not skip") fixes it; no amount of
  validation will.
- The `.env` grammar guarantees *shape*, and a comment of any length is a
  valid shape — so `validate_env` passes it, correctly. Grammars constrain
  form, never sense. The Makefile validator needed extra semantic guards for
  exactly this reason; `.env` has no equivalent guard yet.
