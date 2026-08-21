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
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto bg-sheet">
      <PaneTitle aside={`${grammars.length} loaded`}>Grammars</PaneTitle>

      {grammars.length === 0 ? (
        <p className="px-6 py-5 text-sm text-muted">
          The API did not answer, so no grammar has been read. Start it with{' '}
          <code className="font-mono text-xs text-ink">purecoder serve</code>.
        </p>
      ) : null}

      <div className="px-6 py-6">
        <p className="mb-4 max-w-2xl text-xs leading-relaxed text-faint">
          A root that cannot terminate truncates every attempt: the sampler
          never reaches a stop, so generation runs to the token limit and dies
          there. Two grammars shipped that way, which is why the root rule is
          the first thing shown.
        </p>

        <div className="flex flex-col gap-3">
          {grammars.map((g) => {
            const ok = bounded(g.root)
            const showing = open === g.name
            return (
              <article
                key={g.name}
                className="overflow-hidden rounded border border-line"
              >
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line-soft px-3.5 py-2.5">
                  <span className="inline-flex items-center gap-2">
                    <Marker state={ok ? 'pass' : 'fail'} size={8} />
                    <span className="font-mono text-xs text-ink">{g.name}.gbnf</span>
                  </span>
                  <span
                    className={`text-xs ${ok ? 'text-muted' : 'text-fail'}`}
                  >
                    {ok ? 'root terminates' : 'unbounded root — this will truncate'}
                  </span>
                  <button
                    onClick={() => setOpen(showing ? null : g.name)}
                    className="ml-auto rounded border border-line px-2.5 py-1 text-[11px] text-muted transition-colors hover:border-faint hover:text-ink"
                  >
                    {showing ? 'Hide source' : 'Show source'}
                  </button>
                </div>

                {/* Wrapped, not clipped. contract.gbnf's root is one long
                 * literal shape and the old single-line row cut it off at the
                 * viewport edge with nothing to say it had -- the rule this
                 * pane exists to show was the one thing it hid. */}
                <pre className="bg-raised px-4 py-3 font-mono text-xs leading-6 wrap-anywhere whitespace-pre-wrap text-muted">
                  {g.root}
                </pre>

                {showing ? (
                  <pre className="overflow-x-auto border-t border-line px-4 py-3 font-mono text-xs leading-6 text-faint">
                    {g.text.trim()}
                  </pre>
                ) : null}
              </article>
            )
          })}
        </div>
      </div>
    </div>
  )
}
