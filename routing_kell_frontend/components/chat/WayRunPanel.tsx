"use client"
/**
 * A Way's run, as the room's MAIN pane.
 *
 * This was the right rail until the owner ran a Way and could not find Start (2026-11-23): the run sat in a 280px
 * column beside a conversation that was empty, while the middle of the screen said nothing. So when a room belongs
 * to a Way, the Way IS the room: the middle pane is the steps, the Start button, the progress and the output, and the
 * chat composer is hidden. A room that is not a Way renders the conversation exactly as before.
 *
 * The earlier research still holds and is why this is a checklist rather than a wizard: start with the list, expand
 * a step in place. What changed is the placement, not the shape -- a status surface a person cannot find is not one.
 */
import { useCallback, useEffect, useRef, useState } from "react"

import { MCP_ORIGIN } from "@/lib/connector"
import { getAccessToken } from "@/lib/session"
import { downloadArtifact } from "@/lib/api"

type InputDef = { step: number; step_title: string; key: string; label: string; required: boolean; help: string }
type StepDef = { order: number; title: string; price_low_paise: number; price_high_paise: number; is_human?: boolean }
type RunState = {
  state: "needs_inputs" | "ready" | "running" | "done" | "failed" | "no_way" | "cancelled"
  run: { run_id: string; status: string } | null
  inputs: InputDef[]
  /** The room knows which Way it is for, so a /chat?room=... link works without &way=... */
  submission_id?: string
}
type Output = { order: number; title: string; output: string }
type FileOut = { name: string; size_bytes?: number; url: string }

