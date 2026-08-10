"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  GraphEdge,
  GraphNode,
  GraphOverview,
  GraphOverviewNode,
} from "@/lib/api";
import WorkflowGraph from "@/components/WorkflowGraph";

/**
 * The stored graph, as stored — every visible node and edge in Postgres,
 * not a proposal and not a projection.
 *
 * Two views over the same data. The overview (GET /v1/graph) is the whole
 * graph; focusing a node switches to GET /v1/graph/{id}, the same
 * traversal retrieval uses, so what you see here is what the backend
 * actually walks rather than a client-side approximation of it.
 */

type Filter = "all" | "task_nodes" | "knowledge_nodes";

type Focus = {
  nodeId: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
};

const DEPTHS = [1, 2, 3, 4];

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toISOString().slice(0, 10);
}

export default function VisualizePage() {
  const [overview, setOverview] = useState<GraphOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [includeSuperseded, setIncludeSuperseded] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");

  const [focus, setFocus] = useState<Focus | null>(null);
  const [depth, setDepth] = useState(2);
  const [focusBusy, setFocusBusy] = useState(false);
  const [focusError, setFocusError] = useState<string | null>(null);

  const load = useCallback(async (superseded: boolean) => {
    setLoading(true);
    setError(null);
    try {
      setOverview(await api.getGraph({ includeSuperseded: superseded }));
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Could not reach the graph service.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(includeSuperseded);
  }, [load, includeSuperseded]);

  const focusOn = useCallback(async (nodeId: string, atDepth: number) => {
    setFocusBusy(true);
    setFocusError(null);
    try {
      const sub = await api.getSubgraph(nodeId, atDepth);
      setFocus({ nodeId, nodes: sub.nodes, edges: sub.edges });
    } catch (e) {
      setFocusError(
        e instanceof Error ? e.message : "Could not load that node's neighbourhood.",
      );
    } finally {
      setFocusBusy(false);
    }
  }, []);

  function selectNode(nodeId: string) {
    if (focus?.nodeId === nodeId) {
      setFocus(null);
      setFocusError(null);
      return;
    }
    // GET /v1/graph/{id} resolves its centre with a "still current" check
    // and 404s on anything superseded, so once history is shown these rows
    // are selectable but not focusable. Saying which of the two it is
    // beats spending a round trip to render "something went wrong".
    const node = byId.get(nodeId);
    if (node && !node.current) {
      setFocus(null);
      setFocusError(
        `“${node.label}” has been superseded, so it has no current ` +
          `neighbourhood to walk. Select the node that replaced it to see ` +
          `where those links went.`,
      );
      return;
    }
    focusOn(nodeId, depth);
  }

  function changeDepth(next: number) {
    setDepth(next);
    if (focus) focusOn(focus.nodeId, next);
  }

  const nodes = overview?.nodes ?? [];
  const edges = overview?.edges ?? [];

  // Filtering happens client-side: the whole graph is already loaded, and
  // a round trip to hide half of it would only add latency.
  const visible = useMemo(
    () => (filter === "all" ? nodes : nodes.filter((n) => n.table === filter)),
    [nodes, filter],
  );

  const byId = useMemo(
    () => new Map(nodes.map((n) => [n.id, n])),
    [nodes],
  );

  /**
   * Reconcile the focused view against the overview.
   *
   * GET /v1/graph/{id} walks *edges* by validity but labels the nodes it
   * lands on without checking whether they are still current, so focusing
   * can surface superseded nodes even while the overview is hiding them —
   * nodes drawn in the diagram that have no row in the list below it. The
   * subgraph response carries no `current` flag of its own, so the
   * overview is the only reference available here: it is exactly the set
   * of nodes this page is currently willing to show.
   *
   * That inference only holds while the overview is complete. Once it is
   * truncated, a node missing from it may have been cut by the limit
   * rather than superseded, and dropping those would hide live parts of
   * the neighbourhood — a worse failure than showing a stale one. In that
   * case the focused view is left exactly as the backend sent it.
   */
  const reconciled = !includeSuperseded && !overview?.truncated;

  const focusView = useMemo(() => {
    if (!focus) return null;
    if (!reconciled) {
      return { nodes: focus.nodes, edges: focus.edges, hiddenNodes: 0, hiddenEdges: 0 };
    }
    const keptNodes = focus.nodes.filter((n) => byId.has(n.id));
    const kept = new Set(keptNodes.map((n) => n.id));
    const keptEdges = focus.edges.filter(
      (e) => kept.has(e.source) && kept.has(e.target),
    );
    return {
      nodes: keptNodes,
      edges: keptEdges,
      hiddenNodes: focus.nodes.length - keptNodes.length,
      hiddenEdges: focus.edges.length - keptEdges.length,
    };
  }, [focus, byId, reconciled]);

  // Both arrays are memoised because WorkflowGraph re-runs mermaid whenever
  // its node/edge props change identity — a fresh array each render would
  // re-layout the diagram on every keystroke elsewhere on the page.
  const drawnNodes = useMemo<GraphNode[]>(
    () => (focusView ? focusView.nodes : visible),
    [focusView, visible],
  );

  const drawnEdges = useMemo<GraphEdge[]>(() => {
    if (focusView) return focusView.edges;
    const shown = new Set(visible.map((n) => n.id));
    return edges.filter((e) => shown.has(e.source) && shown.has(e.target));
  }, [focusView, visible, edges]);

  const selected: GraphOverviewNode | undefined = focus
    ? byId.get(focus.nodeId)
    : undefined;

  const links = useMemo(() => {
    if (!focus) return [];
    return edges
      .filter((e) => e.source === focus.nodeId || e.target === focus.nodeId)
      .map((e) => ({
        id: e.id,
        outgoing: e.source === focus.nodeId,
        label: e.label,
        other: byId.get(e.source === focus.nodeId ? e.target : e.source),
      }));
  }, [focus, edges, byId]);

  const taskCount = nodes.filter((n) => n.table === "task_nodes").length;
  const knowledgeCount = nodes.length - taskCount;

  return (
    <main className="shell">
      <div className="masthead">
        <div>
          <div className="masthead-title">Visualize</div>
          <div className="masthead-sub">the knowledge graph as stored</div>
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
        <Link href="/tasks" className="nav-tab">
          Tasks
        </Link>
        <Link href="/visualize" className="nav-tab active">
          Visualize
        </Link>
      </div>

      {loading && <div className="empty-state">Reading the graph&hellip;</div>}

      {error && !loading && (
        <div className="empty-state">
          {error}
          <br />
          <button
            className="scan-button"
            style={{ marginTop: "1rem" }}
            onClick={() => load(includeSuperseded)}
          >
            Retry
          </button>
        </div>
      )}

      {overview && !loading && !error && nodes.length === 0 && (
        <div className="empty-state">
          Nothing is stored in the graph yet.
          <br />
          Decompose a problem in the Workbench and approve it to put the first
          steps here.
        </div>
      )}

      {overview && !loading && !error && nodes.length > 0 && (
        <>
          <div className="graph-stats">
            <span>
              {taskCount} task step{taskCount === 1 ? "" : "s"}
            </span>
            <span>
              {knowledgeCount} knowledge node{knowledgeCount === 1 ? "" : "s"}
            </span>
            <span>
              {edges.length} link{edges.length === 1 ? "" : "s"}
            </span>
            {overview.truncated && (
              <span className="enquiry-warning">
                showing {nodes.length} of {overview.total_nodes} nodes
              </span>
            )}
            {overview.omitted_edges > 0 && (
              <span className="enquiry-warning">
                {overview.omitted_edges} link
                {overview.omitted_edges === 1 ? "" : "s"} hidden — an endpoint
                isn&rsquo;t visible here
              </span>
            )}
          </div>

          <div className="graph-toolbar">
            {(
              [
                ["all", "Everything"],
                ["task_nodes", "Task steps"],
                ["knowledge_nodes", "Knowledge"],
              ] as [Filter, string][]
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                className={`graph-chip ${filter === value ? "active" : ""}`}
                disabled={focus !== null}
                onClick={() => setFilter(value)}
              >
                {label}
              </button>
            ))}

            <button
              type="button"
              className={`graph-chip ${includeSuperseded ? "active" : ""}`}
              onClick={() => setIncludeSuperseded((v) => !v)}
            >
              {includeSuperseded ? "History shown" : "Show superseded"}
            </button>

            {focus && (
              <>
                <span className="graph-toolbar-label">depth</span>
                {DEPTHS.map((d) => (
                  <button
                    key={d}
                    type="button"
                    className={`graph-chip ${depth === d ? "active" : ""}`}
                    disabled={focusBusy}
                    onClick={() => changeDepth(d)}
                  >
                    {d}
                  </button>
                ))}
                <button
                  type="button"
                  className="graph-chip"
                  onClick={() => {
                    setFocus(null);
                    setFocusError(null);
                  }}
                >
                  Back to whole graph
                </button>
              </>
            )}
          </div>

          {focusError && (
            <p className="case-body" style={{ color: "var(--fail)" }}>
              {focusError}
            </p>
          )}

          <div className="case-file">
            <div className="case-section">
              <div className="case-label">
                {focus
                  ? `Around ${selected?.label ?? "this node"} — ${depth} hop${
                      depth === 1 ? "" : "s"
                    }`
                  : "The whole graph"}
              </div>

              {focusView && focusView.hiddenNodes > 0 && (
                <p className="case-body" style={{ marginTop: 0 }}>
                  <span className="enquiry-warning">
                    {focusView.hiddenNodes} superseded node
                    {focusView.hiddenNodes === 1 ? "" : "s"} hidden
                    {focusView.hiddenEdges > 0 &&
                      `, with ${focusView.hiddenEdges} link${
                        focusView.hiddenEdges === 1 ? "" : "s"
                      }`}
                  </span>{" "}
                  — this node&rsquo;s history sits outside the current graph.
                  Turn on &ldquo;Show superseded&rdquo; to include it.
                </p>
              )}

              {focusBusy ? (
                <p className="case-body">Walking the graph&hellip;</p>
              ) : (
                <WorkflowGraph
                  nodes={drawnNodes}
                  edges={drawnEdges}
                  center={focus?.nodeId ?? ""}
                />
              )}

              <div className="graph-legend">
                <span>
                  <span className="graph-legend-swatch task" /> task step
                </span>
                <span>
                  <span className="graph-legend-swatch knowledge" /> knowledge
                </span>
                <span>drag to pan, scroll to zoom</span>
              </div>
            </div>

            {focus && selected && (
              <div className="case-section">
                <div className="case-label">
                  {selected.node_type} · {selected.provenance.replace(/_/g, " ")}
                  {!selected.current && " · superseded"}
                </div>
                <p className="case-body" style={{ marginTop: 0 }}>
                  <strong>{selected.label}</strong>
                  {selected.description ? ` — ${selected.description}` : ""}
                </p>
                {links.length > 0 ? (
                  <ul className="evidence-notes">
                    {links.map((l) => (
                      <li key={l.id}>
                        {l.outgoing ? "→" : "←"} {l.label.toLowerCase()}{" "}
                        {l.other?.label ?? "a node not visible here"}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="case-body">Nothing links to this node yet.</p>
                )}
              </div>
            )}

            <div className="case-section">
              <div className="case-label">
                {visible.length} node{visible.length === 1 ? "" : "s"} — select
                one to see its neighbourhood
              </div>
              <div>
                {visible.map((n) => (
                  <button
                    key={n.id}
                    type="button"
                    className={`graph-node-row ${
                      focus?.nodeId === n.id ? "selected" : ""
                    }`}
                    onClick={() => selectNode(n.id)}
                  >
                    <span className="graph-node-kind">
                      {n.table === "task_nodes" ? "task" : n.node_type}
                    </span>
                    <span className="graph-node-body">
                      <span className="graph-node-name">
                        {n.label}
                        {!n.current && (
                          <span className="graph-node-superseded">
                            superseded
                          </span>
                        )}
                      </span>
                      {n.description && (
                        <span className="graph-node-desc">{n.description}</span>
                      )}
                    </span>
                    <span className="graph-node-meta">
                      {formatDate(n.t_created)}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </>
      )}
    </main>
  );
}
