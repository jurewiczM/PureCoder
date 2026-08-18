import { useEffect, useRef, useState } from 'react'
import { streamRun, type RunEvent, type Verdict } from '../api'
import { Marker, VerdictLine, type State } from '../components/Verdict'
import { Roles } from '../components/Roles'
import { Cards } from '../components/Cards'

export interface Run {
  id: string
  spec: string
  lang: string
  startedAt: string
  /** Wall clock, for elapsed. `startedAt` is for display and cannot be
   * subtracted from anything. */
  startedMs: number
  endedMs: number | null
  events: RunEvent[]
  verdict: Verdict | null
  /** Transport-level failure. A refusal is not this -- that is a verdict. */
  failure: string
  running: boolean
}

export function runState(run: Run): State {
  if (run.running) return 'working'
  if (run.failure) return 'fail'
  return run.verdict?.ok ? 'pass' : 'fail'
}

const clock = () =>
  new Date().toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })

/** The line's tone follows what the loop was reporting, nothing else. */
const EVENT_TONE: Record<RunEvent['kind'], string> = {
  tests: 'text-muted',
  attempt: 'text-retry',
  contract: 'text-muted',
  docs: 'text-faint',
  verdict: 'text-pass',
}

function RunList({
  runs,
  selected,
  onSelect,
}: {
  runs: Run[]
  selected: string | null
  onSelect: (id: string) => void
}) {
  if (runs.length === 0) return null

  return (
    <div className="flex w-64 shrink-0 flex-col overflow-y-auto border-r border-line bg-base">
      <p className="eyebrow px-4 pt-4 pb-2">This session</p>
      {runs.map((run) => {
        const on = run.id === selected
        const state = runState(run)
        return (
          <button
            key={run.id}
            onClick={() => onSelect(run.id)}
            className={`flex items-baseline gap-2.5 px-4 py-3 text-left transition-colors ${
              on ? 'bg-sheet' : 'hover:bg-line-soft'
            }`}
          >
            <Marker state={state} size={8} className="translate-y-[-1px]" />
            <span className="min-w-0 flex-1 truncate text-xs text-ink">{run.spec}</span>
            <span className="shrink-0 font-mono text-[11px] text-faint">{run.lang}</span>
          </button>
        )
      })}
      <p className="mt-auto px-4 py-4 text-[11px] leading-relaxed text-faint">
        Kept in this tab only. A reload clears the list; nothing is written to
        disk.
      </p>
    </div>
  )
}

/**
 * A block of machine output: code, a suite, a diagnostic.
 *
 * Always monospaced, always on the raised surface, always scrollable in its
 * own right rather than widening the record. Long lines are the normal case
 * here -- a compiler diagnostic carries a whole path -- and a page that grows
 * sideways to fit one of them is unreadable for everything else.
 */
function Evidence({
  label,
  children,
  tone = 'text-ink',
}: {
  label: string
  children: string
  tone?: string
}) {
  return (
    <figure className="mt-6">
      <figcaption className="eyebrow mb-2">{label}</figcaption>
      <pre
        className={`overflow-x-auto rounded border border-line bg-raised px-4 py-3.5 font-mono text-xs leading-6 ${tone}`}
      >
        {children}
      </pre>
    </figure>
  )
}

