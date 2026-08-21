import { useEffect, useState } from 'react'
import { getGrammars, getStatus, type Grammar, type Status } from './api'
import { Header, Rail, type Section } from './components/Chrome'
import { RunsPane } from './panes/Runs'
import { LanguagesPane } from './panes/Languages'
import { GrammarsPane } from './panes/Grammars'

const SECTION_IDS: Section[] = ['runs', 'languages', 'grammars']

const sectionFromHash = (): Section => {
  const id = window.location.hash.slice(1) as Section
  return SECTION_IDS.includes(id) ? id : 'runs'
}

export default function App() {
  // In the URL so a reload keeps the pane and a link can name one. Runs are
  // deliberately not addressable: they live in this session only, so a link to
  // one would resolve to nothing the moment it was shared.
  const [section, setSection] = useState<Section>(sectionFromHash)
  const [status, setStatus] = useState<Status | null>(null)
  const [grammars, setGrammars] = useState<Grammar[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    const sync = () => setSection(sectionFromHash())
    window.addEventListener('hashchange', sync)
    return () => window.removeEventListener('hashchange', sync)
  }, [])

  const go = (s: Section) => {
    window.location.hash = s
    setSection(s)
  }

  // Read once at load. Neither of these changes while the page is open unless
  // someone restarts the model server, and polling for that would be motion
  // without information -- the next run reports it accurately either way.
  useEffect(() => {
    Promise.all([getStatus(), getGrammars()])
      .then(([s, g]) => {
        setStatus(s)
        setGrammars(g)
        setError('')
      })
      .catch(() =>
        setError('no API at 127.0.0.1:8100 — start it with `purecoder serve`'),
      )
  }, [])

  const runnable = Object.entries(status?.languages ?? {})
    .filter(([, l]) => l.available)
    .map(([name]) => name)

  return (
    <div className="flex h-full flex-col">
      <Header status={status} error={error} />
      <div className="flex min-h-0 flex-1">
        <Rail active={section} onSelect={go} />
        {section === 'runs' ? (
          <RunsPane languages={runnable.length ? runnable : ['python']} />
        ) : null}
        {section === 'languages' ? <LanguagesPane status={status} /> : null}
        {section === 'grammars' ? <GrammarsPane grammars={grammars} /> : null}
      </div>
    </div>
  )
}
