"""
Part C: per-subtask reuse resolution.

Closes the gap identified after Parts A and B: neither checks each
individual NEWLY PROPOSED subtask against the EXISTING graph. Part A's
dedup only compares new ops against EACH OTHER within one ChangeSet.
The existing top-level reuse check (find_reusable_nodes /
_try_hierarchical_match in decomposition.py) only ever tests the
aggregate incoming problem text, once, before generation -- never the
individual subtasks the model goes on to propose.

Security constraint that shapes everything here: GENERATIVE_OP_TYPES
(app/models/change.py) means an untrusted/generated ChangeSet may only
ever CREATE things, referencing each other by REF, never by directly
attaching to an existing node's real id. This module respects that: it
identifies matches and SHRINKS the proposal (same safe pattern as
dedup.py's dedupe_changeset_ops -- drop a create op, drop any edge that
solely referenced it), and never rewrites an edge to point at an
existing node's real UUID. That makes resolve_subtask_reuse() safe to
run on generated/untrusted output, in the same slot as Part A, before
validate_generative().

What this deliberately does NOT do: wire the surviving proposal back up
to the matched existing nodes. That means attaching to real,
already-persisted rows -- a privileged operation, same trust class as
dedup.py's merge_cluster or hierarchy.py's attach_new_leaf. Left as a
follow-up step for the approval/apply path (knowledge_update.py), not
built here. `SubtaskReuseReport.matches` is what a human reviewer, or
that follow-up step, would act on.

Cost shape this exists to fix: a naive per-subtask loop calling
find_reusable_nodes/hierarchical_search once per proposed op means N
embed calls and up to N*depth round trips for N subtasks. This batches
both: one embed() call for every op's text (not embed_one per op), and
one batch_hierarchical_search call per table (not one per op) -- see
hierarchy.py's batch_hierarchical_search docstring for how that bounds
round trips by distinct frontier groups rather than op count.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.models.change import ChangeSet, CreateEdgeOp, CreateKnowledgeNodeOp, CreateTaskNodeOp
from app.services.access import AccessScope
from app.services.embeddings import Embedder
from app.services.hierarchy import batch_hierarchical_search
from app.services.reuse_detection import FULL_MATCH_THRESHOLD


def _op_text(op) -> str:
    if isinstance(op, CreateTaskNodeOp):
        return f"{op.name} {op.description or ''}"
    if isinstance(op, CreateKnowledgeNodeOp):
        return op.name
    return ""


def _op_table(op) -> Optional[str]:
    if isinstance(op, CreateTaskNodeOp):
        return "task_nodes"
    if isinstance(op, CreateKnowledgeNodeOp):
        return "knowledge_nodes"
    return None


@dataclass
class SubtaskReuseReport:
    matches: list[dict] = field(default_factory=list)
    embed_calls: int = 0
    ops_checked: int = 0


async def resolve_subtask_reuse(
    change_set: ChangeSet,
    pool,
    scope: Optional[AccessScope] = None,
    embedder: Optional[Embedder] = None,
    threshold: float = FULL_MATCH_THRESHOLD,
    query_postconditions: Optional[dict[str, list[str]]] = None,
) -> tuple[ChangeSet, SubtaskReuseReport]:
    """
    For each newly-proposed create op, check it individually against the
    EXISTING persisted graph. A confident match drops that op (and any
    edge that solely referenced it) from the changeset -- shrink-only,
    same safety pattern as dedupe_changeset_ops.

    `query_postconditions`: Rule 1 gate, keyed by op ref. Optional --
    nothing produces these yet for LLM-generated subtasks (see
    EXPERIMENT_PLAN_FINAL.md's open next-step on this), so today this
    is only usable when a caller explicitly supplies postconditions for
    specific refs; anything else passes through ungated, unchanged from
    before this parameter existed.
    """
    scope = scope or AccessScope.unrestricted()
    embedder = embedder or Embedder()
    report = SubtaskReuseReport()

    create_ops = [
        op for op in change_set.ops
        if isinstance(op, (CreateTaskNodeOp, CreateKnowledgeNodeOp))
    ]
    if not create_ops:
        return change_set, report

    report.ops_checked = len(create_ops)
    texts = [_op_text(op) for op in create_ops]
    vectors = await embedder.embed(texts, input_type="query")
    report.embed_calls = 1  # one batched call covering every op, regardless of N

    by_table: dict[str, dict[str, list[float]]] = {"task_nodes": {}, "knowledge_nodes": {}}
    op_by_ref = {op.ref: op for op in create_ops}
    for op, vec in zip(create_ops, vectors):
        table = _op_table(op)
        if table:
            by_table[table][op.ref] = vec

    dropped_refs: set[str] = set()
    for table, queries in by_table.items():
        if not queries:
            continue
        results = await batch_hierarchical_search(
            pool, table, queries, scope=scope, beam=3, adaptive=True,
            query_postconditions=query_postconditions,
        )
        for ref, result in results.items():
            if result.used_flat_fallback or result.leaf_id is None or result.similarity is None:
                continue
            if result.similarity >= threshold:
                dropped_refs.add(ref)
                report.matches.append({
                    "ref": ref, "name": op_by_ref[ref].name, "table": table,
                    "matched_id": result.leaf_id, "matched_name": result.leaf_name,
                    "similarity": result.similarity,
                })

    if not dropped_refs:
        return change_set, report

    new_ops = []
    for op in change_set.ops:
        if isinstance(op, (CreateTaskNodeOp, CreateKnowledgeNodeOp)):
            if op.ref in dropped_refs:
                continue
            new_ops.append(op)
        elif isinstance(op, CreateEdgeOp):
            if (op.source_ref and op.source_ref in dropped_refs) or (op.target_ref and op.target_ref in dropped_refs):
                # See module docstring: we do NOT rewrite this edge to
                # point at the existing node's real id -- that would mean
                # a generated changeset attaching to an arbitrary existing
                # row, exactly what GENERATIVE_OP_TYPES prevents. Drop it;
                # report.matches is what acts on this instead.
                continue
            new_ops.append(op)
        else:
            new_ops.append(op)

    return ChangeSet(ops=new_ops), report
