import { useEffect, useState } from 'react'
import './App.css'

const API_BASE = 'http://127.0.0.1:8767'
const FILES = [
  'claims.md',
  'procedures.md',
  'implementations.md',
  'goals.md',
  'run.md',
  'exploration.md',
] as const

type FileInfo = { content: string; exists: boolean }
type FilesResponse = { repo_path: string; files: Record<string, FileInfo> }
type LedgerEntry = {
  id: string
  file_path: string
  actor: string
  summary: string
  created_at: string
}

function App() {
  const [repoInput, setRepoInput] = useState('')
  const [repoPath, setRepoPath] = useState<string | null>(null)
  const [files, setFiles] = useState<Record<string, FileInfo>>({})
  const [current, setCurrent] = useState<string>(FILES[0])
  const [draft, setDraft] = useState('')
  const [summary, setSummary] = useState('')
  const [ledger, setLedger] = useState<LedgerEntry[]>([])
  const [status, setStatus] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const qp = new URLSearchParams(window.location.search).get('repo_path')
    if (qp) {
      setRepoInput(qp)
      void loadWorkspace(qp)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function loadWorkspace(path: string) {
    setError('')
    const res = await fetch(`${API_BASE}/api/files?repo_path=${encodeURIComponent(path)}`)
    if (!res.ok) {
      setError(await res.text())
      return
    }
    const data: FilesResponse = await res.json()
    setRepoPath(data.repo_path)
    setFiles(data.files)
    setCurrent(FILES[0])
    setDraft(data.files[FILES[0]]?.content ?? '')
    void loadLedger(data.repo_path)
  }

  async function loadLedger(path: string) {
    const res = await fetch(`${API_BASE}/api/ledger?repo_path=${encodeURIComponent(path)}`)
    if (res.ok) setLedger(await res.json())
  }

  function selectFile(file: string) {
    setCurrent(file)
    setDraft(files[file]?.content ?? '')
  }

  async function save() {
    if (!repoPath) return
    if (!summary.trim()) {
      setError('Summary is required -- a short, human-written note, like a commit message.')
      return
    }
    setError('')
    setSaving(true)
    try {
      const res = await fetch(`${API_BASE}/api/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_path: repoPath, file_path: current, content: draft, summary }),
      })
      if (!res.ok) {
        setError(await res.text())
        return
      }
      setFiles((prev) => ({ ...prev, [current]: { content: draft, exists: true } }))
      setStatus(`Saved + logged ${new Date().toLocaleTimeString()}`)
      setSummary('')
      void loadLedger(repoPath)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <h1>WORKSPACE</h1>
        <input
          value={repoInput}
          onChange={(e) => setRepoInput(e.target.value)}
          placeholder="C:\path\to\repo"
        />
        <button className="loadBtn" onClick={() => void loadWorkspace(repoInput.trim())}>
          Load
        </button>

        <h1>.stealth/ FILES</h1>
        <div className="tabs">
          {FILES.map((f) => {
            const info = files[f]
            return (
              <div
                key={f}
                className={`tab ${f === current ? 'active' : ''} ${!info?.exists ? 'missing' : ''}`}
                onClick={() => selectFile(f)}
              >
                {f}
                {!info?.exists ? '  (empty)' : ''}
              </div>
            )
          })}
        </div>

        <h1>RECENT EDITS</h1>
        <div className="ledger">
          {ledger.length === 0 ? (
            <div className="dim">(no edits logged yet)</div>
          ) : (
            ledger.map((e) => (
              <div key={e.id} className="ledgerEntry">
                <b>{e.file_path}</b> by {e.actor}
                <br />
                {e.summary}
                <br />
                <span className="dim">{e.created_at}</span>
              </div>
            ))
          )}
        </div>
      </aside>

      <main className="main">
        <div className="toolbar">
          <input
            className="summaryInput"
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            placeholder="Summary of this edit (required, like a commit message)"
          />
          <button onClick={() => void save()} disabled={saving || !repoPath}>
            {saving ? 'Saving…' : 'Save'}
          </button>
          {status && <span className="status">{status}</span>}
        </div>
        {error && <div className="error">{error}</div>}
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Load a workspace and pick a file on the left."
        />
      </main>
    </div>
  )
}

export default App
