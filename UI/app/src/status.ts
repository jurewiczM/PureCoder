import type { RunEvent, Verdict } from './api'

/**
 * What a run's transcript says about itself, derived — never invented.
 *
 * Every field here is read off events the loop actually emitted or off the
 * verdict envelope. Where a fact is not in the stream, the answer is
 * `unknown`, and the card renders that as "not run" rather than filling it in
 * with something plausible. A panel that has to make up its contents is the
 * defect this repository exists to avoid, and it does not stop being one
 * because it is rendered in a rounded rectangle.
 *
 * TWO OF THESE FACTS ARE PARSED OUT OF TEXT, which is the weak seam and is
 * marked as such below. `kind`, `agent` and `attempt` are structured fields
 * and are used wherever they can answer the question; the suite's line count
 * and the gate's rejection reason exist only inside a sentence meant for a
 * person. The markers are collected in `MARKERS` so a change to the loop's
 * wording breaks one table rather than five call sites — and if the loop is
 * reworded, these degrade to `unknown`, which is the honest failure and not a
 * wrong number.
 */

/** The loop's own phrases, from purecoder/execute.py. One table, on purpose. */
const MARKERS = {
  /** `[tests] accepted on attempt 2 (3 lines)` */
  accepted: /\[tests\] accepted on attempt (\d+) \((\d+) lines?\)/,
  /** `[tests] attempt 1 rejected: only 1 check(s); need at least 3 -- ...` */
  rejected: /\[tests\] attempt \d+ rejected: (.+?)(?: ->|$)/,
  /** The no-progress rule firing: the same failure across DIFFERENT code. */
  redesigned: 'suspecting the tests, redesigning them',
  /** The gate refusing outright rather than rejecting one draft. */
  gateNeverSatisfied: 'gate never satisfied',
  truncated: 'truncated',
} as const

export type GateState = 'passed' | 'failed' | 'not-run'

export interface Gate {
  name: string
  /** What it actually checks — not a slogan; shown to the reader. */
  does: string
  state: GateState
  detail: string
}

export interface RunStatus {
  attempts: number | null
  cap: number | null
  elapsed: string
  grounded: boolean
  contract: 'derived' | 'rejected' | 'off'
  truncated: boolean
}

export interface TesterStatus {
  /** Suites the tester got accepted. Two means the loop redesigned one. */
  accepted: number
  /** Drafts the quality gate threw back before one was accepted. */
  rejected: number
  /** Lines in the last accepted suite, or null when the loop never said. */
  lines: number | null
  /** The no-progress rule fired: the same failure survived different code. */
  redesigned: boolean
  lastRejection: string
}

const seconds = (ms: number): string =>
  ms < 1000 ? '<1s' : ms < 60_000 ? `${Math.round(ms / 1000)}s` : `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`

export function runStatus(
  events: RunEvent[],
  verdict: Verdict | null,
  startedMs: number,
  endedMs: number | null,
): RunStatus {
  const text = events.map((e) => e.text).join('\n')
  return {
    attempts: verdict ? verdict.attempts : null,
    cap: verdict?.agents?.roles?.[0]?.cap ?? null,
    elapsed: seconds((endedMs ?? Date.now()) - startedMs),
    // Retrieval announces itself; silence means the run was ungrounded, which
    // is a real state and not a missing one.
    grounded: events.some((e) => e.kind === 'docs'),
    contract: verdict?.contract
      ? 'derived'
      : events.some((e) => e.kind === 'contract')
        ? 'rejected'
        : 'off',
    truncated: text.includes(MARKERS.truncated),
  }
}

export function testerStatus(events: RunEvent[]): TesterStatus {
  const tests = events.filter((e) => e.kind === 'tests')
  let accepted = 0
  let rejected = 0
  let lines: number | null = null
  let lastRejection = ''

  for (const e of tests) {
    const ok = MARKERS.accepted.exec(e.text)
    if (ok) {
      accepted += 1
      lines = Number(ok[2])
      continue
    }
    const no = MARKERS.rejected.exec(e.text)
    if (no) {
      rejected += 1
      lastRejection = no[1].trim()
    }
  }

  return {
    accepted,
    rejected,
    lines,
    redesigned: events.some((e) => e.text.includes(MARKERS.redesigned)),
    lastRejection,
  }
}

/**
 * The gates, and what each one actually proved on this run.
 *
 * These are the things with the veto. None of them asks a model anything,
 * which is the property that makes a passing run mean something, so they are
 * named separately from the roles rather than folded in beside them.
 *
 * `not-run` is a first-class answer. A gate that never fired on this run has
 * proved nothing, and showing it as a tick would be a false green in a panel
 * built to prevent exactly that.
 */
export function gates(
  events: RunEvent[],
  verdict: Verdict | null,
  tester: TesterStatus,
  run: RunStatus,
): Gate[] {
  const text = events.map((e) => e.text).join('\n')
  const finished = verdict !== null
  const emitted = Boolean(verdict?.code)

  const gateRefused = text.includes(MARKERS.gateNeverSatisfied)
  const quality: GateState = gateRefused
    ? 'failed'
    : tester.accepted > 0
      ? 'passed'
      : 'not-run'

  // The executor is the only gate that can say code RAN. A verdict with code
  // in it is the loop's statement that the suite executed and a check in it
  // actually fired -- that is what `ok` means here, and it is why a pass is
  // worth anything.
  const execution: GateState = !finished
    ? 'not-run'
    : emitted && verdict?.ok
      ? 'passed'
      : 'failed'

  const contract: GateState =
    run.contract === 'off' ? 'not-run' : run.contract === 'derived' ? 'passed' : 'failed'

  return [
    {
      name: 'Test quality gate',
      does: 'refuses a suite that cannot judge anything — too few checks, degenerate assertions, tests aimed at the wrong target',
      state: quality,
      detail: gateRefused
        ? 'never satisfied — the run stopped before code was written'
        : tester.rejected
          ? `${tester.rejected} draft${tester.rejected === 1 ? '' : 's'} thrown back${tester.lastRejection ? `, last: ${tester.lastRejection}` : ''}`
          : tester.accepted
            ? 'accepted the first draft'
            : 'no suite reached it',
    },
    {
      name: 'Execution',
      does: 'compiles where the language needs it, runs the suite in a sandbox, and proves a check actually executed',
      state: execution,
      detail: !finished
        ? 'still running'
        : execution === 'passed'
          ? 'compiled, ran, and a check fired'
          : verdict?.error
            ? verdict.error.trim().split('\n')[0]
            : 'nothing was emitted',
    },
    {
      name: 'Contract',
      does: 'turns the prose spec into a shape both the tester and the writer are held to',
      state: contract,
      detail:
        run.contract === 'off'
          ? 'not requested for this run'
          : run.contract === 'derived'
            ? 'derived and used by both roles'
            : 'rejected — the run continued without one',
    },
  ]
}