function Transcript({ run }: { run: Run }) {
  const tail = useRef<HTMLDivElement>(null)

  // Follow the run while it is producing lines. Scrolling the container rather
  // than the page keeps the composer below it fixed in place.
  useEffect(() => {
    if (run.running) tail.current?.scrollIntoView({ block: 'end' })
  }, [run.events.length, run.running])

  const verdict = run.verdict
  const state = runState(run)

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto bg-sheet">
      {/* The head of the record: what was asked, and how it was ruled on. */}
      <div className="sticky top-0 z-10 border-b border-line bg-sheet px-6 pt-5 pb-4">
        <p className="eyebrow mb-1.5">The request</p>
        <p className="max-w-3xl text-[15px] leading-snug text-ink">{run.spec}</p>
        <div className="mt-3.5 flex items-baseline gap-4">
          <VerdictLine
            large
            state={state}
            detail={
              run.running
                ? `started ${run.startedAt}`
                : verdict
                  ? `${verdict.attempts} attempt${verdict.attempts === 1 ? '' : 's'}`
                  : undefined
            }
          />
          <span className="font-mono text-xs text-faint">{run.lang}</span>
        </div>
        {run.running ? (
          <div className="is-scanning relative mt-4 h-px overflow-hidden bg-line" />
        ) : null}
      </div>

      <div className="px-6 py-5">
        <Cards
          events={run.events}
          verdict={verdict}
          lang={run.lang}
          startedMs={run.startedMs}
          endedMs={run.endedMs}
        />

        <p className="eyebrow mt-8 mb-2">The run, line by line</p>

        {/* THE LEDGER. Role in the margin, a continuous rule, the line beside
         * it -- so who produced a line is read off the layout rather than
         * hunted for inside it. */}
        {run.events.length ? (
          <div className="ledger">
            <div className="ledger-spine" aria-hidden />
            {run.events.map((event, i) => (
              <div key={i} className="contents">
                <div className="ledger-role py-0.5">{event.agent}</div>
                <div
                  className={`ledger-body py-0.5 font-mono text-xs leading-6 ${
                    EVENT_TONE[event.kind] ?? 'text-muted'
                  }`}
                >
                  {event.agent ? (
                    <Marker
                      state="idle"
                      size={6}
                      className="ledger-mark translate-y-[-2px]"
                    />
                  ) : null}
                  {event.text}
                </div>
              </div>
            ))}
          </div>
        ) : null}

        {run.running && run.events.length === 0 ? (
          <p className="text-xs text-muted">
            <span className="is-working">Asking the tester for a suite.</span>{' '}
            Nothing is generated until there is something to judge it with.
          </p>
        ) : null}

        {run.failure ? (
          <p className="mt-4 rounded border border-fail/40 bg-fail/5 px-3.5 py-2.5 text-xs text-fail">
            {run.failure}
          </p>
        ) : null}

        {verdict && !verdict.ok && verdict.error ? (
          <Evidence label="Why it refused" tone="text-fail">
            {verdict.error}
          </Evidence>
        ) : null}

        <Roles ledger={verdict?.agents} />

        {verdict?.code ? (
          <Evidence label={`The ${run.lang} it emitted`}>{verdict.code}</Evidence>
        ) : null}

        {verdict?.tests ? (
          <details className="mt-6 rounded border border-line">
            <summary className="cursor-pointer px-3.5 py-2.5 text-xs text-muted transition-colors hover:text-ink">
              The suite it was judged against
            </summary>
            <pre className="overflow-x-auto border-t border-line px-4 py-3.5 font-mono text-xs leading-6 text-muted">
              {verdict.tests}
            </pre>
          </details>
        ) : null}

        <div ref={tail} />
      </div>
    </div>
  )
}

/* The corpus from scripts/bench/tasks.tsv, verbatim. Real specs the project
 * already measures itself against -- not invented examples, and short enough
 * that a first run finishes while the reader is still watching it. */
const CORPUS: [string, string][] = [
  ['sum_list', 'a function sum_list that returns the sum of a list of integers; the empty list returns 0'],
  ['rev_string', 'a function rev_string that returns a string reversed; the empty string returns the empty string'],
  ['gcd', 'a function gcd that returns the greatest common divisor of two non-negative integers; gcd of x and 0 is x, and gcd of 0 and 0 is 0'],
]

/**
 * The empty state, which is where a first-time reader starts.
 *
 * It has one job: explain why this page is a transcript and not a dashboard,
 * then hand over a spec to run. The corpus entries used to be bare words that
 * looked like prose and behaved like buttons; they are cards now, because a
 * control that does not look like one is not an affordance.
 */
function Empty({ onPick }: { onPick: (spec: string) => void }) {
  return (
    <div className="flex-1 overflow-y-auto bg-sheet px-6 py-10">
      <div className="mx-auto max-w-2xl">
        <h2 className="text-xl leading-snug font-semibold text-ink">
          Read the run, not the score.
        </h2>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          A run reports the same verdict whether the model wrote bad code or the
          harness refused good code. Those are opposite bugs and the verdict
          cannot tell them apart — only the transcript can, which is why it is
          the page here rather than a detail view.
        </p>

        <p className="eyebrow mt-9 mb-3">Start with a spec from the corpus</p>
        <div className="grid gap-2">
          {CORPUS.map(([name, spec]) => (
            <button
              key={name}
              onClick={() => onPick(spec)}
              className="group rounded border border-line bg-raised px-4 py-3 text-left transition-colors hover:border-faint"
            >
              <span className="font-mono text-xs text-ink">{name}</span>
              <span className="mt-1 block text-xs leading-relaxed text-faint group-hover:text-muted">
                {spec}
              </span>
            </button>
          ))}
        </div>
        <p className="mt-3 text-[11px] text-faint">
          These are the tasks the benchmark measures this pipeline against, word
          for word.
        </p>
      </div>
    </div>
  )
}

