"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { agentStoreApi, AgentSearchResult, PendingAgent } from "@/lib/api";

const SOURCE_LABELS: Record<string, string> = {
  internal: "Built in",
  graph_derived: "Promoted from a decomposition",
  user_submitted: "Submitted",
  external_marketplace: "From an external marketplace",
};

const CODE_SOURCED = new Set(["user_submitted", "external_marketplace"]);

// Only the medical report extraction agent has a real run page today.
// Everything else in the store is discoverable now, runnable later --
// stage 3 is search/browse, a generic runner for arbitrary agent types
// is out of scope here.
const KNOWN_RUN_PAGES: Record<string, string> = {
  "Medical Report Extraction": "/agents/medical-report-extraction",
};

type View = "browse" | "submit" | "pending";

function SubmitAgentForm({ onSubmitted }: { onSubmitted: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [source, setSource] = useState<"user_submitted" | "external_marketplace">("user_submitted");
  const [requestedInput, setRequestedInput] = useState("");
  const [requestedOutput, setRequestedOutput] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  const [code, setCode] = useState("");
  const [submittedBy, setSubmittedBy] = useState("");
  const [working, setWorking] = useState(false);
  const [result, setResult] = useState<{ reviewState: string; passed: boolean; notes: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!name.trim() || !description.trim() || !submittedBy.trim()) {
      alert("Name, description, and your name/id are all required.");
      return;
    }
    setWorking(true);
    setError(null);
    try {
      const detail =
        source === "user_submitted"
          ? { requested_input: requestedInput, requested_output: requestedOutput }
          : { repo_url: repoUrl, code };
      const r = await agentStoreApi.submit(name, description, source, detail, submittedBy);
      setResult({ reviewState: r.review_state, passed: r.passed_review, notes: r.reviewer_notes });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not submit this agent.");
    } finally {
      setWorking(false);
    }
  }

  if (result) {
    return (
      <div className="case-section">
        <div className="case-label">Submission reviewed</div>
        <p className="case-body">
          Review {result.passed ? "passed" : "did not pass"}
          {result.notes && ` — ${result.notes}`}. Current status:{" "}
          <strong>{result.reviewState}</strong>.
          {result.reviewState === "pending_human_approval" &&
            " Find it under Pending Review to make the final call."}
        </p>
        <button className="ask-button" onClick={onSubmitted}>
          Submit another
        </button>
      </div>
    );
  }

  return (
    <div className="case-section">
      <div className="case-label">Submit an agent</div>
      <p className="case-body">
        A user-submitted request is a structured description of what you
        want, not code. 
      </p>

      <input className="ask-input" style={{ width: "100%", marginBottom: "0.5rem" }}
        placeholder="Agent name" value={name} onChange={(e) => setName(e.target.value)} />
      <textarea className="ask-input" style={{ width: "100%", minHeight: "60px", marginBottom: "0.5rem" }}
        placeholder="What does this agent do?" value={description}
        onChange={(e) => setDescription(e.target.value)} />

      <div style={{ marginBottom: "0.75rem" }}>
        <label style={{ marginRight: "1rem", fontFamily: "var(--font-mono)", fontSize: "0.8rem" }}>
          <input type="radio" checked={source === "user_submitted"}
            onChange={() => setSource("user_submitted")} /> A request (no code)
        </label>
        <label style={{ fontFamily: "var(--font-mono)", fontSize: "0.8rem" }}>
          <input type="radio" checked={source === "external_marketplace"}
            onChange={() => setSource("external_marketplace")} /> From a marketplace (has code)
        </label>
      </div>

      {source === "user_submitted" ? (
        <>
          <input className="ask-input" style={{ width: "100%", marginBottom: "0.5rem" }}
            placeholder="What input would you give it?" value={requestedInput}
            onChange={(e) => setRequestedInput(e.target.value)} />
          <input className="ask-input" style={{ width: "100%", marginBottom: "0.5rem" }}
            placeholder="What output do you want back?" value={requestedOutput}
            onChange={(e) => setRequestedOutput(e.target.value)} />
        </>
      ) : (
        <>
          <input className="ask-input" style={{ width: "100%", marginBottom: "0.5rem" }}
            placeholder="Repository URL" value={repoUrl} onChange={(e) => setRepoUrl(e.target.value)} />
          <textarea className="ask-input"
            style={{ width: "100%", minHeight: "120px", marginBottom: "0.5rem", fontFamily: "var(--font-mono)" }}
            placeholder="Paste the actual source code here" value={code}
            onChange={(e) => setCode(e.target.value)} />
        </>
      )}

      <input className="ask-input" style={{ width: "100%", marginBottom: "0.75rem" }}
        placeholder="Your name or id" value={submittedBy} onChange={(e) => setSubmittedBy(e.target.value)} />

      {error && <p className="case-body" style={{ color: "var(--fail)" }}>{error}</p>}
      <button className="ask-button" disabled={working} onClick={submit}>
        {working ? "Running independent review…" : "Submit for review"}
      </button>
    </div>
  );
}

