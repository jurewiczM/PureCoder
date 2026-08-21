# HTML in `ingest`

Roadmap item 4 in `docs/STATUS.md`: "It matches prose and source extensions,
and the web's documentation is HTML -- skipped whole rather than stripped and
indexed."

## What was built

`html_to_text` converts a docs page to headed prose; `chunk_file` routes
`.html`, `.htm` and `.xhtml` through it into the markdown chunker; and
`INGEST_PATTERN` admits those extensions to the walk.

Three decisions worth not re-deriving:

- **Headings become `#` lines.** `chunk_markdown` sections on those and on
  nothing else, so a page flattened to one paragraph is one chunk however long
  it is. That would defeat retrieval more quietly than not indexing at all.
- **stdlib `html.parser`, not BeautifulSoup.** `server.py` states the project's
  only runtime dependencies are `requests` and `numpy`. It also does not raise
  on unclosed tags or a stray `<`: half a page of real documentation beats an
  exception that skips the file whole.
- **A list that is at least three items and at least 80% link text is a table
  of contents, and is dropped.** Keying on the container's name does not
  generalise -- every docs generator spells it differently. This keys on what a
  contents list is.

## How it was verified

Unit: 670 passed, ruff clean. Every new test was run against the tree without
its fix and fails there.

Live, against `/usr/share/doc/nodejs/api` -- 70 real published pages, not a
fixture:

```
old pattern chunks: 21
new pattern chunks: 6630 from 70 files      # before the TOC filter
chunks now: 6229                            # after it, 401 dropped
TOC-shaped chunks: 0
```

```
$ purecoder --store ~/models/bench/smoke-html-idx ingest /usr/share/doc/nodejs/api -y
[rag] dropped 6194 duplicate chunks
[rag] 3466 qualified names -- fs (103), buf (86), process (68), crypto (64)
[rag] ingested 6229 chunks from /usr/share/doc/nodejs/api
real 0m46.189s
```

Retrieval, live embedder against that index -- three queries, three correct
sources, heading structure intact:

```
Q: how do I read a file asynchronously and get its contents
  ##### filehandle.readFile(options)# ... allows aborting an in-progress read
Q: create a TCP server that listens on a port
  ##### server.listen([port[, host[, backlog]]][, callback])# ...
Q: hash a string with sha256
  #### crypto.createHash(algorithm[, options])# ...
```

End to end, generation grounded on HTML-derived chunks:

```
$ purecoder --store ~/models/bench/smoke-html-idx --lang javascript ask \
    "a function extName(p) that returns the file extension ..."
[rag] 1061 chars of documentation
[tests] accepted on attempt 1 (7 lines)
[attempt 1] all tests passed
ok=True  attempts=1
```

The TOC filter exists **because** of that live run. The synthetic fixtures had
no contents list, so 401 of 6630 chunks were Node's per-page TOC flattened to
`filehandle.close() filehandle.read(...)` before anyone looked.

## Known gaps

- **`LINK_LIST_ITEMS = 3` and `LINK_LIST_RATIO = 0.8` are judgement, not
  measurement.** They are proven against this corpus and four hand-written
  shapes, not fitted to a labelled set.
- **A concatenated `all.html` dominates.** Node ships one, and since dedup
  keeps the first occurrence, most surviving chunks are attributed to it rather
  than to the per-module page. Retrieval is unaffected; provenance in a citation
  would be less useful than it looks.
- **Tables are flattened to text.** A parameter table reads as a run of cells.
- **`.xhtml` is admitted by pattern but was never run against real XHTML.**
- **JavaScript-rendered documentation still yields nothing.** A page whose prose
  arrives by fetch is empty to a parser, and this does not execute anything.
- **Unrelated defect found by the smoke, not fixed here:** asked for a
  synchronous `sha256Hex`, the model wrote the correct `createHash('sha256')
  .update(str).digest('hex')` and was refused three times because the tester
  asserted a SHA-256 digest it had invented. Verified correct under node. Same
  class as python/`count_vowels`: an expected value nothing could derive
  without computing it. It belongs to roadmap item 3, the tester.
