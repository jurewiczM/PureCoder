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
 */
export function LanguagesPane({ status }: { status: Status | null }) {
  const entries = Object.entries(status?.languages ?? {})
  const ready = entries.filter(([, l]) => l.available)

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <PaneTitle aside={`${ready.length} of ${entries.length} runnable here`}>
        Languages
      </PaneTitle>

      {entries.length === 0 ? (
        <p className="px-5 py-4 text-xs text-faint">
          The API did not answer, so nothing here has been probed.
        </p>
      ) : null}

      <div className="px-5 py-2">
        {entries.map(([name, info]) => (
          <div
            key={name}
            className="flex items-baseline gap-3 border-b border-line-soft py-3 last:border-0"
          >
            <Marker state={info.available ? 'pass' : 'idle'} className="translate-y-0.5" />
            <span className="w-28 shrink-0 text-xs text-ink">{name}</span>
            <span className="flex-1 text-xs leading-relaxed text-faint">
              {info.available ? 'toolchain probed, harness wired' : info.reason}
            </span>
          </div>
        ))}
      </div>

      <p className="px-5 pt-2 pb-5 text-[11px] leading-relaxed text-faint">
        Probed, never assumed: an entry is a claim about what could run here,
        not a promise that the machine has it.
      </p>
    </div>
  )
}
