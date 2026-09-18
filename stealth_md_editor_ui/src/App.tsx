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
type SyncCandidate = {
  candidate_id: string
  object_type: 'claim' | 'procedure' | 'goal'
  classification: 'NEW' | 'CHANGED' | 'ALREADY_SYNCED' | 'LOCAL_ONLY' | 'CONFLICTING'
  local_summary: string
  backend_id: string | null
  scope: string
  status: string
  reason: string
}
type CommitResult = {
  candidate_id: string
  object_type: string
  outcome: 'committed' | 'skipped' | 'refused'
  reason?: string
  id?: string
}

const CLASSIFICATION_COLOR: Record<string, string> = {
  NEW: '#3a8f3a',
  CHANGED: '#c9a227',
  ALREADY_SYNCED: '#555',
  LOCAL_ONLY: '#666',
  CONFLICTING: '#b33',
}

function App() {
  const [view, setView] = useState<'editor' | 'versioning'>('editor')
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

  const [candidates, setCandidates] = useState<SyncCandidate[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [previewing, setPreviewing] = useState(false)
  const [committing, setCommitting] = useState(false)
  const [commitResults, setCommitResults] = useState<CommitResult[]>([])

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

  async function previewSync() {
    if (!repoPath) return
    setError('')
    setPreviewing(true)
    setCommitResults([])
    try {
      const res = await fetch(`${API_BASE}/api/sync/preview?repo_path=${encodeURIComponent(repoPath)}`)
      if (!res.ok) {
        setError(await res.text())
        return
      }
      const data: SyncCandidate[] = await res.json()
      setCandidates(data)
      setSelected(new Set())
    } finally {
      setPreviewing(false)
    }
  }

  function toggleSelected(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function commitSelected() {
    if (!repoPath || selected.size === 0) return
    setError('')
    setCommitting(true)
    try {
      const res = await fetch(`${API_BASE}/api/sync/commit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_path: repoPath, selected_ids: Array.from(selected) }),
      })
      if (!res.ok) {
        setError(await res.text())
        return
      }
      const results: CommitResult[] = await res.json()
      setCommitResults(results)
      void previewSync()
    } finally {
      setCommitting(false)
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

        <div className="viewSwitch">
          <button
            className={view === 'editor' ? 'active' : ''}
            onClick={() => setView('editor')}
          >
            Editor
          </button>
          <button
            className={view === 'versioning' ? 'active' : ''}
            onClick={() => setView('versioning')}
          >
            Versioning
          </button>
        </div>

        {view === 'editor' && (
          <>
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
          </>
        )}

        {view === 'versioning' && (
          <div className="versionHelp">
            Compares each Claim/Procedure/Goal's local <code>version=</code> line against
            its CURRENT canonical Postgres row. This is <code>app.stealth.local_sync</code>'s
            real preview/commit pair -- not a new system.
          </div>
        )}
      </aside>

      {view === 'editor' && (
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
      )}

      {view === 'versioning' && (
        <main className="main">
          <div className="toolbar">
            <button onClick={() => void previewSync()} disabled={previewing || !repoPath}>
              {previewing ? 'Checking…' : 'Preview Sync'}
            </button>
            <button
              onClick={() => void commitSelected()}
              disabled={committing || selected.size === 0}
            >
              {committing ? 'Committing…' : `Commit Selected (${selected.size})`}
            </button>
          </div>
          {error && <div className="error">{error}</div>}

          {commitResults.length > 0 && (
            <div className="commitResults">
              {commitResults.map((r) => (
                <div key={r.candidate_id} className={`commitRow ${r.outcome}`}>
                  <b>{r.outcome.toUpperCase()}</b> {r.object_type} — {r.reason || (r.id ? `new id ${r.id}` : '')}
                </div>
              ))}
            </div>
          )}

          <div className="candidateList">
            {candidates.length === 0 ? (
              <div className="dim">
                {previewing ? 'Checking…' : 'Click "Preview Sync" to compare local .stealth/ files against Postgres.'}
              </div>
            ) : (
              candidates.map((c) => (
                <div key={c.candidate_id} className="candidateRow">
                  <input
                    type="checkbox"
                    checked={selected.has(c.candidate_id)}
                    disabled={c.classification === 'ALREADY_SYNCED'}
                    onChange={() => toggleSelected(c.candidate_id)}
                  />
                  <span
                    className="badge"
                    style={{ background: CLASSIFICATION_COLOR[c.classification] ?? '#444' }}
                  >
                    {c.classification}
                  </span>
                  <span className="objType">{c.object_type}</span>
                  <span className="candSummary">{c.local_summary}</span>
                  {c.reason && <span className="candReason">{c.reason}</span>}
                </div>
              ))
            )}
          </div>
        </main>
      )}
    </div>
  )
}

export default App
