"use client"
// Submit a Way, inside the app (2026-11-18). The button in the Procedure panel used to switch to the Rooms tab and load
// the website's chat with ?panel=submit, which the site does not read: in the app it looked like nothing happened. This
// panel is the app's own, over the chat, and it speaks to the same backend tool the website does (submit_goal, through
// app/api/submit).
//
// A Way is a goal and the steps that achieve it. Each step is done by a person (a description) or by kell (a prompt, a
// locator, or both: a website, a file, a script or a tool's name). The person never chooses a mode: the backend works it
// out from the kinds, and the panel says which it will be. Nothing is stored until "Submit": "Check it" previews, and
// the submit itself is reviewed step by step, so the panel shows each verdict with its reason rather than a spinner.
//
// The same sheet is also where a new Way is written from the Procedure panel: a goal is suggested from the Way on screen
// (never copied), and the steps are prefilled only when the person asks for them (the row's "Start from this Way"). kell
// is not a stored Way here until it is submitted: the panel never presents a draft as something that can be run.
import { useEffect, useMemo, useRef, useState } from "react"

type Kind = "human" | "automated"
type Step = { id: string; kind: Kind; title: string; description: string; prompt: string; locator: string }
type StepOut = { order?: number; kind?: string; title?: string; description?: string; prompt?: string; locator?: unknown; status?: string; reason?: string }
type Submission = { submission_id?: string; id?: string; goal_name?: string; goal?: string; mode?: string; mode_label?: string; status?: string; steps?: StepOut[]; message?: string }
type Preview = { goal?: string; mode?: string; mode_label?: string; message?: string }

const newStep = (kind: Kind, title = ""): Step => ({ id: crypto.randomUUID(), kind, title, description: "", prompt: "", locator: "" })

/** A locator as the backend stored it ({type, value}) or as it was typed: one line either way. */
const locatorText = (l: unknown) =>
  typeof l === "string" ? l : l && typeof l === "object" ? String((l as any).value || "") : ""

const modeOf = (steps: Step[]): string => {
  const kinds = new Set(steps.map((s) => s.kind))
  return kinds.size === 1 ? (kinds.has("human") ? "Fully Human" : "Fully AI") : "Human+AI"
}

