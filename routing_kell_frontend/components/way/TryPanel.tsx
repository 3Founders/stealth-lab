"use client";
import Link from "next/link";
import { useState } from "react";
import { getAccessToken } from "@/lib/session";
import { MCP_ORIGIN } from "@/lib/connector";
import { submissionsApi, type Run } from "@/lib/submissions-api";

/**
 * Try a published Way and read its output here. This is the public page's run action: it calls the
 * same `/v1/goals/submissions/{id}/runs` path the website and the MCP tool use, and renders the
 * step outputs. Nothing is stored as a new Way. A priced Way the reader has not bought never
 * reaches this panel (the page shows the price block instead).
 */
export default function TryPanel({ wayId, goalName }: { wayId: string; goalName: string }) {
  const [input, setInput] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");

  const client = () => submissionsApi({ token: getAccessToken, origin: MCP_ORIGIN });

  async function settle(api: ReturnType<typeof client>, first: Run) {
    let current = first;
    setRun(current);
    // A headless run usually finishes at once; if it is still running, poll for a bounded minute.
    for (let i = 0; i < 30 && current.status === "running"; i++) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      current = await api.getRun(current.id);
      setRun(current);
    }
  }

  async function start() {
    setBusy(true);
    setError("");
    setRun(null);
    try {
      const api = client();
      await settle(api, await api.run(wayId, input.trim() || goalName));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not run this Way.");
    } finally {
      setBusy(false);
    }
  }

  async function finished() {
    if (!run) return;
    setBusy(true);
    setError("");
    try {
      const api = client();
      await settle(api, await api.done(run.id, note));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not continue the run.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="kwm-card" id="kwm-try-h" aria-labelledby="kwm-try-title">
      <h2 id="kwm-try-title">Try it</h2>
      <p>
        Give it the input it needs. You will see each step&apos;s output here. This is a run of this
        Way, not a purchase of a new one.
      </p>

      <label htmlFor="kwm-try-input" className="kwm-small"><b>Input</b></label>
      <textarea
        id="kwm-try-input"
        rows={3}
        value={input}
        onChange={(e) => setInput(e.target.value)}
        placeholder={`e.g. ${goalName}`}
        style={{ width: "100%", marginTop: 6, padding: 10, font: "inherit", borderRadius: 8, border: "1px solid var(--line, #d8d4cc)", background: "transparent", color: "inherit" }}
      />
      <p>
        <button type="button" className="kwm-cta" onClick={start} disabled={busy}>
          {busy ? "Running…" : "Try this Way"}
        </button>
      </p>

      {error && (
        <p className="kwm-small" role="alert" style={{ color: "var(--danger, #b00020)" }}>
          {error} {/sign in/i.test(error) && <Link href={`/sign-in?redirect=${encodeURIComponent(`/way/${wayId}`)}`} style={{ textDecoration: "underline" }}>Sign in</Link>}
        </p>
      )}

      {run && (
        <div style={{ marginTop: 16 }}>
          <p className="kwm-small">
            <b>Status:</b> {run.status.replace(/_/g, " ")}
            {typeof run.steps === "number" ? ` · ${run.next_step || 0} of ${run.steps} steps` : ""}
          </p>
          {run.error && <p className="kwm-small" style={{ color: "var(--danger, #b00020)" }}>{run.error}</p>}

          {run.outputs.length > 0 && (
            <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
              {run.outputs.map((out) => (
                <details key={out.order} open={out.order === run.outputs[run.outputs.length - 1]?.order}>
                  <summary className="kwm-small"><b>{out.order}. {out.title}</b> · {out.actor === "human" ? "you" : "kell"}</summary>
                  <pre style={{ whiteSpace: "pre-wrap", overflowX: "auto", maxHeight: 360, padding: 12, marginTop: 8, borderRadius: 8, background: "var(--wash, #f4f1ea)" }}>{out.output || "(no text)"}</pre>
                </details>
              ))}
            </div>
          )}

          {run.status === "waiting_human" && (
            <div style={{ marginTop: 12 }}>
              <p className="kwm-small">
                <b>Needs you:</b> {run.waiting_on?.title || "a step only a person can do"}
                {run.waiting_on?.description ? ` — ${run.waiting_on.description}` : ""}
              </p>
              {run.waiting_on?.link && (
                <p className="kwm-small"><a href={run.waiting_on.link} target="_blank" rel="noopener noreferrer" style={{ textDecoration: "underline" }}>Open the link</a></p>
              )}
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="What did you do or find?"
                style={{ width: "100%", padding: 10, font: "inherit", borderRadius: 8, border: "1px solid var(--line, #d8d4cc)", background: "transparent", color: "inherit" }}
              />
              <p>
                <button type="button" className="kwm-cta" onClick={finished} disabled={busy}>
                  {busy ? "Continuing…" : "I did this — continue"}
                </button>
              </p>
            </div>
          )}

          {run.status === "waiting_session" && (
            <p className="kwm-small kwm-muted">
              This step needs the desktop agent. Open this Way in kell desktop to continue it there.
            </p>
          )}
          {run.status === "done" && <p className="kwm-small"><b>Done.</b> The outputs above are the result.</p>}
        </div>
      )}
    </section>
  );
}
