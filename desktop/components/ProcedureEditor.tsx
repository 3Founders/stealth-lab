"use client"
// The Way's record as an editor: metadata, why it fits, and every step with its title and note, editable before the
// run. The same component is the right panel's Procedure section and the card's click-to-open sheet, so both always
// agree. Two actions: add a step, and use the Way (the per-step x removes one).
//
// A step may also name a model (the ... beside it opens a search over this machine's catalog). That is optional: with
// nothing chosen the step runs on the main agent's own model, which is the default (2026-11-17).
//
// The ask bar at the foot is this record's own prompt bar, built like the main composer (2026-11-18): a picture picked,
// dropped or pasted there goes to the edit call (app/api/procedure/edit), never to the chat's composer, and it carries
// its own model and its own list of attachments. Pictures make sense here because a Way is often written from a sketch,
// a screenshot of the screen the Way is about, or a photo of the form to fill in.
import { useRef, useState } from "react"
import type { DelegationStep, WayOption } from "@/lib/plan"
import { fileToDataUrl, imageDataUrl, isImage, MAX_ATTACHMENTS, MAX_ATTACH_BYTES, type BarAttachment } from "@/lib/attach"
import AttachStrip from "@/components/AttachStrip"
import ModelPicker from "@/components/ModelPicker"

