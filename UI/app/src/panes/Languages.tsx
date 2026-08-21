import type { Status } from '../api'
import { Marker } from '../components/Verdict'
import { PaneTitle } from '../components/Chrome'

/**
 * The registry, as probed on this machine.
 *
 * The reason matters more than the boolean. "Unavailable" covers three
 * different situations the registry already distinguishes in words -- a
 * toolchain that is not installed, a language declared but never wired, and
 * one that cannot be validated locally at all -- and only the first is
 * something the reader can act on.
 *
 * Which is why the two groups are separated rather than interleaved. The old
 * list printed "toolchain probed, harness wired" seven times in identical
 * rows: seven lines of text carrying no information, styled exactly like the
 * four rows that carried all of it. The runnable ones share one sentence at
 * the top of their group and then need nothing but their names; the refusals
 * are the rows worth reading, so they are the rows with prose in them.
 */
export function LanguagesPane({ status }: { status: Status | null }) {
  const entries = Object.entries(status?.languages ?? {})
  const ready = entries.filter(([, l]) => l.available)
  const refused = entries.filter(([, l]) => !l.available)

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto bg-sheet">
      <PaneTitle aside={`${ready.length} of ${entries.length} runnable here`}>
        Languages
      </PaneTitle>

      {entries.length === 0 ? (
        <p className="px-6 py-5 text-sm text-muted">
          The API did not answer, so nothing here has been probed. Start it with{' '}
          <code className="font-mono text-xs text-ink">purecoder serve</code>.
        </p>
      ) : null}

      <div className="px-6 py-6">
        {ready.length ? (
          <section>
            <h3 className="eyebrow mb-1">Runnable</h3>
            <p className="mb-3 max-w-2xl text-xs leading-relaxed text-faint">
              Toolchain probed on this machine and a harness wired for it. Code
              in these is compiled where the language needs it, executed, and
              made to prove a check actually ran.
            </p>
            <ul className="flex flex-wrap gap-2">
              {ready.map(([name]) => (
                <li
                  key={name}
                  className="inline-flex items-center gap-2 rounded border border-line bg-raised px-3 py-1.5"
                >
                  <Marker state="pass" size={8} />
                  <span className="font-mono text-xs text-ink">{name}</span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {refused.length ? (
          <section className="mt-9">
            <h3 className="eyebrow mb-1">Refused, and why</h3>
            <p className="mb-3 max-w-2xl text-xs leading-relaxed text-faint">
              In the registry's own words. Only a missing toolchain is something
              you can act on; the rest are statements about what could be
              validated here at all.
            </p>
            <div className="overflow-hidden rounded border border-line">
              {refused.map(([name, info], i) => (
                <div
                  key={name}
                  className={`flex flex-wrap items-baseline gap-x-4 gap-y-1 px-3.5 py-3 ${
                    i ? 'border-t border-line-soft' : ''
                  }`}
                >
                  <span className="inline-flex items-center gap-2">
                    <Marker state="idle" size={8} />
                    <span className="w-20 font-mono text-xs text-ink">{name}</span>
                  </span>
                  <span className="min-w-0 flex-1 text-xs leading-relaxed text-muted">
                    {info.reason}
                  </span>
                </div>
              ))}
            </div>
          </section>
        ) : null}

        <p className="mt-8 max-w-2xl text-xs leading-relaxed text-faint">
          Probed, never assumed: an entry is a claim about what could run here,
          not a promise that the machine has it.
        </p>
      </div>
    </div>
  )
}
