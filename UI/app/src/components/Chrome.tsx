import type { Status } from '../api'
import { Marker } from './Verdict'

export type Section = 'runs' | 'languages' | 'grammars'

export const SECTIONS: { id: Section; label: string }[] = [
  { id: 'runs', label: 'Runs' },
  { id: 'languages', label: 'Languages' },
  { id: 'grammars', label: 'Grammars' },
]

/**
 * The header strip: what the pipeline is talking to, and whether it is up.
 *
 * This is context for everything below it rather than somewhere to navigate,
 * which is why it is a strip and not a tab. With the model server down every
 * run will fail for one reason, and saying so once at the top beats reporting
 * it as a fresh surprise on each attempt.
 */
export function Header({ status, error }: { status: Status | null; error: string }) {
  const up = status?.server.up ?? false
  const model = status?.server.model?.replace(/\.gguf$/, '') ?? ''

  return (
    <header className="flex shrink-0 items-center justify-between border-b border-line px-5 py-3">
      <span className="text-sm tracking-[0.35em] text-ink">PURECODER</span>
      <div className="flex items-center gap-3 text-xs text-faint">
        {error ? (
          <span className="text-fail">{error}</span>
        ) : (
          <>
            {model ? <span className="text-muted">{model}</span> : null}
            <span className="inline-flex items-center gap-2">
              <Marker state={up ? 'pass' : 'fail'} />
              {up ? 'model server up' : 'model server down'}
            </span>
          </>
        )}
      </div>
    </header>
  )
}

export function Rail({
  active,
  onSelect,
}: {
  active: Section
  onSelect: (s: Section) => void
}) {
  return (
    <nav className="flex w-36 shrink-0 flex-col border-r border-line py-2">
      {SECTIONS.map((s) => {
        const on = s.id === active
        return (
          <button
            key={s.id}
            onClick={() => onSelect(s.id)}
            aria-current={on ? 'page' : undefined}
            className={`px-5 py-2 text-left text-xs tracking-wide transition-colors ${
              on
                ? 'bg-surface text-ink'
                : 'text-faint hover:bg-line-soft hover:text-muted'
            }`}
          >
            {s.label}
          </button>
        )
      })}
    </nav>
  )
}

/** A heading for a pane. Small, quiet, and not in a display face. */
export function PaneTitle({
  children,
  aside,
}: {
  children: React.ReactNode
  aside?: React.ReactNode
}) {
  return (
    <div className="flex items-baseline justify-between border-b border-line px-5 py-3">
      <h2 className="text-xs tracking-wide text-muted">{children}</h2>
      {aside ? <span className="text-xs text-faint">{aside}</span> : null}
    </div>
  )
}
