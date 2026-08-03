"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api, DecisionResponse, Proposal } from "@/lib/api";
import { planToGraph } from "@/lib/opsToGraph";
import TraceStrip from "@/components/TraceStrip";
import WorkflowGraph from "@/components/WorkflowGraph";

export default function ProposalDetail() {
  const params = useParams<{ id: string }>();
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deciding, setDeciding] = useState(false);
  const [result, setResult] = useState<DecisionResponse | null>(null);
  const [decidedBy, setDecidedBy] = useState("");

  const load = useCallback(async () => {
    try {
      setProposal(await api.getProposal(params.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load this proposal.");
    }
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  async function decide(decision: "approve" | "reject") {
    if (!decidedBy.trim()) {
      alert("Enter who's making this decision first.");
      return;
    }
    setDeciding(true);
    try {
      setResult(await api.decide(params.id, decision, decidedBy));
    } catch (e) {
      alert(e instanceof Error ? e.message : "Could not record the decision.");
    } finally {
      setDeciding(false);
    }
  }

  if (error) {
    return (
      <main className="shell">
        <Link href="/approvals" className="back-link">
          ← back to proposals
        </Link>
        <div className="empty-state">{error}</div>
      </main>
    );
  }

  if (!proposal) {
    return (
      <main className="shell">
        <p style={{ color: "var(--ink-text-dim)" }}>Loading…</p>
      </main>
    );
  }

  const graph = planToGraph(proposal.plan);
  const reused = proposal.plan.nodes?.filter((n) => n.existing_task_id) ?? [];

  return (
    <main className="shell">
      <Link href="/approvals" className="back-link">
        ← back to proposals
      </Link>

      <div className="case-file">
        {/* The structural verdict comes before the plan, deliberately. */}
        {!proposal.typecheck?.ok && (
          <div className="tier-banner simulated">
            This plan failed typecheck and cannot be approved
            <span className="tier-detail">
              Structural failure is not something to weigh up — a plan whose dataflow
              doesn&apos;t close cannot run, however sensible it reads. Fix the plan or
              reject it.
            </span>
          </div>
        )}

        <h1 className="case-heading">{proposal.request_text}</h1>

        <div className="case-section">
          <div className="case-label">Plan</div>
          <div className="metrics-row">
            <div className="metric">
              <span className="metric-value">{proposal.plan.nodes?.length ?? 0}</span>
              <span className="metric-label">steps</span>
            </div>
            <div className="metric">
              <span className="metric-value">{reused.length}</span>
              <span className="metric-label">reuse existing tasks</span>
            </div>
            <div className="metric">
              <span className="metric-value">
                {proposal.plan.external_inputs?.length ?? 0}
              </span>
              <span className="metric-label">caller-supplied inputs</span>
            </div>
          </div>
        </div>

        {graph.nodes.length > 0 && (
          <div className="case-section">
            <div className="case-label">Proposed workflow</div>
            <WorkflowGraph nodes={graph.nodes} edges={graph.edges} center={graph.nodes[0].id} />
          </div>
        )}

        {/* Requirement 2: a plain list of typecheck problems. */}
        <div className="case-section">
          <div className="case-label">Typecheck</div>
          {proposal.typecheck?.ok ? (
            <p className="case-body">
              No structural problems. Dataflow closes, types line up across every edge, the
              graph is acyclic, and every leaf has something that can run it.
            </p>
          ) : (
            <ul className="evidence-notes">
              {(proposal.typecheck?.messages ?? []).map((message, i) => (
                <li key={i}>{message}</li>
              ))}
            </ul>
          )}
        </div>

        {proposal.plan.reasoning && (
          <div className="case-section">
            <div className="case-label">Reasoning</div>
            <p className="case-body">{proposal.plan.reasoning}</p>
          </div>
        )}

        <div className="case-section">
          <div className="case-label">Steps</div>
          {(proposal.plan.nodes ?? []).map((node) => (
            <div key={node.ref} className="transcript-turn">
              <div>
                <div className="transcript-speaker">
                  {node.name}
                  {node.existing_task_id && <> · reuses an existing task</>}
                </div>
                <span className="transcript-action">{node.kind}</span>
              </div>
              <div className="transcript-content">{node.description}</div>
              <div className="docket-meta">
                <span>in: {Object.keys((node.input_schema?.properties as object) ?? {}).join(", ") || "—"}</span>
                <span>out: {Object.keys((node.output_schema?.properties as object) ?? {}).join(", ") || "—"}</span>
                <span>
                  {node.existing_task_id
                    ? "inherited implementations"
                    : `${node.implementations?.length ?? 0} proposed implementation(s)`}
                </span>
              </div>
            </div>
          ))}
        </div>

        {result ? (
          <>
            <div className={`stamp ${result.decision}`}>{result.decision}</div>
            {result.stages && result.stages.length > 0 && (
              <div className="case-section">
                <div className="case-label">Run — per-stage trace</div>
                <TraceStrip stages={result.stages} />
                {result.error && <div className="objection">{result.error}</div>}
              </div>
            )}
          </>
        ) : proposal.status !== "pending" ? (
          <div className={`stamp ${proposal.status}`}>{proposal.status}</div>
        ) : (
          <div className="case-section">
            <div className="case-label">Your decision</div>
            <input
              className="ask-input"
              style={{ width: "100%", marginBottom: "0.75rem" }}
              placeholder="Your name or id"
              value={decidedBy}
              onChange={(e) => setDecidedBy(e.target.value)}
            />
            <div className="ruling-bar">
              {/* No approve button on a plan that failed typecheck. Offering
                  one would imply someone had already decided it was a
                  judgement call. */}
              {proposal.approvable && (
                <button
                  className="ruling-button approve"
                  disabled={deciding}
                  onClick={() => decide("approve")}
                >
                  {deciding ? "Running…" : "Approve and run"}
                </button>
              )}
              <button
                className="ruling-button reject"
                disabled={deciding}
                onClick={() => decide("reject")}
              >
                Reject
              </button>
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
