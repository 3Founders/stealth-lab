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
  state:
    | "needs_inputs"
    | "ready"
    | "running"
    | "waiting_human"   // a step needs a person: the run is paused on it, not finished
    | "waiting_session"
    | "done"
    | "failed"
    | "no_way"
    | "cancelled"
  run: { run_id: string; status: string } | null
  inputs: InputDef[]
  /** The room knows which Way it is for, so a /chat?room=... link works without &way=... */
  submission_id?: string
  /** Set while paused on a human step: which one, and what it asks for (app/execution/way_runs.py::view). */
  waiting?: { kind?: string; order?: number; title?: string; description?: string } | null
}
type Output = { order: number; title: string; output: string }
type FileOut = { name: string; size_bytes?: number; url: string }
type Refused = { name: string; why: string }

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
  const [refused, setRefused] = useState<Refused[]>([])
  const [downloadAllUrl, setDownloadAllUrl] = useState<string>("")
  const [values, setValues] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState("")
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** The Way to start. The room knows it even when the URL did not say, so the server's answer wins over `?way=`. */
  const wayForRun = view?.submission_id || wayId || ""

  const load = useCallback(async () => {
    if (!roomId) return
    try {
      const r = await call<RunState>(`/v1/rooms/${encodeURIComponent(roomId)}/run`)
      setView(r)
      if (r.run?.run_id) {
        const full = await call<{
          outputs?: Output[]
          artifacts?: FileOut[]
          artifacts_refused?: Refused[]
          download_all?: string
        }>(`/v1/goals/runs/${encodeURIComponent(r.run.run_id)}`)
        setOutputs(full.outputs || [])
        setFiles(full.artifacts || [])
        setRefused(full.artifacts_refused || [])
        setDownloadAllUrl(full.download_all || "")
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

  async function downloadZip() {
    if (!downloadAllUrl) return
    setBusy(true)
    setError(null)
    try {
      await downloadArtifact(downloadAllUrl, "way-output.zip")
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy(false)
    }
  }

  async function resume() {
    if (!roomId) return
    setBusy(true)
    setError(null)
    try {
      await call(`/v1/rooms/${encodeURIComponent(roomId)}/run/resume`, {
        method: "POST",
        body: { note },
      })
      setNote("")
      await load()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy(false)
    }
  }

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

  // No room id yet is a WAITING state, not a "not a Way" state: `info` arrives from sync a few seconds after a cold
  // load, so bailing out here bounced the person back to the conversation ("Loading messages…") and they never
  // reached the Way at all. Only the server's own "no_way" answer means this room is not a Way.
  const noWay = view?.state === "no_way"
  useEffect(() => { if (noWay) onNothing?.() }, [noWay, onNothing])

  const done = outputs.length
  const total = steps.length || 0
  const pct = total ? Math.round((done / total) * 100) : 0
  const running = view?.state === "running"
  // A run paused on a human step is NOT finished. Rendering it as "finished" is what the owner saw after pressing
  // Start: the run was waiting_human at step 1 and the pane said "Finished." (2026-11-23).
  const paused = view?.state === "waiting_human" || view?.state === "waiting_session"
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
        {total > 0 && (
          <p className="dg-way-sub">
            {done} of {total} steps done{finished ? " · finished" : paused ? " · waiting on you" : running ? " · running" : ""}
          </p>
        )}
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

      {/* Paused on a human step. The reference for this shape is LangGraph's human-in-the-loop: the agent stops, the
          UI shows the pending step and what it needs, and the run resumes from where it stopped -- never replaying. */}
      {paused && view?.waiting && (
        <div className="dg-way-pause">
          <h3 className="dg-way-sec">Waiting on you — step {view.waiting.order}</h3>
          <p className="dg-way-pause-title">{view.waiting.title}</p>
          {view.waiting.description && <p className="dg-way-note">{view.waiting.description}</p>}
          <form
            className="dg-way-pause-form"
            onSubmit={(e) => { e.preventDefault(); void resume() }}
          >
            <label htmlFor="dg-way-note">What you did or found</label>
            <input
              id="dg-way-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="A line or two is enough"
            />
            <button type="submit" className="dg-way-start" disabled={busy}>{busy ? "Continuing…" : "Continue"}</button>
          </form>
        </div>
      )}

      {/* Start is the one action of a run that has not begun. It is never disabled without saying why. */}
      {!running && !paused && !finished && (
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
                  <span className="dg-way-fname">{f.name}</span>
                  {f.size_bytes != null && <span className="dg-way-fsize">{Math.max(1, Math.round(f.size_bytes / 1024))} KB</span>}
                </button>
              </li>
            ))}
          </ul>
          {files.length > 1 && (
            <button
              type="button"
              className="dg-way-all"
              disabled={busy}
              onClick={() => void downloadZip()}
            >
              Download all ({files.length})
            </button>
          )}
        </div>
      )}

      {/* Files the run refused, and why. Without this a missing `.tex` looks like a bug rather than a cut-off reply. */}
      {refused.length > 0 && (
        <div className="dg-way-out">
          <h3>Not kept</h3>
          <ul>
            {refused.map((r) => (
              <li key={r.name}>
                <span className="dg-way-fname">{r.name}</span>
                <span className="dg-way-fsize">{r.why}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