export default function SubmitWay({ onClose, onSubmitted, from }: {
  onClose: () => void
  /** A stored submission: the panel offers to show its Way in the chat. */
  onSubmitted?: (submissionId: string) => void
  /** The Way on screen when the sheet was opened to write a new one: its name becomes the goal's first suggestion and,
   *  on request, its steps seed the form. Nothing is copied by itself: a Way is written, never duplicated. */
  from?: { name?: string; steps?: Array<{ title?: string; kind?: string; note?: string }> } | null
}) {
  const [goal, setGoal] = useState("")
  const [description, setDescription] = useState("")
  const [steps, setSteps] = useState<Step[]>([newStep("human"), newStep("automated")])
  const [busy, setBusy] = useState<"" | "check" | "submit">("")
  const [err, setErr] = useState("")
  const [preview, setPreview] = useState<Preview | null>(null)
  const [sub, setSub] = useState<Submission | null>(null)
  const box = useRef<HTMLDivElement | null>(null)
  const first = useRef<HTMLInputElement | null>(null)

  // The app's overlay manners: focus goes in, Escape closes, and the sheet takes its own clicks so the chat behind stays
  // as it is. Escape does not abandon a submit that is already on its way.
  useEffect(() => { first.current?.focus() }, [])
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === "Escape" && !busy) onClose() }
    document.addEventListener("keydown", key)
    return () => document.removeEventListener("keydown", key)
  }, [busy, onClose])

  const sent = useMemo(() => steps.map((s) => ({
    kind: s.kind, title: s.title.trim(),
    ...(s.kind === "human"
      ? { description: s.description.trim() }
      : { prompt: s.prompt.trim(), ...(s.locator.trim() ? { locator: s.locator.trim() } : {}) }),
  })), [steps])
  // A step is ready when the part its doer needs is there: a person needs a description, kell a prompt or a locator.
  const ready = !!goal.trim() && steps.every((s) => s.kind === "human" ? !!s.description.trim() : !!(s.prompt.trim() || s.locator.trim()))

  const edit = (id: string, patch: Partial<Step>) => setSteps((all) => all.map((s) => (s.id === id ? { ...s, ...patch } : s)))
  const move = (i: number, by: number) => setSteps((all) => {
    const j = i + by
    if (j < 0 || j >= all.length) return all
    const next = all.slice()
    const [t] = next.splice(i, 1)
    next.splice(j, 0, t)
    return next
  })
  const remove = (id: string) => setSteps((all) => all.filter((s) => s.id !== id))
  // The Way on screen, if the sheet was opened to write a new one from it.
  const seedSteps = from?.steps || null

  // Written from the Way on screen: its steps can seed this form, on request and once. A person's step keeps the words;
  // kell's step keeps its prompt (its note) and, if it had one, its model, so the new Way starts from where the old one is
  // rather than from blank fields. Nothing is copied until the button is pressed.
  const seed = () => {
    const from = seedSteps || []
    if (!from.length) return
    setSteps(from.slice(0, 40).map((s) => {
      const kind: Kind = s.kind === "human" || s.kind === "person" ? "human" : "automated"
      const words = String(s.note || "")
      return kind === "human"
        ? { ...newStep("human", String(s.title || "")), description: words }
        : { ...newStep("automated", String(s.title || "")), prompt: words }
    }))
    setErr("")
  }

  const post = async (body: any) => {
    const r = await fetch("/api/submit", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) })
    const j = await r.json().catch(() => ({}))
    if (!r.ok) throw new Error(String(j?.error || `the server answered ${r.status}`))
    return j
  }
  const check = async () => {
    setBusy("check"); setErr(""); setPreview(null)
    try { const j = await post({ goal: goal.trim(), description: description.trim(), steps: sent, preview: true }); setPreview(j.preview || null) }
    catch (ex) { setErr(String((ex as Error)?.message || ex)) }
    finally { setBusy("") }
  }
  const submit = async () => {
    setBusy("submit"); setErr(""); setPreview(null)
    try {
      const j = await post({ goal: goal.trim(), description: description.trim(), steps: sent })
      const stored: Submission | undefined = j.submission
      if (!stored) throw new Error("the submission came back empty")
      setSub(stored)
      // A reviewer that answered within the wait has already returned verdicts; otherwise they arrive by email, and the
      // panel can ask again (the refresh below) rather than pretending it knows.
      const id = String(stored.submission_id || stored.id || "")
      if (id) onSubmitted?.(id)
    } catch (ex) { setErr(String((ex as Error)?.message || ex)) }
    finally { setBusy("") }
  }
  const refresh = async () => {
    const id = String(sub?.submission_id || sub?.id || "")
    if (!id) return
    setBusy("check"); setErr("")
    try { const j = await post({ submissionId: id }); if (j.submission) setSub(j.submission) }
    catch (ex) { setErr(String((ex as Error)?.message || ex)) }
    finally { setBusy("") }
  }

  const subId = String(sub?.submission_id || sub?.id || "")

  return (
    <div className="dk-sw-backdrop" onMouseDown={() => { if (!busy) onClose() }}>
      <div className="dk-sw" role="dialog" aria-modal="true" aria-labelledby="dk-sw-title" ref={box} onMouseDown={(e) => e.stopPropagation()}>
        <header className="dk-sw-head">
          <div>
            <h2 id="dk-sw-title">Submit a Way</h2>
            <p>Teach kell a goal and the steps that achieve it. Each step is reviewed before it can be used.</p>
          </div>
          <button type="button" className="dk-icon dk-icon-sm" aria-label="Close" title={busy ? "Waiting for kell…" : "Close (Esc)"}
            disabled={!!busy} onClick={onClose}>×</button>
        </header>

        <div className="dk-sw-body">
          <label className="dk-sw-field">
            <span>Goal</span>
            <input ref={first} value={goal} maxLength={200} disabled={!!sub || !!busy} placeholder="What this Way achieves, e.g. ML research"
              aria-label="Goal" onChange={(e) => setGoal(e.target.value)} />
          </label>
          <label className="dk-sw-field">
            <span>What it is for <i>optional</i></span>
            <textarea value={description} rows={2} maxLength={4000} disabled={!!sub || !!busy} placeholder="A sentence on when this Way is the right one."
              aria-label="Description" onChange={(e) => setDescription(e.target.value)} />
          </label>

          <div className="dk-sw-steps-head">
            <h3>Steps <span>{steps.length}</span></h3>
            {seedSteps && !sub && (
              <button type="button" className="btn-line dg-mini" disabled={!!busy} onClick={seed}
                title="Fill these steps from the Way that was on screen; edit them before submitting">
                Start from {from?.name ? `“${from.name.length > 28 ? `${from.name.slice(0, 27)}…` : from.name}”` : "that Way"}
              </button>
            )}
            <span className="dk-sw-mode" title="Worked out from the steps: kell never asks you to choose a mode">{sub?.mode_label || modeOf(steps)}</span>
          </div>
          <ol className="dk-sw-steps">
            {steps.map((s, i) => {
              const out = sub?.steps?.find((o) => Number(o.order) === i + 1)
              return (
                <li key={s.id} className="dk-sw-step" data-state={out?.status || undefined}>
                  <div className="dk-sw-step-top">
                    <span className="n">{i + 1}</span>
                    <div className="dk-sw-kind" role="group" aria-label={`Who does step ${i + 1}`}>
                      <button type="button" data-on={s.kind === "human" || undefined} disabled={!!sub || !!busy}
                        onClick={() => edit(s.id, { kind: "human" })}>A person</button>
                      <button type="button" data-on={s.kind === "automated" || undefined} disabled={!!sub || !!busy}
                        onClick={() => edit(s.id, { kind: "automated" })}>kell</button>
                    </div>
                    <input className="dk-sw-title" value={s.title} maxLength={200} disabled={!!sub || !!busy}
                      placeholder="What this step achieves" aria-label={`Step ${i + 1} title`} onChange={(e) => edit(s.id, { title: e.target.value })} />
                    {!sub && (
                      <span className="dk-sw-step-acts">
                        <button type="button" aria-label={`Move step ${i + 1} up`} title="Move up" disabled={i === 0 || !!busy} onClick={() => move(i, -1)}>↑</button>
                        <button type="button" aria-label={`Move step ${i + 1} down`} title="Move down" disabled={i === steps.length - 1 || !!busy} onClick={() => move(i, 1)}>↓</button>
                        <button type="button" aria-label={`Remove step ${i + 1}`} title="Remove this step" disabled={steps.length === 1 || !!busy} onClick={() => remove(s.id)}>×</button>
                      </span>
                    )}
                  </div>
                  {s.kind === "human" ? (
                    <input value={s.description} maxLength={4000} disabled={!!sub || !!busy}
                      placeholder="What the person does, e.g. Search 'xyz' on abc.com and copy the top 5 links"
                      aria-label={`Step ${i + 1}: what the person does`} onChange={(e) => edit(s.id, { description: e.target.value })} />
                  ) : (
                    <div className="dk-sw-auto">
                      <input value={s.prompt} maxLength={4000} disabled={!!sub || !!busy}
                        placeholder="What kell does, i.e. a prompt" aria-label={`Step ${i + 1}: kell's prompt`} onChange={(e) => edit(s.id, { prompt: e.target.value })} />
                      <input value={s.locator} maxLength={2000} disabled={!!sub || !!busy}
                        placeholder="or where: https://…, path/to/file, script.py, tool-name" aria-label={`Step ${i + 1}: a locator`}
                        onChange={(e) => edit(s.id, { locator: e.target.value })} />
                    </div>
                  )}
                  {!sub && s.kind === "automated" && !s.prompt.trim() && !s.locator.trim() && (
                    <p className="dk-note">kell does this step: give it a prompt, a locator (a website, file, script or tool), or both.</p>
                  )}
                  {out && (
                    <p className="dk-sw-verdict" data-status={out.status}>
                      {out.status === "rejected" ? `Not approved: ${out.reason || "the reviewer gave no reason"}` : out.status || "reviewing"}
                    </p>
                  )}
                </li>
              )
            })}
          </ol>
          {!sub && (
            <button type="button" className="btn-line dg-mini" disabled={!!busy} onClick={() => setSteps((a) => [...a, newStep("human")])}>+ Add step</button>
          )}

          {preview && !sub && (
            <p className="dk-sw-preview" role="status">{preview.message || `This will be submitted as ${preview.mode_label || "a Way"}.`}</p>
          )}
          {err && <p className="dk-err" role="alert">{err}</p>}

          {sub && (
            <div className="dk-sw-done" role="status">
              <p>
                <b>Submitted.</b>{" "}
                {modeOf(steps)} · each step reviewed on its own. An approved Way goes into kell&apos;s rankings marked
                &quot;AI approved&quot;; a step that is turned down comes back with its reason (and by email). You can close
                this and keep working.
              </p>
              {subId && onSubmitted && (
                <button type="button" className="btn-line dg-mini" onClick={() => { onClose(); onSubmitted(subId) }}>Show this Way in the chat →</button>
              )}
            </div>
          )}
        </div>

        <footer className="dk-sw-foot">
          <span className="dk-note">{sub ? "Review happens on kell's side; this asks again for the verdicts." : "Nothing is stored until you submit."}</span>
          <span className="dk-sw-actions">
            {sub ? (
              <>
                <button type="button" className="btn-line" disabled={!!busy} onClick={() => void refresh()}>{busy === "check" ? "Checking…" : "Check the verdicts"}</button>
                <button type="button" className="btn-ink" onClick={onClose}>Done</button>
              </>
            ) : (
              <>
                <button type="button" className="btn-line" disabled={!ready || !!busy} onClick={() => void check()}>{busy === "check" ? "Checking…" : "Check it"}</button>
                <button type="button" className="btn-ink" disabled={!ready || !!busy} onClick={() => void submit()}
                  title={ready ? "Submit this Way for review" : "Fill the goal and every step first"}>
                  {busy === "submit" ? "Submitting…" : "Submit"}
                </button>
              </>
            )}
          </span>
        </footer>
      </div>
    </div>
  )
}
