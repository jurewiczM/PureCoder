import type { Ledger } from '../api'
import { Marker } from './Verdict'

/**
 * What each role spent on a run.
 *
 * Every role, always — never the one the run stopped on by itself. Those are
 * different facts and conflating them is actively misleading: a live SQL run
 * stopped on `writer` while the implementation was a correct
 * `COALESCE(SUM(n), 0)` and the tester was what could not be satisfied. A
 * reader shown only "writer" goes and fixes correct code.
 *
 * The loop's no-progress rule already suspects the tests when the same failure
 * survives different generated code, and redesigns them — which shows up here
 * as the tester spending twice. That second spend is the tell, and it is only
 * visible if the whole roster is on screen.
 */
export function Roles({ ledger }: { ledger: Ledger | undefined }) {
  if (!ledger?.roles?.length) return null

  return (
    <div className="mt-5 border-t border-line pt-4">
      <p className="mb-2 text-[11px] text-faint">roles</p>
      <div className="flex flex-col gap-1.5">
        {ledger.roles.map((r) => (
          <div key={r.name} className="flex items-baseline gap-3 text-xs">
            <Marker
              state={r.accepted ? 'pass' : 'fail'}
              className="translate-y-0.5"
            />
            <span className="w-20 shrink-0 text-ink" title={r.role}>
              {r.name}
            </span>
            <span className="w-24 shrink-0 text-faint">
              {r.attempts} of {r.cap}
            </span>
            <span className="min-w-0 flex-1 truncate text-faint" title={r.reason}>
              {r.reason}
            </span>
          </div>
        ))}
      </div>
      {ledger.stopped_on ? (
        <p className="mt-2.5 text-[11px] leading-relaxed text-faint">
          Ran out on <span className="text-muted">{ledger.stopped_on}</span> —
          where it stopped, which is not always what was wrong.
        </p>
      ) : null}
    </div>
  )
}
