"""
Bridge from a SWE-bench Pro instance to the backend's REAL task-decomposition
pipeline (app/services/decomposition.py), and back into the DAG shape
htn_agent.py's planner already accepts.

WHY THIS FILE EXISTS. htn_agent.py's own planner is a single, standalone LLM
call -- it never touches decomposition.py, dedup.py, subtask_reuse.py or
precondition_gate.py, only graph_memory.py's retrieval bridge reaches the
backend at all (retrieval.py, hierarchy.py). One call to
DecompositionService.decompose() internally exercises sanitize/scan ->
_try_hierarchical_match (hierarchy.py, which itself calls
precondition_gate.postconditions_compatible) -> find_reusable_nodes
(reuse_detection.py) -> the generator LLM call -> dedupe_changeset_ops
(dedup.py) -> resolve_subtask_reuse (subtask_reuse.py) ->
validate_generative() (change.py's capability boundary) -> the adversarial
critic. That is effectively the whole backend list, in one call -- see
symbolic_htn_agent.py for how the result is threaded into an HTN run via the
existing `_seed_plan` hook.

WHY A LOCAL PanelAgent RATHER THAN OpenAICompatAgent FROM app.debate.panel.
PanelAgent.respond() returns text only, no token usage -- fine for the
debate subsystem, which doesn't measure cost per call. This experiment's
primary measured quantity IS token cost, so UsageTrackingOpenAIAgent below
duplicates OpenAICompatAgent's ~10 lines locally rather than editing shared
`app/debate/panel.py` for an experiment-specific need.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.config import settings  # noqa: E402
from app.models.change import ChangeSet, CreateEdgeOp, CreateKnowledgeNodeOp, CreateTaskNodeOp  # noqa: E402
from app.services.decomposition import Decomposition, DecompositionService  # noqa: E402
from app.services.retrieval import HybridRetriever  # noqa: E402

from agent import Usage  # noqa: E402

# The SWE-bench-specific framing prepended to the issue text. Lives here,
# inside the untrusted `problem` argument that gets wrap_untrusted()-fenced
# like any other input, rather than editing decomposition.py's shared
# DECOMPOSE_SYSTEM prompt -- this experiment wants file-level executability,
# other callers of DecompositionService do not.
_ISSUE_FRAMING = (
    "Break this repository bug into concrete steps, each naming the exact "
    "file or symbol it touches:\n\n"
)


def changeset_to_subgoals(change_set: ChangeSet) -> tuple[list[dict], dict]:
    """
    Pure, synchronous, zero I/O. Maps each CreateTaskNodeOp (in declaration
    order) to a subgoal dict; a PRODUCES edge between two declared task refs
    becomes a `deps` entry on the target (PRODUCES means the target consumes
    the source's output). CreateKnowledgeNodeOps are facts, not actions, and
    are dropped -- counted in the returned diagnostics rather than silently
    lost. Output is exactly the shape HTNAgent.parse_dag() already accepts.
    """
    task_ops = [op for op in change_set.ops if isinstance(op, CreateTaskNodeOp)]
    knowledge_ops = [op for op in change_set.ops if isinstance(op, CreateKnowledgeNodeOp)]
    edge_ops = [op for op in change_set.ops if isinstance(op, CreateEdgeOp)]

    ref_to_id = {op.ref: i for i, op in enumerate(task_ops, 1)}
    subgoals = []
    for i, op in enumerate(task_ops, 1):
        goal = f"{op.name}: {op.description}" if op.description else op.name
        subgoals.append({"id": i, "goal": goal.strip(), "deps": []})
    by_id = {s["id"]: s for s in subgoals}

    produces_applied = 0
    for edge in edge_ops:
        if edge.edge_type != "PRODUCES" or not edge.source_ref or not edge.target_ref:
            continue
        src_id = ref_to_id.get(edge.source_ref)
        tgt_id = ref_to_id.get(edge.target_ref)
        if src_id is None or tgt_id is None or src_id == tgt_id:
            continue
        target = by_id[tgt_id]
        if src_id not in target["deps"]:
            target["deps"].append(src_id)
            produces_applied += 1

    diagnostics = {
        "task_ops": len(task_ops),
        "knowledge_ops_dropped": len(knowledge_ops),
        "edges_total": len(edge_ops),
        "produces_edges_applied": produces_applied,
    }
    return subgoals, diagnostics


def derive_query_postconditions(sample: dict, seed_files: list[str]) -> list[str]:
    """
    Coarse tags for precondition_gate.py's Jaccard gate -- the first time it
    is fed real (non-synthetic) data; see that module's own docstring. Not a
    claim to formal precondition extraction: `lang:`/`area:` are the same
    coarse-tag shape ingest_after_skills.py already uses for real AFTER
    tasks (`role:<role>`), just derived from the SWE-bench sample instead.
    """
    tags = [f"lang:{sample.get('repo_language') or 'unknown'}"]
    if seed_files:
        first = seed_files[0]
        area = first.rsplit("/", 1)[0] if "/" in first else first
        tags.append(f"area:{area}")
    tags.append("touches_test:false")   # the agent never edits tests
    return tags


@dataclass
class UsageTrackingOpenAIAgent:
    """PanelAgent-shaped (agent_id/model_id/family/respond), but folds each
    call's token usage into a shared agent.Usage counter so this arm's total
    cost stays comparable to the other arms' -- see module docstring."""

    agent_id: str
    model_id: str
    family: str
    usage: Usage
    api_key_field: Optional[str] = None
    base_url: Optional[str] = None
    max_tokens: int = 2000

    async def respond(self, system: str, user: str) -> str:
        from openai import AsyncOpenAI

        key = (settings.require(self.api_key_field) if self.api_key_field
               else "not-needed-for-local")
        client = AsyncOpenAI(api_key=key, base_url=self.base_url)
        resp = await client.chat.completions.create(
            model=self.model_id,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        if resp.usage:
            self.usage.add(resp.usage)
        return resp.choices[0].message.content or ""


def build_decomposer(
    model: str, retriever: Optional[HybridRetriever], usage: Usage,
    with_critique: bool = False,
) -> DecompositionService:
    """
    Wires a DecompositionService against the same General Compute endpoint
    the rest of this experiment uses (see run_graph_experiment.py's own
    client construction), so a caller only needs one model name, not a
    separate provider config for this bridge. `with_critique=False` by
    default for cost control on a full sweep; the adversarial critic pass
    doubles the LLM calls this bridge makes.
    """
    generator = UsageTrackingOpenAIAgent(
        agent_id="swebench_decomposer", model_id=model, family="swebench",
        usage=usage, api_key_field="general_compute_api_key",
        base_url=settings.general_compute_base_url,
    )
    critic = None
    if with_critique:
        critic = UsageTrackingOpenAIAgent(
            agent_id="swebench_critic", model_id=model, family="swebench",
            usage=usage, api_key_field="general_compute_api_key",
            base_url=settings.general_compute_base_url,
        )
    return DecompositionService(generator=generator, critic=critic, retriever=retriever)


async def _reused_instance_ids(pool, reused: list[dict], id_key: str) -> set[str]:
    """
    Map reused/matched node ids back to the SWE-bench instance_id they came
    from -- the same lookup graph_memory._instance_of() does per-node, batched
    by table here since `reused` can list several. Used only by the
    holdout-leak check below; a plain read, safe under concurrent access.
    """
    by_table: dict[str, list] = {"task_nodes": [], "knowledge_nodes": []}
    for r in reused:
        table = r.get("table")
        nid = r.get(id_key)
        if table in by_table and nid:
            by_table[table].append(nid)
    out: set[str] = set()
    if by_table["task_nodes"]:
        rows = await pool.fetch(
            "SELECT skill_ref AS iid FROM task_nodes WHERE id = ANY($1::uuid[])",
            by_table["task_nodes"])
        out |= {str(r["iid"]) for r in rows if r["iid"]}
    if by_table["knowledge_nodes"]:
        rows = await pool.fetch(
            "SELECT properties->>'instance_id' AS iid FROM knowledge_nodes "
            "WHERE id = ANY($1::uuid[])", by_table["knowledge_nodes"])
        out |= {str(r["iid"]) for r in rows if r["iid"]}
    return out


async def decompose_issue(
    decomposer: DecompositionService,
    sample: dict,
    seed_files: Optional[list[str]] = None,
    query_postconditions: Optional[list[str]] = None,
    pool=None,
    held_out_instance_id: Optional[str] = None,
) -> tuple[Optional[list[dict]], dict]:
    """
    Drive DecompositionService.decompose() for one SWE-bench Pro sample and
    translate a safe-to-propose result into htn_agent's subgoal-list shape.

    Returns (subgoals, diagnostics). `subgoals` is None whenever there is
    nothing usable to seed a plan with -- an infeasible decomposition, a
    capability-boundary rejection, an empty change set, or a detected
    holdout leak -- so the caller's own planner LLM runs exactly as it does
    today; this bridge only ever adds a chance to skip that call, never a
    reason to fail outright. `diagnostics` always carries what the backend
    pipeline actually did (reuse hits, dedup, subtask reuse, objections),
    so a caller can prove these code paths fired, not just that the call
    didn't crash.

    `held_out_instance_id`: when given (together with `pool`), checked
    against every reused/matched node's source instance -- the same check
    run_one() already performs for graph_memory.retrieve(). The bi-temporal
    filter that hold_out() relies on should already make this impossible;
    this is the same defense-in-depth verification, not the primary
    safeguard.
    """
    if query_postconditions is None and seed_files:
        query_postconditions = derive_query_postconditions(sample, seed_files)

    problem = _ISSUE_FRAMING + str(sample.get("problem_statement", ""))
    result: Decomposition = await decomposer.decompose(
        problem, query_postconditions=query_postconditions)

    diagnostics = {
        "feasible": result.feasible,
        "reused_nodes": result.reused_nodes,
        "subtask_reuse": result.subtask_reuse,
        "deduplicated": len(result.deduplicated),
        "objections": result.objections,
        "suspected_manipulation": result.suspected_manipulation,
        "structural_problems": result.structural_problems,
        "is_novel": result.is_novel,
        "node_count": result.node_count,
    }

    if held_out_instance_id is not None and pool is not None:
        leaked_ids = await _reused_instance_ids(pool, result.reused_nodes, "id")
        leaked_ids |= await _reused_instance_ids(pool, result.subtask_reuse, "matched_id")
        if str(held_out_instance_id) in leaked_ids:
            diagnostics["holdout_leaked"] = True
            return None, diagnostics

    if not result.safe_to_propose or not result.change_set.ops:
        return None, diagnostics

    subgoals, sg_diag = changeset_to_subgoals(result.change_set)
    diagnostics.update(sg_diag)
    if not subgoals:
        return None, diagnostics
    return subgoals, diagnostics
