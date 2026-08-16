import { useState } from 'react'
import type { Grammar } from '../api'
import { Marker } from '../components/Verdict'
import { PaneTitle } from '../components/Chrome'

/**
 * A root rule that cannot terminate is a grammar that always truncates: the
 * sampler never reaches a stop, so the model keeps writing until it hits
 * n_predict and every attempt dies the same way. That has shipped twice --
 * `env.gbnf` and then `makefile.gbnf` -- which is why the root is the line
 * this pane puts first.
 *
 * The check is textual and deliberately shallow. It reports whether the root
 * carries a repetition bound, not whether the bound is small enough to fit
 * inside n_predict, which is the part only a live run can answer.
 */
function bounded(root: string): boolean {
  if (/\{\d+,\d+\}/.test(root)) return true
  // No repetition operator at all means the root is a fixed sequence, which
  // terminates by construction -- contract.gbnf is one long literal shape.
  return !/[*+]/.test(root)
}

export function GrammarsPane({ grammars }: { grammars: Grammar[] }) {
  const [open, setOpen] = useState<string | null>(null)

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <PaneTitle aside={`${grammars.length} loaded`}>Grammars</PaneTitle>

      {grammars.length === 0 ? (
        <p className="px-5 py-4 text-xs text-faint">
          The API did not answer, so no grammar has been read.
        </p>
      ) : null}

      <div className="min-w-0 px-5 py-2 pr-8">
        {grammars.map((g) => {
          const ok = bounded(g.root)
          const showing = open === g.name
          return (
            <div key={g.name} className="border-b border-line-soft py-3 last:border-0">
              <div className="flex min-w-0 items-baseline gap-3">
                <Marker state={ok ? 'pass' : 'fail'} className="translate-y-0.5" />
                <span className="w-28 shrink-0 text-xs text-ink">{g.name}.gbnf</span>
                <span className="min-w-0 flex-1 truncate text-xs text-faint">
                  {ok ? 'root terminates' : 'unbounded root — this will truncate'}
                </span>
                <button
                  onClick={() => setOpen(showing ? null : g.name)}
                  className="shrink-0 text-xs text-faint transition-colors hover:text-muted"
                >
                  {showing ? 'hide' : 'source'}
                </button>
              </div>
              <pre className="mt-2 ml-[3.4rem] overflow-x-auto text-xs text-muted">
                {g.root}
              </pre>
              {showing ? (
                <pre className="mt-3 ml-[3.4rem] overflow-x-auto border-l border-line pl-3 text-xs leading-6 text-faint">
                  {g.text.trim()}
                </pre>
              ) : null}
            </div>
          )
        })}
      </div>
    </div>
  )
}
