"""
Persist a subgoal's own failure reason as a real, retrievable
knowledge_node -- the other half of method_library.py's survivorship
bias fix. persist_plan() only ever stores successes; when a subgoal
fails, the model has ALREADY written a precise diagnosis via
subgoal_failed(reason) (see htn_agent.py's Node.note / last_evidence).
That text is thrown away today. This stores it instead of the raw
trajectory: a 20-30 call transcript is mostly list_dir/search noise and
embeds badly (it matches on incidental tokens, not on why the attempt
failed); the model's own distilled sentence is smaller and semantically
retrievable.

WHY NOT KnowledgeUpdater. Its apply() dispatches only
InvalidateEdgeOp/CreateEdgeOp/UpdateTaskNodeOp/UpdateKnowledgeNodeOp --
CreateKnowledgeNodeOp is not one of the cases and falls through to
"unknown op type". The other path, apply_generated(), DOES create
nodes, but it exists specifically for untrusted public input: it forces
provenance='public_generated', and its capability check
(ChangeSet.validate_generative) explicitly forbids a generated edge
from attaching to an EXISTING node -- exactly what a failure record
needs to do, since it must link to the SWE-bench instance's own
task_node, not a node created in the same change set. Neither method
fits an internal, trusted write of one node and one edge, so this
follows graph_ingest.py's own established convention for this same
node family (task_nodes/knowledge_nodes/edges, one instance at a time):
direct, explicit INSERTs in one transaction.

CONTAMINATION -- read this before adding a new call site or a new
consumer of failure_mode nodes.

  1. Every row carries `properties.instance_id` (node AND edge), which
     is what makes graph_memory.hold_out() invalidate these exactly
     like the ingested corpus -- ZERO changes needed there; hold_out's
     WHERE clauses key on properties->>'instance_id', not on
     created_by, so they already cover any node/edge tagged this way.

  2. graph_memory._hydrate() joins task_nodes to knowledge_nodes on
     `properties->>'instance_id' = skill_ref` with no node_type filter
     and no LIMIT-safe ordering. Before this module existed, at most
     one live knowledge_node ever matched that join per instance
     (the ingested code_location row), so the ambiguity was latent and
     harmless. Adding a SECOND live knowledge_node sharing the same
     instance_id breaks that invariant: an unfiltered join could pick
     the failure_mode row instead of the code_location row for
     rendering, silently serving a "reason"/"last_evidence" blob where
     a patch was expected. `_hydrate` has been given an explicit
     `k.node_type = 'code_location'` guard because of this module --
     do not remove it without re-auditing every join in graph_memory.py
     for the same one-row assumption.

  3. Cross-instance retrieval of these IS the intended feature (a later,
     different instance benefiting from what an earlier one learned),
     but there is no rendering support today for a node shaped like
     this (no patch, no files) inside graph_memory.render_context's
     SEARCH/REPLACE pipeline. graph_memory.retrieve() therefore excludes
     created_by=CREATED_BY by default; pass include_failure_modes=True
     only once a caller actually knows how to render one.
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg

CREATED_BY = "htn_failure_mode"


async def capture_failure(
    pool: asyncpg.Pool,
    *,
    instance_id: str,
    repo: str,
    arm: str,
    model: str,
    failing_goal: str,
    reason: str,
    last_evidence: str = "",
    stop_reason: str = "",
) -> Optional[str]:
    """
    Write one failure_mode knowledge_node plus an edge to the instance's
    own task_node (task_nodes.skill_ref == instance_id, the same key
    graph_ingest.py used to create it).

    Returns the new node's id, or None if no live task_node exists for
    this instance -- nothing to link to, so this is a silent no-op, not
    an error. This function is best-effort telemetry: it must never be
    able to fail the run it is attached to, which is also why callers
    invoke it after the real evaluation result is already durable (see
    run_graph_experiment.py's debate-curation call site for the same
    discipline, in its own try/except).
    """
    name = f"failure: {failing_goal[:180]}"
    properties: dict[str, Any] = {
        "instance_id": instance_id,
        "repo": repo,
        "arm": arm,
        "model": model,
        "failing_goal": failing_goal,
        "reason": reason,
        "last_evidence": last_evidence,
        "stop_reason": stop_reason,
    }
    async with pool.acquire() as conn:
        async with conn.transaction():
            task_id = await conn.fetchval(
                "SELECT id FROM task_nodes WHERE skill_ref = $1 AND t_invalid IS NULL",
                instance_id,
            )
            if task_id is None:
                return None
            node_id = await conn.fetchval(
                "INSERT INTO knowledge_nodes "
                "(node_type, name, properties, created_by, provenance) "
                "VALUES ('failure_mode', $1, $2, $3, 'company_ingested') "
                "RETURNING id",
                name[:200], properties, CREATED_BY,
            )
            # Mirrors graph_ingest.py's OWNS/RESOLVED_AT idiom for the same
            # task_node<->knowledge_node relationship, just a different
            # custom label for a different outcome.
            await conn.execute(
                "INSERT INTO edges (edge_type, custom_edge_type, "
                " source_id, source_table, target_id, target_table, "
                " properties, created_by, provenance) "
                "VALUES ('OWNS', 'FAILURE_MODE', $1, 'task_nodes', "
                " $2, 'knowledge_nodes', $3, $4, 'company_ingested')",
                task_id, node_id, {"instance_id": instance_id}, CREATED_BY,
            )
    return str(node_id)
