"use client"
/**
 * A Way's run, as a control surface in the room's right rail.
 *
 * Researched before building (2026-11-23): the guidance is consistent that chat is for conversation and a
 * multi-step run needs a **persistent status surface** beside it — "chat excels at goal-setting but struggles with
 * ongoing visibility", and an inline card should stay small (≤2 actions, no nested scrolling, ≤4–5 data points).
 *
 * So the run is not rendered as a takeover of the transcript. It lives here: a checklist of the steps, the one that
 * is running, the inputs it needs, and the files it produced. The room's messages stay messages.
 *
 * It does not start by itself: a person who arrives from a purchase reads what the Way needs, then presses Start.
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
  if (paise == null || !Number.isFinite(paise)) return "—"
  const r = paise / 100
  return `₹${Number.isInteger(r) ? r.toLocaleString("en-IN") : r.toFixed(2)}`
}

export default function WayRunPanel({ roomId, wayId }: { roomId?: string; wayId?: string }) {
  const [view, setView] = useState<RunState | null>(null)
  const [steps, setSteps] = useState<StepDef[]>([])
  const [outputs, setOutputs] = useState<Output[]>([])
  const [files, setFiles] = useState<FileOut[]>([])
  const [values, setValues] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [openInputs, setOpenInputs] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

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
    } catch (e) {
      setError(String((e as Error)?.message || e))
    }
  }, [roomId])

  useEffect(() => { void load() }, [load])

  // While a run is going, keep the panel current without the person reloading. Stops as soon as it finishes.
  useEffect(() => {
    const running = view?.state === "running"
    if (!running) return
    timer.current = setTimeout(() => void load(), 3000)
    return () => { if (timer.current) clearTimeout(timer.current) }
  }, [view, load])

  useEffect(() => {
    if (!wayId) return
    void call<{ steps: StepDef[] }>(`/v1/goals/submissions/${encodeURIComponent(wayId)}/steps/cost`)
      .then((r) => setSteps(r.steps || []))
      .catch(() => { /* the estimate is information; its absence must not break the run */ })
  }, [wayId])

  async function start() {
    if (!roomId) return
    setBusy(true)
    setError(null)
    try {
      await call(`/v1/rooms/${encodeURIComponent(roomId)}/run/start`, {
        method: "POST",
        body: { submission_id: wayId, values },
      })
      setOpenInputs(false)
      await load()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy(false)
    }
  }

  if (!roomId) return null

  // Nothing to show when the room is not running a Way: the rail keeps its other cards rather than an empty one.
  if (!view) return null
  if (view.state === "no_way") return null

  const done = outputs.length
  const total = steps.length || 0
  const pct = total ? Math.round((done / total) * 100) : 0

  return (
    <section className="dg-rail-card" aria-label="This Way">
      <h2>This Way</h2>

      {view.state === "needs_inputs" && (
        <div className="dg-run-panel">
          <p className="dg-rail-muted">It needs {view.inputs.length} answer{view.inputs.length === 1 ? "" : "s"} before it can start.</p>
          {openInputs ? (
            <ul className="dg-run-inputs">
              {view.inputs.map((i) => (
                <li key={`${i.step}.${i.key}`}>
                  <label htmlFor={`ri-${i.step}-${i.key}`}>
                    {i.label}
                    <span className="dg-run-which">step {i.step}</span>
                  </label>
                  <input
                    id={`ri-${i.step}-${i.key}`}
                    value={values[`${i.step}.${i.key}`] || ""}
                    onChange={(e) => setValues((v) => ({ ...v, [`${i.step}.${i.key}`]: e.target.value }))}
                    required={i.required}
                  />
                  {i.help && <span className="dg-rail-muted">{i.help}</span>}
                </li>
              ))}
            </ul>
          ) : null}
          <button
            type="button"
            className="dg-run-cta"
            disabled={busy}
            onClick={() => (openInputs ? void start() : setOpenInputs(true))}
          >
            {openInputs ? (busy ? "Starting…" : "Start") : "Answer to start"}
          </button>
        </div>
      )}

      {view.state === "ready" && (
        <div className="dg-run-panel">
          <p className="dg-rail-muted">{total ? `${total} steps, one at a time.` : "Ready when you are."}</p>
          <button type="button" className="dg-run-cta" disabled={busy} onClick={() => void start()}>
            {busy ? "Starting…" : "Start"}
          </button>
        </div>
      )}

      {(view.state === "running" || view.state === "done" || view.state === "failed") && (
        <div className="dg-run-panel">
          <div className="dg-run-progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
            {/* scaleX, not width: a width change repaints every frame (docs/ios_animation.md §6 rule 1). */}
            <span style={{ transform: `scaleX(${pct / 100})` }} />
          </div>
          <p className="dg-rail-muted">{done} of {total || "?"} steps done</p>
          <ol className="dg-run-steps">
            {outputs.map((o) => (
              <li key={o.order} data-done="true">
                <span className="dg-run-n">{o.order}</span>
                <span>{o.title}</span>
              </li>
            ))}
            {view.state === "running" && total > done && steps[done] && (
              <li data-running="true">
                <span className="dg-run-n">{steps[done].order}</span>
                <span>{steps[done].title}</span>
              </li>
            )}
          </ol>
          {error && <p className="dg-rail-muted">{error}</p>}
        </div>
      )}

      {files.length > 0 && (
        <div className="dg-run-panel">
          <p className="dg-rail-h">Output</p>
          <ul className="dg-run-files">
            {files.map((f) => (
              <li key={f.name}>
                <button type="button" onClick={() => void downloadArtifact(f.url, f.name)}>{f.name}</button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
