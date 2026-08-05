"use client";

import Link from "next/link";
import { useState } from "react";
import { agentStoreApi, decomposeApi, DecomposeResponse } from "@/lib/api";
import { opsToGraph } from "@/lib/opsToGraph";
import WorkflowGraph from "@/components/WorkflowGraph";

function PromoteSection({ decompositionId }: { decompositionId: string }) {
  const [actor, setActor] = useState("");
  const [working, setWorking] = useState(false);
  const [promoted, setPromoted] = useState<{
    agentId: string; passedReview: boolean; reviewState: string; notes: string;
  } | null>(null);
  const [agentDecision, setAgentDecision] = useState<{
    reviewState: string; runnable: boolean;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function promote() {
    if (!actor.trim()) {
      alert("Enter who's promoting this first.");
      return;
    }
    setWorking(true);
    setError(null);
    try {
      const r = await agentStoreApi.promote(decompositionId, actor);
      setPromoted({
        agentId: r.agent_id, passedReview: r.passed_review,
        reviewState: r.review_state, notes: r.review_notes,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not promote this decomposition.");
    } finally {
      setWorking(false);
    }
  }

  async function decideAgent(decision: "approved" | "rejected") {
    if (!promoted) return;
    setWorking(true);
    try {
      const r = await agentStoreApi.decideAgent(promoted.agentId, decision, actor);
      setAgentDecision({ reviewState: r.review_state, runnable: r.runnable });
    } catch (e) {
      alert(e instanceof Error ? e.message : "Could not record the agent decision.");
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className="case-section">
      <div className="case-label">Promote to a reusable agent</div>
      {!promoted && (
        <>
          <p className="case-body">
            If this decomposition is generalizable, not specific to just this
            wording, it can become a standing agent others can run directly.
            Promoting runs a fresh, independent review first.
          </p>
          <input
            className="ask-input"
            style={{ width: "100%", marginBottom: "0.75rem" }}
            placeholder="Your name or id"
            value={actor}
            onChange={(e) => setActor(e.target.value)}
          />
          <button className="ask-button" disabled={working} onClick={promote}>
            {working ? "Reviewing…" : "Promote to Agent Store"}
          </button>
        </>
      )}

      {error && <p className="case-body" style={{ color: "var(--fail)" }}>{error}</p>}

      {promoted && !agentDecision && (
        <>
          <p className="case-body">
            Review {promoted.passedReview ? "passed" : "did not pass"}
            {promoted.notes && ` — ${promoted.notes}`}
          </p>
          {promoted.reviewState === "pending_human_approval" ? (
            <div className="ruling-bar">
              <button className="ruling-button approve" disabled={working}
                      onClick={() => decideAgent("approved")}>
                Approve agent
              </button>
              <button className="ruling-button reject" disabled={working}
                      onClick={() => decideAgent("rejected")}>
                Reject agent
              </button>
            </div>
          ) : (
            <div className="stamp rejected">rejected by review</div>
          )}
        </>
      )}

      {agentDecision && (
        <div className={`stamp ${agentDecision.reviewState}`}>
          {agentDecision.reviewState}
          {agentDecision.reviewState === "approved" && (
            <span style={{ marginLeft: "0.75rem", fontSize: "0.75rem" }}>
              {agentDecision.runnable
                ? "runnable"
                : "not yet runnable — the step it depends on isn't registered"}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export default function WorkbenchPage() {
  const [problem, setProblem] = useState("");
  const [result, setResult] = useState<DecomposeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [decided, setDecided] = useState<string | null>(null);
  const [approverId, setApproverId] = useState("");

  async function submit() {
    const text = problem.trim();
    if (!text || working) return;

    setWorking(true);
    setError(null);
    setResult(null);
    setDecided(null);
    try {
      setResult(await decomposeApi.submit(text));
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Could not reach the decomposition service.",
      );
    } finally {
      setWorking(false);
    }
  }

  async function decide(decision: "approved" | "rejected") {
    if (!result || !approverId.trim()) {
      alert("Enter who's making this decision first.");
      return;
    }
    setWorking(true);
    try {
      const response = await decomposeApi.decide(result.id, approverId, decision);
      setDecided(response.decision);
    } catch (e) {
      alert(e instanceof Error ? e.message : "Could not record the decision.");
    } finally {
      setWorking(false);
    }
  }

  const graph = result ? opsToGraph(result.ops) : null;

  return (
    <main className="shell">
      <div className="masthead">
        <div>
          <div className="masthead-title">Workbench</div>
          <div className="masthead-sub">describe a problem, get a task breakdown</div>
        </div>
      </div>

      <div className="nav-tabs">
        <Link href="/workbench" className="nav-tab active">
          Workbench
        </Link>
        <Link href="/approvals" className="nav-tab">
          Docket
        </Link>
        <Link href="/archive" className="nav-tab">
          Archive
        </Link>
        <Link href="/agents" className="nav-tab">
          Agents
        </Link>
      </div>

      <textarea
        className="problem-input"
        value={problem}
        placeholder="e.g. We receive client PDFs each month and need summary charts from the tables inside them."
        onChange={(e) => setProblem(e.target.value)}
        rows={5}
        disabled={working}
      />
      <div className="ask-bar">
        <button
          className="ask-button"
          onClick={submit}
          disabled={working || !problem.trim()}
        >
          {working ? "Working…" : "Decompose"}
        </button>
      </div>

      {error && <div className="empty-state">{error}</div>}

      {result && (
        <div className="case-file" style={{ marginTop: "2rem" }}>
          {/* Manipulation suspicion goes first — a reviewer who reads the
              plan before the warning has already been influenced by it. */}
          {(result.suspected_manipulation || result.input_flagged) && (
            <div className="tier-banner simulated">
              {result.suspected_manipulation
                ? "This submission may have attempted to manipulate the system"
                : "This submission matched known manipulation patterns"}
              <span className="tier-detail">
                Read the proposed steps for anything that came from instructions
                rather than from the problem itself.
              </span>
            </div>
          )}

          {!result.feasible ? (
            <>
              <h2 className="case-heading">No workflow could be derived</h2>
              <p className="case-body">{result.reasoning}</p>
            </>
          ) : result.reused_nodes.length > 0 && result.ops.length === 0 ? (
            <>
              <h2 className="case-heading">Already covered — nothing new proposed</h2>
              <div className="case-section">
                <div className="case-label">Reasoning</div>
                <p className="case-body">{result.reasoning}</p>
              </div>
              <div className="case-section">
                <div className="case-label">
                  Matched deterministically, not a model judgment call
                </div>
                <ul className="evidence-notes">
                  {result.reused_nodes.map((n, i) => (
                    <li key={i}>
                      {n.name} — {Math.round(n.similarity * 100)}% match ({n.method})
                    </li>
                  ))}
                </ul>
              </div>
            </>
          ) : (
            <>
              <h2 className="case-heading">
                {result.node_count} step{result.node_count === 1 ? "" : "s"} proposed
                {result.is_novel && (
                  <span className="agent-store-badge runnable" style={{ marginLeft: "0.75rem" }}>
                    entirely new
                  </span>
                )}
              </h2>

              <div className="case-section">
                <div className="case-label">Reasoning</div>
                <p className="case-body">{result.reasoning}</p>
              </div>

              {result.reused_nodes.length > 0 && (
                <div className="case-section">
                  <div className="case-label">
                    Existing content this must not duplicate (deterministic match)
                  </div>
                  <ul className="evidence-notes">
                    {result.reused_nodes.map((n, i) => (
                      <li key={i}>
                        {n.name} — {Math.round(n.similarity * 100)}% match ({n.method})
                      </li>
                    ))}
                  </ul>
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

              {result.structural_problems.length > 0 && (
                <div className="case-section">
                  <div className="case-label">Blocked — cannot be proposed</div>
                  {result.structural_problems.map((p, i) => (
                    <div key={i} className="objection">
                      {p}
                    </div>
                  ))}
                </div>
              )}

              {result.objections.length > 0 && (
                <div className="case-section">
                  <div className="case-label">Raised in adversarial review</div>
                  <ul className="evidence-notes">
                    {result.objections.map((o, i) => (
                      <li key={i}>{o}</li>
                    ))}
                  </ul>
                </div>
              )}

              {result.related_existing.length > 0 && (
                <div className="case-section">
                  <div className="case-label">Existing steps that may already do this</div>
                  <ul className="evidence-notes">
                    {result.related_existing.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                </div>
              )}

              {result.suggested_agents.length > 0 && (
                <div className="case-section">
                  <div className="case-label">A runnable agent may already cover this</div>
                  <ul className="agent-results-list">
                    {result.suggested_agents.map((a) => (
                      <li key={a.id} className="agent-result-row">
                        <span className="agent-result-name">{a.name}</span>
                        <span className="agent-result-meta">{a.description}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="case-section">
                <div className="case-label">Status</div>
                <p className="case-body">
                  This is a proposal. Nothing has been added to the shared
                  library — an approval is required first, and approved content
                  stays marked as generated from a public submission.
                </p>
              </div>

              {decided ? (
                <>
                  <div className={`stamp ${decided}`}>{decided}</div>
                  {decided === "approved" && <PromoteSection decompositionId={result.id} />}
                </>
              ) : (
                result.safe_to_propose && (
                  <div className="case-section">
                    <div className="case-label">Decision</div>
                    <input
                      className="ask-input"
                      style={{ width: "100%", marginBottom: "0.75rem" }}
                      placeholder="Your name or id"
                      value={approverId}
                      onChange={(e) => setApproverId(e.target.value)}
                    />
                    <div className="ruling-bar">
                      <button
                        className="ruling-button approve"
                        disabled={working}
                        onClick={() => decide("approved")}
                      >
                        Add to library
                      </button>
                      <button
                        className="ruling-button reject"
                        disabled={working}
                        onClick={() => decide("rejected")}
                      >
                        Discard
                      </button>
                    </div>
                  </div>
                )
              )}
            </>
          )}
        </div>
      )}
    </main>
  );
}
