/**
 * The `purecoder serve` surface, typed.
 *
 * Every shape here mirrors what the server actually returns; nothing is
 * invented for the UI's convenience. Where the pipeline says `ok: false` with
 * a reason, that is the answer, not an error -- a refusal is the pipeline
 * working, and the transport must not dress it up as a failure.
 */

const API = '/api'

export interface Verdict {
  ok: boolean
  error: string
  code: string
  tests: string
  contract: Contract | null
  attempts: number
  agents: Ledger
}

/** What each role spent, recorded by the loop as the run happened. */
export interface Role {
  name: string
  /** One line on what the role is for; comes from the server, not from here. */
  role: string
  /** The attempt cap this run actually had, not a declared constant. */
  cap: number
  attempts: number
  accepted: boolean
  reason: string
}

export interface Ledger {
  /**
   * Where the run ran out — NOT whose fault it was. The two differ: when the
   * tester writes a suite that cannot pass, the writer spends the attempts and
   * is not at fault. Show the whole roster, never this field alone.
   */
  stopped_on: string
  roles: Role[]
}

export interface Contract {
  name: string
  summary?: string
  params?: { name: string; type?: string }[]
  returns?: string
  raises?: string[]
  examples?: { in?: string; out?: string; exc?: string }[]
}

/** One line of a run's narration, as the loop produced it. */
export interface RunEvent {
  kind: 'tests' | 'attempt' | 'contract' | 'docs' | 'verdict'
  attempt: number
  text: string
  /** The role that produced the line. Empty for the loop's own lines. */
  agent: string
}

export interface LanguageInfo {
  available: boolean
  /** Why not, in the registry's own words. Empty when available. */
  reason: string
}

export interface Status {
  server: { up: boolean; url: string; model: string }
  gpu: string
  grammars: string[]
  modules: Record<string, true | string>
  languages: Record<string, LanguageInfo>
}

export interface Grammar {
  name: string
  /** The root rule, lifted out: an unbounded one truncates every attempt. */
  root: string
  text: string
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(API + path)
  if (!res.ok) throw new Error(`${path} answered ${res.status}`)
  return (await res.json()) as T
}

export const getStatus = () => getJSON<Status>('/status')
export const getGrammars = () =>
  getJSON<{ grammars: Grammar[] }>('/grammars').then((d) => d.grammars)

export interface RunRequest {
  spec: string
  lang: string
  contract: boolean
  retries: number
  no_docs: boolean
}

/**
 * Drive one generation, reading its transcript as it happens.
 *
 * Server-sent events over POST, so `EventSource` is out -- it only does GET.
 * The last event carries the same envelope the blocking `/code` returns, which
 * is why `onEvent` and the resolved value can be consumed independently: a
 * caller that ignores the narration still gets the verdict.
 *
 * `signal` aborts the read. The server notices the closed socket and stops.
 */
export async function streamRun(
  req: RunRequest,
  onEvent: (event: RunEvent) => void,
  signal?: AbortSignal,
): Promise<Verdict> {
  const res = await fetch(`${API}/code/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
    signal,
  })
  if (!res.ok || !res.body) {
    const detail = await res.text().catch(() => '')
    throw new Error(detail || `the server answered ${res.status}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let verdict: Verdict | null = null

  // Events are separated by a blank line and can be split across chunks, so
  // the buffer is drained by delimiter rather than per read().
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let split: number
    while ((split = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, split)
      buffer = buffer.slice(split + 2)
      if (!frame.startsWith('data: ')) continue
      const record = JSON.parse(frame.slice(6))
      if (record.kind === 'result') verdict = record.result as Verdict
      else onEvent(record as RunEvent)
    }
  }

  if (!verdict) {
    throw new Error('the stream ended before the run reported a verdict')
  }
  return verdict
}