function PendingReviewCard({ agent, onDecided }: { agent: PendingAgent; onDecided: () => void }) {
  const [actor, setActor] = useState("");
  const [acknowledge, setAcknowledge] = useState(false);
  const [working, setWorking] = useState(false);
  const [decided, setDecided] = useState<{ reviewState: string; runnable: boolean } | null>(null);
  const isCodeSourced = CODE_SOURCED.has(agent.source);

  async function decide(decision: "approved" | "rejected") {
    if (!actor.trim()) {
      alert("Enter your name first.");
      return;
    }
    setWorking(true);
    try {
      const r = await agentStoreApi.decideAgent(agent.id, decision, actor, acknowledge);
      setDecided({ reviewState: r.review_state, runnable: r.runnable });
      onDecided();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Could not record this decision.");
    } finally {
      setWorking(false);
    }
  }

  return (
    <li className="agent-store-card">
      <div className="agent-store-card-header">
        <span className="agent-store-name">{agent.name}</span>
        <span className="agent-store-badge not-runnable">
          {SOURCE_LABELS[agent.source] ?? agent.source}
        </span>
      </div>
      <p className="agent-store-description">{agent.description}</p>

      {decided ? (
        <div className={`stamp ${decided.reviewState}`}>
          {decided.reviewState}
          {decided.reviewState === "approved" && (
            <span style={{ marginLeft: "0.75rem", fontSize: "0.75rem" }}>
              {decided.runnable ? "runnable" : "not yet runnable"}
            </span>
          )}
        </div>
      ) : (
        <>
          {isCodeSourced && (
            <p className="case-body" style={{ fontSize: "0.82rem" }}>
              This is code-sourced content. A sandbox check exists with real,
              verified network and resource isolation, plus a real (but
              partial) filesystem restriction — see AGENT_STORE_MECHANISM.md
              for exactly what's covered and what isn&rsquo;t. Non-root
              production behavior is still unconfirmed. Approving without
              acknowledging this leaves it correctly not runnable.
            </p>
          )}
          <input className="ask-input" style={{ width: "100%", marginBottom: "0.5rem" }}
            placeholder="Your name or id" value={actor} onChange={(e) => setActor(e.target.value)} />
          {isCodeSourced && (
            <label style={{ display: "block", marginBottom: "0.5rem", fontFamily: "var(--font-mono)", fontSize: "0.78rem" }}>
              <input type="checkbox" checked={acknowledge}
                onChange={(e) => setAcknowledge(e.target.checked)} />
              {" "}I acknowledge the sandbox's real, stated limitations and want a
              clean sandbox run to count toward runnable
            </label>
          )}
          <div className="ruling-bar">
            <button className="ruling-button approve" disabled={working} onClick={() => decide("approved")}>
              Approve
            </button>
            <button className="ruling-button reject" disabled={working} onClick={() => decide("rejected")}>
              Reject
            </button>
          </div>
        </>
      )}
    </li>
  );
}

