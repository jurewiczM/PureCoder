import { useEffect, useRef, useState } from 'react'
import { streamRun, type RunEvent, type Verdict } from '../api'
import { Marker, VerdictLine, type State } from '../components/Verdict'

export interface Run {
  id: string
  spec: string
  lang: string
  startedAt: string
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
  return (
    <div className="flex w-64 shrink-0 flex-col overflow-y-auto border-r border-line">
      {runs.length === 0 ? (
        <p className="px-5 py-4 text-xs leading-relaxed text-faint">
          No runs in this session. The list is not stored — a reload clears it.
        </p>
      ) : null}
      {runs.map((run) => {
        const on = run.id === selected
        const state = runState(run)
        return (
          <button
            key={run.id}
            onClick={() => onSelect(run.id)}
            className={`flex items-center gap-2.5 border-b border-line-soft px-4 py-3 text-left transition-colors ${
              on ? 'bg-surface' : 'hover:bg-line-soft'
            }`}
          >
            <Marker state={state} />
            <span className="min-w-0 flex-1 truncate text-xs text-ink">{run.spec}</span>
            <span className="shrink-0 text-[11px] text-faint">{run.lang}</span>
          </button>
        )
      })}
    </div>
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
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="flex items-baseline justify-between border-b border-line px-5 py-3">
        <span className="mr-4 truncate text-xs text-ink">{run.spec}</span>
        <VerdictLine
          state={runState(run)}
          detail={
            run.running
              ? run.startedAt
              : verdict
                ? `${verdict.attempts} attempt${verdict.attempts === 1 ? '' : 's'}`
                : undefined
          }
        />
      </div>

      <div className="flex-1 px-5 py-4 text-xs leading-7">
        {run.events.map((event, i) => (
          <div key={i} className={EVENT_TONE[event.kind] ?? 'text-muted'}>
            {event.text}
          </div>
        ))}
        {run.running && run.events.length === 0 ? (
          <div className="is-working text-faint">waiting for the first attempt…</div>
        ) : null}
        {run.failure ? <div className="text-fail">{run.failure}</div> : null}

        {verdict && !verdict.ok && verdict.error ? (
          <div className="mt-4 border-l-2 border-fail pl-3 whitespace-pre-wrap text-fail">
            {verdict.error}
          </div>
        ) : null}

        {verdict?.code ? (
          <pre className="mt-5 overflow-x-auto border-t border-line pt-4 text-ink">
            {verdict.code}
          </pre>
        ) : null}

        {verdict?.tests ? (
          <details className="mt-4 border-t border-line pt-3">
            <summary className="cursor-pointer text-faint hover:text-muted">
              the suite it was judged against
            </summary>
            <pre className="mt-3 overflow-x-auto text-muted">{verdict.tests}</pre>
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

function Empty({ onPick }: { onPick: (spec: string) => void }) {
  return (
    <div className="flex-1 overflow-y-auto px-6 py-8">
      <p className="max-w-xl text-xs leading-relaxed text-muted">
        A run reports the same verdict whether the model wrote bad code or the
        harness refused good code. The transcript is what tells them apart, so
        it is the page here rather than a detail view.
      </p>
      <p className="mt-6 mb-2 text-xs text-faint">
        Three specs from the benchmark corpus, if you want one to hand:
      </p>
      <div className="flex flex-col items-start gap-1">
        {CORPUS.map(([name, spec]) => (
          <button
            key={name}
            onClick={() => onPick(spec)}
            className="text-xs text-faint transition-colors hover:text-pass"
          >
            {name}
          </button>
        ))}
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
    <div className="shrink-0 border-t border-line bg-surface px-5 py-4">
      <textarea
        value={spec}
        onChange={(e) => setSpec(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit()
        }}
        rows={2}
        disabled={busy}
        placeholder="a function that…"
        className="w-full resize-none rounded border border-line bg-base px-3 py-2.5 text-xs leading-relaxed text-ink outline-none placeholder:text-faint focus:border-faint disabled:opacity-40"
      />
      <div className="mt-2.5 flex items-center gap-4 text-xs">
        <label className="flex items-center gap-2 text-faint">
          language
          <select
            value={lang}
            onChange={(e) => setLang(e.target.value)}
            disabled={busy}
            className="rounded border border-line bg-base px-2 py-1 text-ink outline-none focus:border-faint"
          >
            {languages.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-faint">
          <input
            type="checkbox"
            checked={contract}
            onChange={(e) => setContract(e.target.checked)}
            disabled={busy}
            className="accent-pass"
          />
          derive a contract first
        </label>
        <button
          onClick={submit}
          disabled={busy || !spec.trim()}
          className="ml-auto rounded border border-faint px-4 py-1.5 text-ink transition-colors hover:border-pass hover:text-pass disabled:opacity-30"
        >
          {busy ? 'running' : 'run'}
        </button>
      </div>
      <p className="mt-2 text-[11px] text-faint">
        Runs block until the pipeline decides. Nothing is written to disk.
      </p>
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
      .then((verdict) => patch(id, { verdict, running: false }))
      .catch((e: Error) => patch(id, { failure: e.message, running: false }))
  }

  const current = runs.find((r) => r.id === selected) ?? null

  return (
    <div className="flex min-h-0 flex-1">
      <RunList runs={runs} selected={selected} onSelect={setSelected} />
      <div className="flex min-w-0 flex-1 flex-col">
        {current ? (
          <Transcript run={current} />
        ) : (
          <Empty onPick={setSpec} />
        )}
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
