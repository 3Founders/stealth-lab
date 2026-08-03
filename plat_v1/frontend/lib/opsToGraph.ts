/**
 * Convert a plan into the shape WorkflowGraph renders.
 *
 * This is the adapter, not a rewrite. A plan's {nodes, edges} is the same
 * shape as a change set's create-and-link ops — local `ref` strings rather
 * than database ids, because the nodes don't exist yet, which is the whole
 * point of a proposal — so only the reader changed. The mapping, the
 * unknown-ref guard, and the output types are the ones frontend_v2 uses, so
 * the two converge to one function when the backends merge.
 *
 * Kept as a pure function rather than folded into the page so it can be
 * tested directly: the mapping is where a silent mismatch between the
 * backend's plan vocabulary and the frontend's rendering would hide.
 */

import { GraphEdge, GraphNode, Plan, PlanEdge, PlanNode } from "@/lib/api";

type Op = Record<string, unknown>;

/**
 * The original, unchanged, from frontend_v2.
 *
 * plat_v1 never produces ops, so nothing here calls it — it is kept
 * verbatim so that when the two frontends merge this file is a no-op diff
 * rather than a conflict. That is the whole reason the spec says to keep the
 * copy as close to the original as possible.
 */
export function opsToGraph(ops: Op[]): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];

  for (const op of ops) {
    const opType = op.op_type as string;

    if (opType === "create_task_node" || opType === "create_knowledge_node") {
      const ref = op.ref as string;
      if (!ref) continue;
      nodes.push({
        id: ref,
        table: opType === "create_task_node" ? "task_nodes" : "knowledge_nodes",
        label: (op.name as string) ?? ref,
      });
    }
  }

  const known = new Set(nodes.map((n) => n.id));

  ops.forEach((op, i) => {
    if (op.op_type !== "create_edge") return;
    const source = (op.source_ref as string) ?? (op.source_id as string);
    const target = (op.target_ref as string) ?? (op.target_id as string);
    // An edge referencing something outside this change set can't be
    // drawn. The backend rejects those, so reaching here means a
    // mismatch worth not rendering silently wrong.
    if (!source || !target || !known.has(source) || !known.has(target)) return;
    edges.push({
      id: `edge-${i}`,
      source,
      target,
      label: (op.edge_type as string) ?? "PRODUCES",
    });
  });

  return { nodes, edges };
}

export function planToGraph(plan: Plan): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];

  function pushEdges(source: PlanEdge[], prefix: string) {
    source.forEach((edge, i) => {
      edges.push({
        id: `${prefix}-${i}`,
        source: edge.source_ref,
        target: edge.target_ref,
        label: edge.type,
      });
    });
  }

  // A composite's children are drawn alongside it with the DECOMPOSES_TO
  // link made explicit. Collapsing an expansion would show a reviewer one
  // box where six stages are about to run.
  function collect(source: PlanNode[], parent?: string) {
    for (const node of source) {
      if (!node.ref) continue;
      nodes.push({ id: node.ref, table: "task_nodes", label: node.name || node.ref });
      if (parent) {
        edges.push({
          id: `expand-${parent}-${node.ref}`,
          source: parent,
          target: node.ref,
          label: "DECOMPOSES_TO",
        });
      }
      if (node.expansion?.nodes?.length) {
        collect(node.expansion.nodes, node.ref);
        pushEdges(node.expansion.edges ?? [], `${node.ref}-inner`);
      }
    }
  }

  collect(plan.nodes ?? []);
  pushEdges(plan.edges ?? [], "edge");

  // An edge referencing something outside the plan can't be drawn. The
  // typechecker rejects those, so reaching here means a mismatch worth not
  // rendering silently wrong.
  const known = new Set(nodes.map((n) => n.id));
  return { nodes, edges: edges.filter((e) => known.has(e.source) && known.has(e.target)) };
}