async function call<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const token = await getAccessToken()
  const headers: Record<string, string> = { Accept: "application/json" }
  if (token) headers.Authorization = `Bearer ${token}`
  if (init?.body !== undefined) headers["Content-Type"] = "application/json"
  const res = await fetch(`${MCP_ORIGIN}${path}`, {
    method: init?.method || "GET",
    headers,
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
    cache: "no-store",
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(String(body?.error?.message || body?.error || `the server answered ${res.status}`))
  return body as T
}

function rupees(paise?: number | null): string {
  if (paise == null || !Number.isFinite(paise)) return ""
  const r = paise / 100
  return `₹${Number.isInteger(r) ? r.toLocaleString("en-IN") : r.toFixed(2)}`
}

/** `onNothing` lets the room reclaim the middle pane when it turns out this room is not a Way after all. */
export default function WayRunPanel({
  roomId,
  wayId,
  title,
  onNothing,
}: {
  roomId?: string
  wayId?: string
  title?: string
  onNothing?: () => void
}) {
  const [view, setView] = useState<RunState | null>(null)
  const [steps, setSteps] = useState<StepDef[]>([])
  const [outputs, setOutputs] = useState<Output[]>([])
  const [files, setFiles] = useState<FileOut[]>([])
  const [values, setValues] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** The Way to start. The room knows it even when the URL did not say, so the server's answer wins over `?way=`. */
  const wayForRun = view?.submission_id || wayId || ""

  const load = useCallback(async () => {
    if (!roomId) return
    try {
      const r = await call<RunState>(`/v1/rooms/${encodeURIComponent(roomId)}/run`)
      setView(r)
      if (r.run?.run_id) {
        const full = await call<{ outputs?: Output[]; artifacts?: FileOut[] }>(
          `/v1/goals/runs/${encodeURIComponent(r.run.run_id)}`)
        setOutputs(full.outputs || [])
        setFiles(full.artifacts || [])
      }
      if (r.submission_id && !wayId) {
        // The room knows its Way even when the URL did not say so: one read, then the step list is real.
        void call<{ steps: StepDef[] }>(`/v1/goals/submissions/${encodeURIComponent(r.submission_id)}/steps/cost`)
          .then((c) => setSteps(c.steps || []))
          .catch(() => { /* the estimate is information; its absence must not break the run */ })
      }
    } catch (e) {
      setError(String((e as Error)?.message || e))
    }
  }, [roomId, wayId])

  useEffect(() => { void load() }, [load])

  // While a run is going, keep the panel current without the person reloading. Stops as soon as it finishes.
  useEffect(() => {
    if (view?.state !== "running") return
    timer.current = setTimeout(() => void load(), 3000)
    return () => { if (timer.current) clearTimeout(timer.current) }
  }, [view, load])

  useEffect(() => {
    if (!wayId) return
    void call<{ steps: StepDef[] }>(`/v1/goals/submissions/${encodeURIComponent(wayId)}/steps/cost`)
      .then((r) => setSteps(r.steps || []))
      .catch(() => { /* information only */ })
  }, [wayId])

  async function start() {
    if (!roomId) return
    if (!wayForRun) {
      setError("this room does not name the Way it should run — open it from the Way's page")
      return
    }
    setBusy(true)
    setError(null)
    try {
      await call(`/v1/rooms/${encodeURIComponent(roomId)}/run/start`, {
        method: "POST",
        body: { submission_id: wayForRun, values },
      })
      await load()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy(false)
    }
  }

  if (!roomId) { onNothing?.(); return null }
  if (view && view.state === "no_way") { onNothing?.(); return null }

  const done = outputs.length
  const total = steps.length || 0
  const pct = total ? Math.round((done / total) * 100) : 0
  const running = view?.state === "running"
  const finished = view?.state === "done" || view?.state === "failed"

  // A step is "done" once the run has an output for it; "running" while it is the next one and the run is going.
  const stateOf = (order: number) => {
    if (outputs.some((o) => o.order === order)) return "done"
    if (running && steps[done]?.order === order) return "running"
    return "waiting"
  }

  return (
    <section className="dg-way-main" aria-label={title ? `${title}: steps and run` : "Way run"}>
      <header className="dg-way-head">
        <h2>{title || "This Way"}</h2>
        {total > 0 && <p className="dg-way-sub">{done} of {total} steps done{finished ? " · finished" : ""}</p>}
      </header>

      {total > 0 && (
        <div className="dg-way-bar" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
          {/* scaleX, not width: a width change is layout every frame (docs/ios_animation.md §6 rule 1). */}
          <span style={{ transform: `scaleX(${pct / 100})` }} />
        </div>
      )}

      {!view && <p className="dg-way-note" role="status">Reading this Way…</p>}

      {view?.state === "needs_inputs" && (
        <div className="dg-way-inputs">
          <h3 className="dg-way-sec">What it needs from you</h3>
          <ul>
            {view.inputs.map((i) => (
              <li key={`${i.step}.${i.key}`}>
                <label htmlFor={`wi-${i.step}-${i.key}`}>
                  {i.label}
                  <span className="dg-way-stepof">step {i.step}</span>
                </label>
                <input
                  id={`wi-${i.step}-${i.key}`}
                  value={values[`${i.step}.${i.key}`] || ""}
                  onChange={(e) => setValues((v) => ({ ...v, [`${i.step}.${i.key}`]: e.target.value }))}
                  required={i.required}
                />
                {i.help && <span className="dg-way-note">{i.help}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {total > 0 && (
        <div className="dg-way-list">
          <h3 className="dg-way-sec">Steps</h3>
          <ol className="dg-way-steps">
            {steps.map((s) => {
              const st = stateOf(s.order)
              const price = s.price_high_paise ? rupees(s.price_high_paise) : ""
              const wrote = outputs.find((o) => o.order === s.order)
              return (
                <li key={s.order} data-state={st} data-human={s.is_human ? "true" : "false"}>
                  <span className="dg-way-n" aria-hidden="true">{st === "done" ? "✓" : s.order}</span>
                  <span className="dg-way-col">
                    <span className="dg-way-title">{s.title}</span>
                    {/* The step's own output, in place: what "see the steps and outputs" means for a long run. */}
                    {wrote?.output && <span className="dg-way-out-step">{wrote.output}</span>}
                  </span>
                  {s.is_human && <span className="dg-way-kind">you</span>}
                  {price && st === "waiting" && <span className="dg-way-price">{price}</span>}
                </li>
              )
            })}
          </ol>
        </div>
      )}

      {/* Start is the one action. It is never disabled without saying why -- an unclickable button with no reason
          is what sent the owner looking for a different screen. */}
      {!running && !finished && (
        <div className="dg-way-act">
          <button type="button" className="dg-way-start" disabled={busy} onClick={() => void start()}>
            {busy ? "Starting…" : view?.state === "needs_inputs" ? "Start with these answers" : "Start"}
          </button>
          <p className="dg-way-note">
            {view ? "A human step pauses the run and waits for you." : "Reading this Way — Start opens when it has loaded."}
          </p>
        </div>
      )}

      {finished && (
        <div className="dg-way-act">
          <p className="dg-way-note">
            {view?.state === "done" ? "Finished." : "Stopped with a failure — the steps below show how far it got."}
          </p>
        </div>
      )}

      {error && <p className="dg-way-error" role="alert">{error}</p>}

      {files.length > 0 && (
        <div className="dg-way-out">
          <h3>Output</h3>
          <ul>
            {files.map((f) => (
              <li key={f.name}>
                <button type="button" onClick={() => void downloadArtifact(f.url, f.name)}>
                  {f.name}
                  {f.size_bytes != null && <span>{Math.max(1, Math.round(f.size_bytes / 1024))} KB</span>}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
