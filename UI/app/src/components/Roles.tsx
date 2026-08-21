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
 *
 * The cap is printed ONCE, for the run, rather than as a denominator on every
 * row. There are no per-role budgets: `agents.py` declared one, did not
 * enforce it, and deleted the field for the reason its docstring gives — a
 * denominator the numerator can exceed is decoration that reads like a
 * guarantee. "tester 1 of 4" on its own row invites exactly the reading that
 * was removed from the data model, so the shared cap is stated as what it is.
 */
export function Roles({ ledger }: { ledger: Ledger | undefined }) {
  if (!ledger?.roles?.length) return null
  const cap = ledger.roles[0]?.cap ?? 0

  return (
    <section className="mt-8">
      <div className="mb-3 flex items-baseline justify-between">
        <h3 className="eyebrow">Who spent what</h3>
        {cap ? (
          <span className="font-mono text-[11px] text-faint">
            {cap} attempts allowed, shared
          </span>
        ) : null}
      </div>

      <div className="overflow-hidden rounded border border-line">
        {ledger.roles.map((r, i) => (
          <div
            key={r.name}
            className={`flex items-baseline gap-3 px-3.5 py-2.5 ${
              i ? 'border-t border-line-soft' : ''
            }`}
          >
            <Marker
              state={r.accepted ? 'pass' : 'fail'}
              size={8}
              className="translate-y-[-1px]"
            />
            <span
              className="w-16 shrink-0 font-mono text-xs text-ink"
              title={r.role}
            >
              {r.name}
            </span>
            <span className="w-20 shrink-0 text-xs text-muted">
              {r.attempts} attempt{r.attempts === 1 ? '' : 's'}
            </span>
            {/* First line only. SQL reports EVERY failing check rather than
             * the first, so a reason is routinely several lines, and rendering
             * the whole string in one row ran them together into a sentence
             * that never existed: "CHECK FAILED: empty table sum CHECK FAILED:
             * empty table non-null result". The same defect the CLI's roles
             * block had. The full text is under "Why it refused" above and in
             * the title. */}
            <span
              className="min-w-0 flex-1 truncate font-mono text-[11px] text-faint"
              title={r.reason}
            >
              {r.reason.trim().split('\n')[0]}
            </span>
          </div>
        ))}
      </div>

      {ledger.stopped_on ? (
        <p className="mt-2.5 text-xs leading-relaxed text-faint">
          Ran out on <span className="font-mono text-muted">{ledger.stopped_on}</span>.
          That is where it stopped, which is not always what was wrong — a role
          that spent twice was redesigned mid-run, and that is the tell.
        </p>
      ) : null}
    </section>
  )
}
