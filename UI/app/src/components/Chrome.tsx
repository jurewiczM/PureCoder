import type { Status } from '../api'
import { Marker } from './Verdict'

export type Section = 'runs' | 'languages' | 'grammars'

export const SECTIONS: { id: Section; label: string; hint: string }[] = [
  { id: 'runs', label: 'Runs', hint: 'generate and read the transcript' },
  { id: 'languages', label: 'Languages', hint: 'what can be executed here' },
  { id: 'grammars', label: 'Grammars', hint: 'the shapes output is held to' },
]

/**
 * The header strip: what the pipeline is talking to, and whether it is up.
 *
 * Context for everything below it rather than somewhere to navigate, which is
 * why it is a strip and not a tab bar. With the model server down every run
 * fails for one reason, and saying so once at the top beats reporting it as a
 * fresh surprise on each attempt.
 */
export function Header({ status, error }: { status: Status | null; error: string }) {
  const up = status?.server.up ?? false
  const model = status?.server.model?.replace(/\.gguf$/, '') ?? ''

  return (
    <header className="flex shrink-0 items-center justify-between border-b border-line bg-base px-6 py-3.5">
      <div className="flex items-baseline gap-3">
        <span className="text-[15px] font-semibold tracking-[0.2em] text-ink">
          PURECODER
        </span>
        <span className="hidden text-xs text-faint sm:inline">
          nothing is emitted that was not executed
        </span>
      </div>

      <div className="flex items-center gap-4 text-xs">
        {error ? (
          <span className="text-fail">{error}</span>
        ) : (
          <>
            {model ? (
              <span className="hidden font-mono text-muted md:inline">{model}</span>
            ) : null}
            <span className="inline-flex items-center gap-2 text-muted">
              <Marker state={up ? 'pass' : 'fail'} size={8} />
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
    <nav className="flex w-52 shrink-0 flex-col gap-0.5 border-r border-line bg-base p-3">
      {SECTIONS.map((s) => {
        const on = s.id === active
        return (
          <button
            key={s.id}
            onClick={() => onSelect(s.id)}
            aria-current={on ? 'page' : undefined}
            className={`group relative rounded px-3 py-2 text-left transition-colors ${
              on ? 'bg-sheet' : 'hover:bg-line-soft'
            }`}
          >
            {/* The active mark is a bar on the edge rather than a wash of
             * colour: it locates the section without spending chroma, which
             * on this page belongs to verdicts. */}
            <span
              aria-hidden
              className={`absolute top-2 bottom-2 left-0 w-0.5 rounded-full ${
                on ? 'bg-muted' : 'bg-transparent'
              }`}
            />
            <span
              className={`block text-[13px] font-medium ${
                on ? 'text-ink' : 'text-muted group-hover:text-ink'
              }`}
            >
              {s.label}
            </span>
            <span className="block text-[11px] leading-snug text-faint">{s.hint}</span>
          </button>
        )
      })}
    </nav>
  )
}

/** A heading for a pane: what you are looking at, and how much of it. */
export function PaneTitle({
  children,
  aside,
}: {
  children: React.ReactNode
  aside?: React.ReactNode
}) {
  return (
    <div className="flex shrink-0 items-baseline justify-between border-b border-line px-6 py-4">
      <h2 className="text-[15px] font-semibold text-ink">{children}</h2>
      {aside ? <span className="font-mono text-xs text-faint">{aside}</span> : null}
    </div>
  )
}