function Composer({
  busy,
  languages,
  spec,
  setSpec,
  onRun,
}: {
  busy: boolean
  languages: string[]
  spec: string
  setSpec: (s: string) => void
  onRun: (spec: string, lang: string, contract: boolean) => void
}) {
  const [lang, setLang] = useState('python')
  const [contract, setContract] = useState(false)

  const submit = () => {
    if (!spec.trim() || busy) return
    onRun(spec.trim(), lang, contract)
    setSpec('')
  }

  return (
    <div className="shrink-0 border-t border-line bg-base px-6 py-4">
      <div className="mx-auto max-w-4xl">
        <textarea
          value={spec}
          onChange={(e) => setSpec(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit()
          }}
          rows={2}
          disabled={busy}
          placeholder="Describe one function — what it takes, what it returns, and what the empty case does."
          className="w-full resize-none rounded border border-line bg-sheet px-3.5 py-3 text-sm leading-relaxed text-ink outline-none placeholder:text-faint focus:border-faint disabled:opacity-40"
        />

        {/* The controls sit together and the action sits with them. `run` used
         * to be flung to the far right of a full-width bar, a thousand pixels
         * from the language it applies to. */}
        <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-3 text-xs">
          <label className="flex items-center gap-2 text-muted">
            Language
            <select
              value={lang}
              onChange={(e) => setLang(e.target.value)}
              disabled={busy}
              className="rounded border border-line bg-sheet px-2.5 py-1.5 font-mono text-xs text-ink outline-none focus:border-faint"
            >
              {languages.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          </label>

          <label className="flex items-center gap-2 text-muted">
            <input
              type="checkbox"
              checked={contract}
              onChange={(e) => setContract(e.target.checked)}
              disabled={busy}
            />
            Derive a contract first
          </label>

          <button
            onClick={submit}
            disabled={busy || !spec.trim()}
            className="rounded border border-faint px-5 py-1.5 text-xs font-medium text-ink transition-colors hover:border-pass hover:text-pass disabled:opacity-30 disabled:hover:border-faint disabled:hover:text-ink"
          >
            {busy ? 'Running…' : 'Run'}
          </button>

          <span className="ml-auto text-[11px] text-faint">
            {busy
              ? 'The run blocks until the pipeline decides.'
              : 'Ctrl+Enter runs it.'}
          </span>
        </div>
      </div>
    </div>
  )
}

export function RunsPane({ languages }: { languages: string[] }) {
  const [runs, setRuns] = useState<Run[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [spec, setSpec] = useState('')
  const busy = runs.some((r) => r.running)

  const patch = (id: string, change: Partial<Run>) =>
    setRuns((prev) => prev.map((r) => (r.id === id ? { ...r, ...change } : r)))

  const start = (text: string, lang: string, contract: boolean) => {
    const id = `${Date.now()}`
    const run: Run = {
      id,
      spec: text,
      lang,
      startedAt: clock(),
      startedMs: Date.now(),
      endedMs: null,
      events: [],
      verdict: null,
      failure: '',
      running: true,
    }
    setRuns((prev) => [run, ...prev])
    setSelected(id)

    streamRun(
      { spec: text, lang, contract, retries: 4, no_docs: true },
      (event) =>
        setRuns((prev) =>
          prev.map((r) => (r.id === id ? { ...r, events: [...r.events, event] } : r)),
        ),
    )
      .then((verdict) => patch(id, { verdict, running: false, endedMs: Date.now() }))
      .catch((e: Error) =>
        patch(id, { failure: e.message, running: false, endedMs: Date.now() }),
      )
  }

  const current = runs.find((r) => r.id === selected) ?? null

  return (
    <div className="flex min-h-0 flex-1">
      <RunList runs={runs} selected={selected} onSelect={setSelected} />
      <div className="flex min-w-0 flex-1 flex-col">
        {current ? <Transcript run={current} /> : <Empty onPick={setSpec} />}
        <Composer
          busy={busy}
          languages={languages}
          spec={spec}
          setSpec={setSpec}
          onRun={start}
        />
      </div>
    </div>
  )
}
