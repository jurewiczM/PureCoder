import { useEffect, useRef, useState } from 'react'
import { streamRun, type RunAsk, type RunEvent, type Verdict } from '../api'
import { Marker, VerdictLine, type State } from '../components/Verdict'
import { Roles } from '../components/Roles'
import { Cards } from '../components/Cards'

export interface Run {
  id: string
  spec: string
  lang: string
  /** Exactly what was asked for, kept so a repeat is the same question. */
  ask: RunAsk
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

/** Mirrors `MAX_RETRIES` in purecoder/server.py, which is where the real
 * bound lives: this one keeps the control from offering what the server would
 * only clamp, and is not itself a guarantee. */
const MAX_RETRIES = 10

/** The cycle counts offered. 0 is deliberate and means no limit: keep asking
 * until the spec passes or a person stops it.
 *
 * Unlimited is a real setting rather than a hidden one, and the honesty it
 * needs is in the interface around it -- the count of runs so far is always on
 * screen and "Stop after this run" is always reachable, because the only thing
 * that ends an unlimited chain is a pass or a person. A refusal that is a
 * property of the spec (SQLite has no user-defined functions, so a task asking
 * for one cannot pass in any number of runs) will not converge, and the run
 * counter is what makes that visible instead of quietly expensive. */
const CYCLE_CHOICES = [2, 3, 5, 10, 0] as const
const DEFAULT_CYCLES = 3

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

/**
 * What to do about a run that has finished.
 *
 * The pipeline already goes back and forth on its own -- the writer retries,
 * and when the same failure survives different code the no-progress rule has
 * the suite redesigned. That is the cycle, and it is bounded because the same
 * rule is the evidence that more of it will not help: identical failures mean
 * the next attempt is the previous one again.
 *
 * So what a person needs is not a longer loop but a SECOND run. The model is
 * stochastic and a refused spec often passes on the next ask, which is a
 * different thing from spending attempt five. Each repeat lands as its own
 * record with its own transcript, because two runs of one spec is exactly the
 * comparison this project keeps saying to make.
 */
function Actions({
  run,
  busy,
  onRepeat,
}: {
  run: Run
  busy: boolean
  onRepeat: (ask: RunAsk) => void
}) {
  if (run.running) return null
  const refused = !run.verdict?.ok
  const roomier = Math.min(MAX_RETRIES, run.ask.retries * 2)
  const canGoLonger = refused && roomier > run.ask.retries

  return (
    <div className="mt-8 flex flex-wrap items-center gap-2 border-t border-line pt-5">
      <button
        onClick={() => onRepeat(run.ask)}
        disabled={busy}
        className="rounded border border-line px-3.5 py-1.5 text-xs text-ink transition-colors hover:border-faint disabled:opacity-30"
      >
        Ask again
      </button>
      {canGoLonger ? (
        <button
          onClick={() => onRepeat({ ...run.ask, retries: roomier })}
          disabled={busy}
          className="rounded border border-line px-3.5 py-1.5 text-xs text-ink transition-colors hover:border-faint disabled:opacity-30"
        >
          Ask again with {roomier} attempts
        </button>
      ) : null}
      <span className="text-[11px] text-faint">
        {refused
          ? 'A repeat is a fresh run, kept beside this one. The model is stochastic; the same spec often passes on the next ask.'
          : 'Runs again from the same spec, as a separate record.'}
      </span>
    </div>
  )
}

function Transcript({
  run,
  busy,
  onRepeat,
}: {
  run: Run
  busy: boolean
  onRepeat: (ask: RunAsk) => void
}) {
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

        <Actions run={run} busy={busy} onRepeat={onRepeat} />

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
  keepAsking,
  setKeepAsking,
  maxCycles,
  setMaxCycles,
}: {
  busy: boolean
  languages: string[]
  spec: string
  setSpec: (s: string) => void
  onRun: (ask: RunAsk) => void
  keepAsking: boolean
  setKeepAsking: (v: boolean) => void
  maxCycles: number
  setMaxCycles: (n: number) => void
}) {
  const [lang, setLang] = useState('python')
  const [contract, setContract] = useState(false)
  const [retries, setRetries] = useState(4)

  const submit = () => {
    if (!spec.trim() || busy) return
    onRun({ spec: spec.trim(), lang, contract, retries })
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
            Attempts
            <select
              value={retries}
              onChange={(e) => setRetries(Number(e.target.value))}
              disabled={busy}
              className="rounded border border-line bg-sheet px-2.5 py-1.5 font-mono text-xs text-ink outline-none focus:border-faint"
            >
              {[2, 4, 6, 8, 10].map((n) => (
                <option key={n} value={n}>
                  {n}
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

          <label
            className="flex items-center gap-2 text-muted"
            title="On a refusal, ask the same spec again as a fresh run. A pass ends it."
          >
            <input
              type="checkbox"
              checked={keepAsking}
              onChange={(e) => setKeepAsking(e.target.checked)}
            />
            Keep asking on a refusal
          </label>

          {keepAsking ? (
            <label className="flex items-center gap-2 text-muted">
              Cycles
              <select
                value={maxCycles}
                onChange={(e) => setMaxCycles(Number(e.target.value))}
                className="rounded border border-line bg-sheet px-2.5 py-1.5 font-mono text-xs text-ink outline-none focus:border-faint"
              >
                {CYCLE_CHOICES.map((n) => (
                  <option key={n} value={n}>
                    {n === 0 ? 'no limit' : n}
                  </option>
                ))}
              </select>
            </label>
          ) : null}

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
  const [keepAsking, setKeepAsking] = useState(false)
  /** 0 means unlimited -- see CYCLE_CHOICES. */
  const [maxCycles, setMaxCycles] = useState<number>(DEFAULT_CYCLES)
  /** Which run of the current chain is in flight, counting from 1. */
  const [cycle, setCycle] = useState(0)
  const busy = runs.some((r) => r.running)

  // Read inside the stream's completion handler, which closes over the state
  // it was created with. A ref is the value AT THAT MOMENT rather than at the
  // moment the run started -- so unticking the box stops the chain, which is
  // the only way a person can call it off mid-flight.
  const asking = useRef(keepAsking)
  asking.current = keepAsking
  const limit = useRef(maxCycles)
  limit.current = maxCycles

  const patch = (id: string, change: Partial<Run>) =>
    setRuns((prev) => prev.map((r) => (r.id === id ? { ...r, ...change } : r)))

  const start = (ask: RunAsk, nth = 1) => {
    const id = `${Date.now()}`
    const run: Run = {
      id,
      spec: ask.spec,
      lang: ask.lang,
      ask,
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

    setCycle(nth)

    streamRun(
      { ...ask, no_docs: true },
      (event) =>
        setRuns((prev) =>
          prev.map((r) => (r.id === id ? { ...r, events: [...r.events, event] } : r)),
        ),
    )
      .then((verdict) => {
        patch(id, { verdict, running: false, endedMs: Date.now() })
        // The machine's half of the cycle. It repeats a REFUSAL, never a
        // pass, and only while the box is still ticked -- and the bound is
        // mechanical rather than a promise in the label, because an unattended
        // loop on a local card is the one failure here that costs real money.
        // Unlimited (0) never runs out; a set limit stops when it is reached.
        const room = limit.current === 0 || nth < limit.current
        if (!verdict.ok && asking.current && room) start(ask, nth + 1)
        else setCycle(0)
      })
      // A transport failure is not a refusal and is never repeated: the run
      // never reached the pipeline, so asking again asks the same broken
      // question of the same missing server.
      .catch((e: Error) => {
        patch(id, { failure: e.message, running: false, endedMs: Date.now() })
        setCycle(0)
      })
  }

  const current = runs.find((r) => r.id === selected) ?? null

  return (
    <div className="flex min-h-0 flex-1">
      <RunList runs={runs} selected={selected} onSelect={setSelected} />
      <div className="flex min-w-0 flex-1 flex-col">
        {current ? (
          <Transcript run={current} busy={busy} onRepeat={(ask) => start(ask)} />
        ) : (
          <Empty onPick={setSpec} />
        )}
        {busy && keepAsking && cycle > 0 ? (
          <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-t border-line bg-base px-6 py-2.5">
            <span className="is-working text-xs text-retry">
              Asking again on a refusal
            </span>
            <span className="font-mono text-[11px] text-faint">
              {maxCycles === 0
                ? `run ${cycle}, no limit set`
                : `run ${cycle} of ${maxCycles}`}
            </span>
            {maxCycles === 0 ? (
              <span className="text-[11px] text-faint">
                Only a pass or you will end this.
              </span>
            ) : null}
            <button
              onClick={() => setKeepAsking(false)}
              className="ml-auto rounded border border-line px-3 py-1 text-[11px] text-ink transition-colors hover:border-fail hover:text-fail"
            >
              Stop after this run
            </button>
          </div>
        ) : null}
        <Composer
          busy={busy}
          languages={languages}
          spec={spec}
          setSpec={setSpec}
          onRun={(ask) => start(ask)}
          keepAsking={keepAsking}
          setKeepAsking={setKeepAsking}
          maxCycles={maxCycles}
          setMaxCycles={setMaxCycles}
        />
      </div>
    </div>
  )
}
