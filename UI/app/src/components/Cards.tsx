import { useEffect, useState } from 'react'
import type { RunEvent, Verdict } from '../api'
import { Marker, type State } from './Verdict'
import { gates, runStatus, testerStatus, type GateState } from '../status'

/**
 * Three cards at the head of a record: the run, the tester, and the gates.
 *
 * They answer the questions a reader asks before reading the transcript line
 * by line — how long did this take and how much budget did it burn, did the
 * suite get thrown back, and what was actually PROVEN. The transcript below
 * remains the evidence; these are its index.
 *
 * Everything shown is derived in `status.ts` from events the loop emitted or
 * from the verdict envelope. Where the stream does not say, the card says
 * "not run" instead of guessing, which is the whole reason this is worth
 * having in a project whose central failure mode is a plausible number.
 */

const GATE_TONE: Record<GateState, State> = {
  passed: 'pass',
  failed: 'fail',
  'not-run': 'idle',
}

const GATE_WORD: Record<GateState, string> = {
  passed: 'proved',
  failed: 'refused',
  'not-run': 'not run',
}

function Card({
  title,
  aside,
  children,
}: {
  title: string
  aside?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="min-w-0 rounded border border-line bg-raised p-3.5">
      <div className="mb-2.5 flex items-baseline justify-between gap-2">
        <h3 className="eyebrow">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  )
}

/** One fact: what it is, and what it was. Mono on the value, because it is. */
function Fact({ label, value, tone = 'text-ink' }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <span className="shrink-0 text-xs text-faint">{label}</span>
      <span className={`min-w-0 truncate text-right font-mono text-xs ${tone}`} title={value}>
        {value}
      </span>
    </div>
  )
}

export function Cards({
  events,
  verdict,
  lang,
  startedMs,
  endedMs,
}: {
  events: RunEvent[]
  verdict: Verdict | null
  lang: string
  startedMs: number
  endedMs: number | null
}) {
  // A run's first phase emits nothing for tens of seconds, so a card that
  // recomputed only when an event arrived showed "<1s" for the whole of it.
  // Elapsed is the one fact here that changes on its own, so it gets a tick --
  // stopped the moment the run ends, because a finished run's duration is a
  // fact and not a clock.
  const [, tick] = useState(0)
  useEffect(() => {
    if (endedMs !== null) return
    const id = setInterval(() => tick((n) => n + 1), 1000)
    return () => clearInterval(id)
  }, [endedMs])

  const run = runStatus(events, verdict, startedMs, endedMs)
  const tester = testerStatus(events)
  const checks = gates(events, verdict, tester, run)

  return (
    <div className="grid items-start gap-3 md:grid-cols-3">
      <Card title="This run">
        <Fact label="Language" value={lang} />
        <Fact
          label="Attempts"
          value={
            run.attempts === null
              ? 'in flight'
              : `${run.attempts}${run.cap ? ` of ${run.cap}` : ''}`
          }
          tone={run.cap && run.attempts === run.cap ? 'text-retry' : 'text-ink'}
        />
        <Fact label="Elapsed" value={run.elapsed} />
        <Fact label="Documentation" value={run.grounded ? 'retrieved' : 'ungrounded'} />
        {run.truncated ? (
          <Fact label="Truncated" value="hit the token limit" tone="text-retry" />
        ) : null}
      </Card>

      <Card
        title="The tester"
        aside={
          tester.redesigned ? (
            <span className="font-mono text-[10px] text-retry">redesigned</span>
          ) : null
        }
      >
        <Fact
          label="Suites accepted"
          value={tester.accepted ? String(tester.accepted) : 'none yet'}
        />
        <Fact
          label="Drafts refused"
          value={tester.rejected ? String(tester.rejected) : '0'}
          tone={tester.rejected ? 'text-retry' : 'text-ink'}
        />
        <Fact label="Suite size" value={tester.lines === null ? 'unknown' : `${tester.lines} lines`} />
        {tester.redesigned ? (
          <p className="mt-2 text-[11px] leading-relaxed text-faint">
            The same failure survived different code, so the loop suspected the
            suite and had it rewritten. That second acceptance is the tell.
          </p>
        ) : null}
        {!tester.redesigned && tester.lastRejection ? (
          <p className="mt-2 truncate text-[11px] text-faint" title={tester.lastRejection}>
            Last refusal: {tester.lastRejection}
          </p>
        ) : null}
      </Card>

      <Card title="What was proved">
        <div className="flex flex-col gap-2">
          {checks.map((g) => (
            <div key={g.name} className="flex items-baseline gap-2" title={g.does}>
              <Marker state={GATE_TONE[g.state]} size={7} className="translate-y-[-1px]" />
              <span className="min-w-0 flex-1">
                <span className="block text-xs text-ink">
                  {g.name}
                  <span className="ml-1.5 font-mono text-[10px] text-faint">
                    {GATE_WORD[g.state]}
                  </span>
                </span>
                <span className="block truncate text-[11px] text-faint" title={g.detail}>
                  {g.detail}
                </span>
              </span>
            </div>
          ))}
        </div>
        <p className="mt-2.5 text-[11px] leading-relaxed text-faint">
          None of these asks a model anything. That is what makes a pass worth
          something.
        </p>
      </Card>
    </div>
  )
}
