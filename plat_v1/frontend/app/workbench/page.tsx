"use client";

import Link from "next/link";
import { useState } from "react";
import { api, RunResponse } from "@/lib/api";
import { planToGraph } from "@/lib/opsToGraph";
import TraceStrip from "@/components/TraceStrip";
import WorkflowGraph from "@/components/WorkflowGraph";

export default function WorkbenchPage() {
  const [prompt, setPrompt] = useState("");
  const [inputsText, setInputsText] = useState('{\n  "pdf_path": ""\n}');
  const [result, setResult] = useState<RunResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  async function submit() {
    const text = prompt.trim();
    if (!text || working) return;

    let inputs: Record<string, unknown>;
    try {
      inputs = inputsText.trim() ? JSON.parse(inputsText) : {};
    } catch {
      setError("Inputs must be a JSON object.");
      return;
    }

    setWorking(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api.run({ prompt: text, inputs }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not reach the platform.");
    } finally {
      setWorking(false);
    }
  }

  const graph = result?.route === "decompose" ? planToGraph(result.plan) : null;

  return (
    <main className="shell">
      <div className="masthead">
        <div>
          <div className="masthead-title">Workbench</div>
          <div className="masthead-sub">a prompt goes in, work comes out</div>
        </div>
      </div>

      <div className="nav-tabs">
        <Link href="/workbench" className="nav-tab active">
          Workbench
        </Link>
        <Link href="/approvals" className="nav-tab">
          Proposals
        </Link>
        <Link href="/archive" className="nav-tab">
          Runs
        </Link>
      </div>

      <textarea
        className="problem-input"
        value={prompt}
        placeholder="e.g. turn the tables in this PDF into a spreadsheet"
        onChange={(e) => setPrompt(e.target.value)}
        rows={4}
        disabled={working}
      />
      <div className="case-label" style={{ marginTop: "0.75rem" }}>
        Inputs (JSON)
      </div>
      <textarea
        className="problem-input"
        value={inputsText}
        onChange={(e) => setInputsText(e.target.value)}
        rows={5}
        disabled={working}
      />
      <div className="ask-bar">
        <button className="ask-button" onClick={submit} disabled={working || !prompt.trim()}>
          {working ? "Working…" : "Run"}
        </button>
      </div>

      {error && <div className="empty-state">{error}</div>}

      {result?.route === "match" && (
        <div className="case-file" style={{ marginTop: "2rem" }}>
          <h2 className="case-heading">
            Matched <em>{result.matched_task}</em> and ran it
          </h2>
          <div className="docket-meta" style={{ marginBottom: "1rem" }}>
            <span>score {result.match_score.toFixed(4)}</span>
            <span>status {result.status}</span>
            <span>
              <Link href="/archive">run {result.run_id.slice(0, 8)}</Link>
            </span>
          </div>

          {result.error && <div className="objection">{result.error}</div>}

          <div className="case-section">
            <div className="case-label">Outputs</div>
            <pre className="change-op">{JSON.stringify(result.outputs, null, 2)}</pre>
          </div>

          <div className="case-section">
            <div className="case-label">Per-stage trace</div>
            <TraceStrip stages={result.stages} />
          </div>
        </div>
      )}

      {result?.route === "decompose" && (
        <div className="case-file" style={{ marginTop: "2rem" }}>
          {/* The structural verdict goes above the plan. A reviewer who reads
              a plausible-looking DAG first has already been influenced by it
              before learning that node 4's input is produced by nothing. */}
          {!result.typecheck?.ok && (
            <div className="tier-banner simulated">
              This plan failed typecheck and cannot be approved
              <span className="tier-detail">
                {result.typecheck?.messages?.length ?? 0} structural problem
                {(result.typecheck?.messages?.length ?? 0) === 1 ? "" : "s"}. Structural failure is not
                a judgement call, so there is no approve button.
              </span>
            </div>
          )}

          <h2 className="case-heading">
            {result.feasible
              ? `${result.plan.nodes.length} step${result.plan.nodes.length === 1 ? "" : "s"} proposed`
              : "No plan could be derived"}
          </h2>

          <div className="case-section">
            <div className="case-label">Why this wasn&apos;t matched to an existing task</div>
            <p className="case-body">{result.match_reason}</p>
          </div>

          {result.reasoning && (
            <div className="case-section">
              <div className="case-label">Reasoning</div>
              <p className="case-body">{result.reasoning}</p>
            </div>
          )}

          {graph && graph.nodes.length > 0 && (
            <div className="case-section">
              <div className="case-label">Proposed workflow</div>
              <WorkflowGraph
                nodes={graph.nodes}
                edges={graph.edges}
                center={graph.nodes[0].id}
              />
            </div>
          )}

          {(result.typecheck?.messages?.length ?? 0) > 0 && (
            <div className="case-section">
              <div className="case-label">Typecheck problems</div>
              <ul className="evidence-notes">
                {(result.typecheck?.messages ?? []).map((m, i) => (
                  <li key={i}>{m}</li>
                ))}
              </ul>
            </div>
          )}

          {result.candidates.length > 0 && (
            <div className="case-section">
              <div className="case-label">Existing tasks that came closest</div>
              <ul className="evidence-notes">
                {result.candidates.map((c) => (
                  <li key={c.id}>
                    {c.name} — {c.score.toFixed(4)}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="case-section">
            <div className="case-label">Status</div>
            <p className="case-body">
              Nothing has run. This is a proposal awaiting approval; approving it persists
              the new tasks and executes the plan.
            </p>
            <Link href={`/approvals/${result.proposal_id}`} className="back-link">
              review this proposal →
            </Link>
          </div>
        </div>
      )}
    </main>
  );
}
