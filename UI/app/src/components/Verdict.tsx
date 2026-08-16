/**
 * How a verdict looks, in one place.
 *
 * Shape carries the same information as colour: filled for a pass, half for a
 * run still going, hollow for a refusal. Hue alone would make the only output
 * this tool has unreadable to anyone who cannot separate red from green, and
 * pass/fail is not a detail here -- it is the entire message.
 */

export type State = 'pass' | 'working' | 'fail' | 'idle'

const TONE: Record<State, string> = {
  pass: 'text-pass',
  working: 'text-retry',
  fail: 'text-fail',
  idle: 'text-faint',
}

export function Marker({ state, className = '' }: { state: State; className?: string }) {
  const tone = TONE[state]
  const working = state === 'working'
  return (
    <span
      aria-hidden
      className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full border ${tone} ${
        working ? 'is-working' : ''
      } ${className}`}
      style={{
        borderColor: 'currentColor',
        // Filled, half, hollow -- readable with the colour removed.
        background:
          state === 'pass'
            ? 'currentColor'
            : working
              ? 'linear-gradient(to right, currentColor 50%, transparent 50%)'
              : 'transparent',
      }}
    />
  )
}

const WORD: Record<State, string> = {
  pass: 'ok',
  working: 'running',
  fail: 'refused',
  idle: 'idle',
}

export function VerdictLine({ state, detail }: { state: State; detail?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 text-xs ${TONE[state]}`}>
      <Marker state={state} />
      {WORD[state]}
      {detail ? <span className="text-faint">{detail}</span> : null}
    </span>
  )
}
