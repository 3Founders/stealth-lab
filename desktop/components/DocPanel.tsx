"use client"
// Documents beside the chat (docs/desktop_requirements_2026-11.md, P0 #2, P1 #5): a card for each file kell made in a turn, and a
// preview pane with Open, Show in folder, Save a copy, the file's earlier versions (each can be previewed and put back) and a box to
// ask for a change, which goes to the chat as a normal prompt so the change is made by remaking the file (its old version is kept).
import { useEffect, useState } from "react"

export type Doc = { path: string; name: string; kind: string; at: number; size: number }
type Bridge = { openPath?: (p: string) => Promise<string>; showInFolder?: (p: string) => void; saveCopy?: (p: string) => Promise<string | null> }
const bridge = (): Bridge => (typeof window !== "undefined" ? ((window as any).kellDesktop || {}) : {})

const KIND: Record<string, string> = { docx: "Word", xlsx: "Excel", pptx: "PowerPoint", pdf: "PDF", csv: "CSV", md: "Markdown", html: "Web page", htm: "Web page" }
const size = (n: number) => (n < 1024 ? `${n} B` : n < 1048576 ? `${Math.round(n / 1024)} KB` : `${(n / 1048576).toFixed(1)} MB`)
const when = (t: number) => new Date(t).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })

export function DocCards({ docs, onOpen }: { docs: Doc[]; onOpen: (d: Doc) => void }) {
  if (!docs.length) return null
  return (
    <div className="dk-docs" aria-label="Files kell made">
      {docs.map((d) => (
        <button key={d.path + d.at} type="button" className="dk-doc" onClick={() => onOpen(d)} title={d.path}>
          <span className="dk-doc-ico" data-kind={d.kind} aria-hidden="true">{(d.kind || "?").slice(0, 4).toUpperCase()}</span>
          <span className="dk-doc-txt"><b>{d.name}</b><span>{KIND[d.kind] || d.kind} · {size(d.size)}</span></span>
          <span className="dk-doc-go">Preview</span>
        </button>
      ))}
    </div>
  )
}

export function DocPreview({ doc, onClose, onAskChange }: { doc: Doc; onClose: () => void; onAskChange: (text: string) => void }) {
  const [versions, setVersions] = useState<{ id: string; at: number; size: number }[]>([])
  const [ver, setVer] = useState<string>("")
  const [stamp, setStamp] = useState(Date.now())
  const [ask, setAsk] = useState("")
  const [note, setNote] = useState("")
  const load = () => fetch(`/api/docs/versions?path=${encodeURIComponent(doc.path)}`).then((r) => r.json()).then((j) => setVersions(j.versions || [])).catch(() => setVersions([]))
  useEffect(() => { setVer(""); setNote(""); void load() }, [doc.path, doc.at])   // eslint-disable-line react-hooks/exhaustive-deps
  const src = `/api/docs/preview?path=${encodeURIComponent(doc.path)}${ver ? `&v=${encodeURIComponent(ver)}` : ""}&t=${stamp}`
  const restore = async () => {
    const r = await fetch("/api/docs/versions", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ path: doc.path, id: ver }) })
    if (r.ok) { setNote("Restored. The version it replaced is in the list too."); setVer(""); setStamp(Date.now()); void load() }
    else setNote("Couldn't restore that version.")
  }
  const save = async () => {
    const b = bridge()
    if (!b.saveCopy) { setNote("Saving a copy works in the desktop app."); return }
    const to = await b.saveCopy(doc.path)
    if (to) setNote(`Saved a copy to ${to}`)
  }
  return (
    <aside className="dk-docpane" aria-label={`Preview of ${doc.name}`}>
      <header>
        <b title={doc.path}>{doc.name}</b>
        <span className="sp" />
        <button type="button" className="btn-line dg-mini" onClick={() => void bridge().openPath?.(doc.path)}>Open</button>
        <button type="button" className="btn-line dg-mini" onClick={() => bridge().showInFolder?.(doc.path)}>Show in folder</button>
        <button type="button" className="btn-line dg-mini" onClick={() => void save()}>Save a copy…</button>
        <button type="button" className="dk-icon dk-icon-sm" aria-label="Close the preview" onClick={onClose}>×</button>
      </header>
      {versions.length > 0 && (
        <div className="dk-docver">
          <label>Version{" "}
            <select value={ver} onChange={(e) => { setVer(e.target.value); setNote("") }} aria-label="Version to preview">
              <option value="">Current</option>
              {versions.map((v) => <option key={v.id} value={v.id}>{when(v.at)} · {size(v.size)}</option>)}
            </select>
          </label>
          {ver && <button type="button" className="btn-ink dg-mini" onClick={() => void restore()}>Restore this version</button>}
        </div>
      )}
      <iframe key={src} src={src} title={`Preview of ${doc.name}`} sandbox={doc.kind === "pdf" ? undefined : ""} />
      {note && <p className="dk-note" role="status" style={{ margin: "6px 12px" }}>{note}</p>}
      <form className="dk-docask" onSubmit={(e) => { e.preventDefault(); const t = ask.trim(); if (!t) return; onAskChange(t); setAsk("") }}>
        <input value={ask} onChange={(e) => setAsk(e.target.value)} placeholder={`Ask for a change to ${doc.name}…`} aria-label="Ask for a change" />
        <button type="submit" className="btn-ink dg-mini" disabled={!ask.trim()}>Change it</button>
      </form>
    </aside>
  )
}
