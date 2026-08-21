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

export function Marker({
  state,
  className = '',
  size = 10,
}: {
  state: State
  className?: string
  size?: number
}) {
  const working = state === 'working'
  return (
    <span
      aria-hidden
      className={`inline-block shrink-0 rounded-full border ${TONE[state]} ${
        working ? 'is-working' : ''
      } ${className}`}
      style={{
        width: size,
        height: size,
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
  pass: 'passed',
  working: 'running',
  fail: 'refused',
  idle: 'idle',
}

/**
 * The ruling, at the head of the record.
 *
 * This is the one fact on the page that gets rank. It used to be set at the
 * same size and in the same face as a form label, which is a strange thing to
 * do to the only line that says whether anything was emitted -- and it left
 * the reader hunting for it. Set in the UI face because it is the interface
 * ruling, not machine output; the count beside it is mono because it is.
 */
export function VerdictLine({
  state,
  detail,
  large = false,
}: {
  state: State
  detail?: string
  large?: boolean
}) {
  return (
    <span
      className={`inline-flex items-baseline gap-2.5 ${TONE[state]} ${
        large ? 'text-lg font-semibold' : 'text-sm font-medium'
      }`}
    >
      <Marker state={state} size={large ? 12 : 10} className="translate-y-[-1px]" />
      {WORD[state]}
      {detail ? (
        <span className="font-mono text-xs font-normal text-faint">{detail}</span>
      ) : null}
    </span>
  )
}