export default function ProcedureEditor({ option, steps, catalog = [], onEdit, onAdd, onRemove, onUse, useTitle, onAsk, initialAsk = "", initialFiles = [] }: {
  option: WayOption
  steps: DelegationStep[]
  /** Every model this machine may run; the per-step picker searches it. Empty means no list yet. */
  catalog?: string[]
  onEdit: (i: number, patch: Partial<DelegationStep>) => void
  onAdd: () => void
  onRemove: (i: number) => void
  /** Absent while nothing is waiting to run: the Use button stays visible, disabled, so the editor keeps its two actions. */
  onUse?: () => void
  useTitle?: string
  /** Ask kell to rewrite the steps from a sentence, and from any pictures attached with it (the canvas's "Change it").
   *  `undefined` rather than a promise when a file was still being read: a string is then the sentence that says so. */
  onAsk?: (text: string, files?: BarAttachment[], model?: string) => Promise<string | void>
  /** What this record's bar starts with: the message and pictures the chat's composer was holding. Read once, at mount:
   *  after that the bar is the person's, and a resend does not put back what was already sent. */
  initialAsk?: string
  initialFiles?: BarAttachment[]
}) {
  const [pick, setPick] = useState<number | null>(null)
  const [q, setQ] = useState("")
  const [ask, setAsk] = useState(initialAsk)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState("")
  // A line the edit came back with: the pictures were sent, and whether the model could read them is not ours to claim.
  const [note, setNote] = useState("")
  // The ask bar's own files and model: they belong to this record, and to the edit call it makes.
  const [files, setFiles] = useState<BarAttachment[]>(initialFiles)
  const [model, setModel] = useState("")
  const [over, setOver] = useState(false)
  const fileIn = useRef<HTMLInputElement | null>(null)
  const choose = (i: number, model: string) => {
    onEdit(i, { model: model || undefined })
    setPick(null)
    setQ("")
  }
  const needle = q.trim().toLowerCase()
  const shown = catalog.filter((m) => m.toLowerCase().includes(needle)).slice(0, 12)

  // A picked or dropped file becomes a chip at once, with its picture read in the background (lib/attach.ts downscales it).
  const addFiles = async (list: File[]) => {
    const room = MAX_ATTACHMENTS - files.length
    const take = list.slice(0, Math.max(0, room))
    if (list.length > room) setErr(`A Way takes ${MAX_ATTACHMENTS} files at a time.`)
    const next: BarAttachment[] = []
    for (const f of take) {
      if (f.size > MAX_ATTACH_BYTES) { setErr(`${f.name} is larger than 25 MB`); continue }
      const mime = f.type || "application/octet-stream"
      const base = { id: crypto.randomUUID(), name: f.name, size: f.size, mime }
      next.push(base)
      try {
        const dataUrl = isImage(mime) ? await imageDataUrl(f, mime) : await fileToDataUrl(f)
        setFiles((a) => a.map((x) => (x.id === base.id ? { ...x, dataUrl } : x)))
      } catch {
        setErr(`Could not read ${f.name}`)
        setFiles((a) => a.filter((x) => x.id !== base.id))
      }
    }
    if (next.length) setFiles((a) => [...a, ...next].slice(0, MAX_ATTACHMENTS))
  }
  const removeFile = (id: string) => setFiles((a) => a.filter((x) => x.id !== id))

  return (
    <div className="dk-edit">
      <dl className="dk-proc-dl">
        <dt>Way</dt><dd>{option.id}</dd>
        {option.goal?.name && <><dt>Goal</dt><dd>{option.goal.name}{option.goal.id ? ` · ${option.goal.id}` : ""}</dd></>}
        <dt>Approval</dt><dd>{option.approval.replace(/_/g, " ")}</dd>
        {option.review && <><dt>Review</dt><dd>{option.review.replace(/_/g, " ")}</dd></>}
        {option.tool && <><dt>Tool</dt><dd>{option.tool.name}{option.tool.server ? ` · ${option.tool.server}` : ""}</dd></>}
        <dt>Steps</dt><dd>{steps.length}</dd>
      </dl>
      {option.why && <p className="dk-proc-why">{option.why}</p>}

      <h3>Steps <span>{steps.length}</span></h3>
      <ol className="dk-proc-steps">
        {steps.map((s, i) => (
          <li key={i} data-pick={pick === i || undefined}>
            <span className="n">{i + 1}</span>
            <span className="fields">
              <input value={s.title} aria-label={`Step ${i + 1} title`}
                onChange={(e) => onEdit(i, { title: e.target.value })} />
              <input value={s.note || ""} placeholder="A note for this step" aria-label={`Step ${i + 1} note`}
                onChange={(e) => onEdit(i, { note: e.target.value })} />
              <span className="k">
                {[s.kind, s.tool].filter(Boolean).join(" · ")}
                {s.model ? <> · <b className="dk-step-model">{s.model}</b></> : null}
              </span>
              {pick === i && (
                // The model catalog for this step, opened by the ... beside it. Nothing chosen means the main agent.
                <div className="dk-model-pick">
                  <input autoFocus value={q} placeholder="Search models…" aria-label={`Search models for step ${i + 1}`}
                    onChange={(e) => setQ(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); setPick(null); setQ("") } }} />
                  <ul role="listbox" aria-label={`Models for step ${i + 1}`}>
                    <li>
                      <button type="button" data-on={!s.model || undefined} onClick={() => choose(i, "")}>
                        Default: the main agent
                      </button>
                    </li>
                    {shown.map((m) => (
                      <li key={m}>
                        <button type="button" data-on={s.model === m || undefined} onClick={() => choose(i, m)}>{m}</button>
                      </li>
                    ))}
                  </ul>
                  {!catalog.length && <p className="dk-note">No model list on this machine yet.</p>}
                  {catalog.length > 0 && !shown.length && <p className="dk-note">No model matches that.</p>}
                </div>
              )}
            </span>
            <button type="button" className="dk-step-opts" aria-label={`Model for step ${i + 1}`} aria-expanded={pick === i}
              title={s.model ? `Model: ${s.model}` : "Choose a model for this step (default: the main agent)"}
              onClick={() => { setPick(pick === i ? null : i); setQ("") }}>⋯</button>
            <button type="button" className="dk-step-del" aria-label={`Remove step ${i + 1}`} title="Remove this step"
              onClick={() => onRemove(i)}>×</button>
          </li>
        ))}
      </ol>

      <div className="dk-edit-actions">
        <button type="button" className="btn-line dg-mini" onClick={onAdd}>+ Add step</button>
        <button type="button" className="dk-proc-use" disabled={!onUse}
          title={onUse ? (useTitle || "Run this Way with the steps as edited") : "Pick a task first: the Way runs with it"}
          onClick={onUse}>
          Use this Way<span className="sq" aria-hidden="true">→</span>
        </button>
      </div>

      {onAsk && (
        // Like the canvas's ask, and built like the main composer: a sentence, pictures if they help, a model, and Enter
        // sends. kell rewrites the steps (title and note); the graph follows. Pictures never reach the chat.
        <form className="dk-proc-ask" data-over={over || undefined}
          onDragEnter={(e) => { if (Array.from(e.dataTransfer.types).includes("Files")) { e.preventDefault(); setOver(true) } }}
          onDragOver={(e) => { if (Array.from(e.dataTransfer.types).includes("Files")) { e.preventDefault(); e.dataTransfer.dropEffect = "copy" } }}
          onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOver(false) }}
          onDrop={(e) => { e.preventDefault(); setOver(false); void addFiles(Array.from(e.dataTransfer.files || [])) }}
          onSubmit={async (e) => {
            e.preventDefault()
            const text = ask.trim()
            if ((!text && !files.length) || busy) return
            // A picture still being read is not a picture: say so instead of asking about one the model never got.
            if (!files.every((f) => f.dataUrl)) { setErr("The pictures are still being read; one more moment."); return }
            setBusy(true); setErr(""); setNote("")
            try {
              const said = await onAsk(text, files, model || undefined)
              setAsk(""); setFiles([]); setNote(typeof said === "string" ? said : "")
            }
            catch (ex) { setErr(String((ex as Error)?.message || ex)) }
            finally { setBusy(false) }
          }}>
          <input ref={fileIn} type="file" multiple hidden
            onChange={(e) => { const fs = Array.from(e.currentTarget.files || []); e.currentTarget.value = ""; void addFiles(fs) }} />
          <AttachStrip files={files} onRemove={removeFile} label="Files for this Way's edit" />
          <div className="dk-proc-ask-row">
            <button type="button" className="dk-proc-ask-add" aria-label="Add files to this Way's edit" title="Add files or pictures"
              disabled={busy} onClick={() => fileIn.current?.click()}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
                strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="m21.4 11.6-8.9 8.9a5 5 0 0 1-7.1-7.1l8.9-8.9a3.5 3.5 0 1 1 5 5l-8.9 8.9a2 2 0 1 1-2.8-2.8l8.2-8.2" />
                <path d="M19 15v6M16 18h6" />
              </svg>
            </button>
            <input className="dk-proc-ask-text" value={ask} onChange={(e) => setAsk(e.target.value)}
              placeholder={files.length ? "What should change about this Way? (Enter to send)" : "Ask kell to change these steps…"}
              aria-label="Ask kell to change these steps"
              onPaste={(e) => { const fs = Array.from(e.clipboardData.files || []); if (fs.length) { e.preventDefault(); void addFiles(fs) } }}
              onKeyDown={(e) => {
                // A picture alone is a complete ask ("make the steps from this"); Escape takes the files back out.
                if (e.key === "Enter" && !e.shiftKey) { if (e.nativeEvent.isComposing) return; e.preventDefault(); e.currentTarget.form?.requestSubmit(); return }
                if (e.key === "Escape" && files.length) { e.preventDefault(); setFiles([]) }
              }}
              disabled={busy} />
            <ModelPicker value={model} catalog={catalog} onChange={setModel} disabled={busy} align="end"
              label={`Model for this Way's edit`} />
            <button type="submit" className="btn-ink dg-mini" disabled={(!ask.trim() && !files.length) || busy}>
              {busy ? "Editing…" : "Change it"}
            </button>
          </div>
          {files.length > 0 && !files.every((f) => f.dataUrl) && (
            <p className="dk-note">Reading {files.every((f) => isImage(f.mime)) ? "the pictures" : "the files"}…</p>
          )}
        </form>
      )}
      {note && !err && <p className="dk-note" role="status">{note}</p>}
      {err && <p className="dk-err" role="alert">{err}</p>}
    </div>
  )
}