export default function AgentStorePage() {
  const [view, setView] = useState<View>("browse");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<AgentSearchResult[] | null>(null);
  const [pending, setPending] = useState<PendingAgent[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load(q?: string) {
    setLoading(true);
    setError(null);
    try {
      const response = await agentStoreApi.browseOrSearch(q);
      setResults(response.results);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Could not reach the agent store.",
      );
    } finally {
      setLoading(false);
    }
  }

  async function loadPending() {
    setLoading(true);
    setError(null);
    try {
      setPending(await agentStoreApi.pending());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load pending agents.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (view === "browse") load();
    if (view === "pending") loadPending();
  }, [view]);

  return (
    <main className="shell">
      <div className="masthead">
        <div>
          <div className="masthead-title">Agent Store</div>
          <div className="masthead-sub">
            search for a reviewed, ready-to-use agent
          </div>
        </div>
      </div>

      <div className="nav-tabs">
        <Link href="/workbench" className="nav-tab">
          Workbench
        </Link>
        <Link href="/approvals" className="nav-tab">
          Docket
        </Link>
        <Link href="/archive" className="nav-tab">
          Archive
        </Link>
        <Link href="/agents" className="nav-tab active">
          Agents
        </Link>
      </div>

      <div className="nav-tabs" style={{ marginBottom: "1.5rem" }}>
        <button
          className={`nav-tab ${view === "browse" ? "active" : ""}`}
          style={{ background: "none", border: "none", cursor: "pointer" }}
          onClick={() => setView("browse")}
        >
          Browse
        </button>
        <button
          className={`nav-tab ${view === "submit" ? "active" : ""}`}
          style={{ background: "none", border: "none", cursor: "pointer" }}
          onClick={() => setView("submit")}
        >
          Submit an agent
        </button>
        <button
          className={`nav-tab ${view === "pending" ? "active" : ""}`}
          style={{ background: "none", border: "none", cursor: "pointer" }}
          onClick={() => setView("pending")}
        >
          Pending review
        </button>
      </div>

      {view === "submit" && <SubmitAgentForm onSubmitted={() => setView("pending")} />}

      {view === "pending" && (
        <>
          {error && <div className="empty-state">{error}</div>}
          {pending && pending.length === 0 && !error && (
            <div className="empty-state">Nothing is currently awaiting a decision.</div>
          )}
          {pending && pending.length > 0 && (
            <ul className="agent-store-list">
              {pending.map((agent) => (
                <PendingReviewCard key={agent.id} agent={agent} onDecided={loadPending} />
              ))}
            </ul>
          )}
        </>
      )}

      {view === "browse" && (
        <>
          <div className="ask-bar">
            <input
              className="ask-input"
              value={query}
              placeholder="e.g. extract data from a lab report"
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") load(query);
              }}
            />
            <button className="ask-button" onClick={() => load(query)} disabled={loading}>
              {loading ? "Searching…" : "Search"}
            </button>
            {query && (
              <button
                className="ask-button"
                style={{ background: "transparent", border: "1px solid var(--rule)", color: "var(--ink-text-dim)" }}
                onClick={() => {
                  setQuery("");
                  load();
                }}
              >
                Clear
              </button>
            )}
          </div>

          {error && <div className="empty-state">{error}</div>}

          {results && results.length === 0 && !error && (
            <div className="empty-state">
              {query
                ? "No approved agents match that search."
                : "No agents have been approved into the store yet."}
            </div>
          )}

          {results && results.length > 0 && (
            <ul className="agent-store-list">
              {results.map((agent) => {
                const runPage = KNOWN_RUN_PAGES[agent.name];
                return (
                  <li key={agent.id} className="agent-store-card">
                    <div className="agent-store-card-header">
                      <span className="agent-store-name">{agent.name}</span>
                      <span
                        className={`agent-store-badge ${agent.runnable ? "runnable" : "not-runnable"}`}
                      >
                        {agent.runnable ? "Runnable" : "Not yet runnable"}
                      </span>
                    </div>
                    <p className="agent-store-description">{agent.description}</p>
                    <div className="agent-store-meta">
                      <span>{SOURCE_LABELS[agent.source] ?? agent.source}</span>
                      {runPage ? (
                        <Link href={runPage} className="agent-download-link">
                          Open
                        </Link>
                      ) : (
                        <span className="agent-store-meta-dim">
                          No run page for this agent type yet
                        </span>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
    </main>
  );
}
