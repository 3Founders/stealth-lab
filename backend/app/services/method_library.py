"""
HTN method library: dynamic synthesis + reuse over the SAME graph platform
graph_memory.py already bridges to, at a DIFFERENT granularity.

graph_memory.py's task_nodes rows (created_by='swebench_ingest') are one row
per WHOLE ISSUE -- "similar problem, where did its fix live". This module's
rows (created_by=CREATED_BY, provenance='prior_library' -- the enum value
db/01_ontology.sql defines for exactly this case) are one row per HTN
SUBGOAL DECOMPOSITION -- "has this kind of problem been broken down before,
and into what". Mixing the two in one flat search would match a decomposed
subgoal like "add a cookie fallback to auth middleware" against an entire
unrelated SWE-bench issue row, so this module never calls
reuse_detection.find_reusable_nodes (which searches task_nodes +
knowledge_nodes together with no content-type filter) -- it runs its own
query, scoped to CREATED_BY, reusing that module's thresholds and lexical
fallback rather than its query.

WHY THIS IS A SEPARATE ASYNC MODULE, NOT PART OF HTNAgent ITSELF

htn_agent.py's HTNAgent is deliberately synchronous with no database
dependency -- every call in it is `self._client.chat.completions.create`.
run_graph_experiment.py calls `runner.run(...)` synchronously from inside an
async function; starting an event loop inside that call (to await asyncpg)
would raise "asyncio.run() cannot be called from a running event loop". The
existing graph-memory bridge (this file's sibling, graph_memory.py) solves
this the same way: retrieval happens BEFORE `.run()`, as a plain async call
the harness awaits, and the result is handed to the agent as plain data.
This module follows that exact pattern -- see ResearchHTNAgent._synthesize_method
in htn_agent.py for the glue that hands a match to HTNAgent.run() via its
(synchronous) `_seed_plan` hook.
"""
from __future__ import annotations

from typing import Optional

from app.services.embeddings import Embedder, to_pgvector
from app.services.precondition_gate import extract_postconditions, postconditions_compatible
from app.services.reuse_detection import (
    FULL_MATCH_THRESHOLD, LEXICAL_FULL_MATCH_THRESHOLD, _lexical_overlap,
)

CREATED_BY = "htn_method_library"


def _to_match(row, similarity: float, method: str) -> dict:
    schema = row["io_schema"] or {}
    return {
        "id": str(row["id"]), "name": row["name"],
        "similarity": round(float(similarity), 3), "method": method,
        "decomposition": schema.get("decomposition"),
    }


def _passes_gate(row, query_postconditions: Optional[list[str]]) -> bool:
    """
    Rule 1 gate, reusing precondition_gate.py as-is (it already reads
    arbitrary string tags from any dict, so no edit there is needed): a
    stored method's `touch_tags` (see persist_plan below) must overlap the
    new issue's predicted touch-set, upgrading reuse from "text looked
    similar" to "text looked similar AND the predicted file set actually
    overlaps" -- directly targets Pattern A (GRAPH_EXPERIMENT.md section 8):
    a stored method whose call-graph fingerprint doesn't match the new
    issue is refused reuse rather than silently seeding a plan for the
    wrong files. Trivially passes, as elsewhere in this codebase, when
    either side has no tags -- this only tightens matching once both a
    stored method and the query actually carry touch tags.
    """
    candidate_tags = extract_postconditions(row["success_criteria"] or {})
    return postconditions_compatible(candidate_tags, query_postconditions)


async def find_reusable_plan(
    pool, embedder: Embedder, goal_text: str,
    query_postconditions: Optional[list[str]] = None,
) -> Optional[dict]:
    """
    Look up a past HTN decomposition for a goal closely matching `goal_text`.

    Returns {"id", "name", "similarity", "method", "decomposition"} on a
    CONFIDENT FULL match only -- same threshold reuse_detection.py uses for
    its own full-match short-circuit. Deliberately no partial-match tier:
    a decomposition either fits the new problem or it doesn't, there is no
    "adapt a related one" step here the way memory_block lets a model adapt
    a related PATCH in prose. None is the common, expected answer early in
    a library's life -- that is a correct result, not a failure.

    `query_postconditions`: optional touch-set tags (e.g. from
    call_graph.py's reachability analysis) checked against the candidate's
    own stored `touch_tags` via the Rule 1 gate before a match is accepted.
    Omitted, behavior is unchanged from before this parameter existed.
    """
    try:
        vec = await embedder.embed_one(goal_text, input_type="query")
        row = await pool.fetchrow(
            "SELECT id, name, io_schema, success_criteria, "
            " 1 - (embedding <=> $1::vector) AS similarity "
            "FROM task_nodes WHERE created_by = $2 AND t_invalid IS NULL "
            "AND embedding IS NOT NULL ORDER BY similarity DESC LIMIT 1",
            to_pgvector(vec), CREATED_BY)
        if row and row["similarity"] >= FULL_MATCH_THRESHOLD and _passes_gate(row, query_postconditions):
            await _bump_reuse_count(pool, row["id"])
            return _to_match(row, row["similarity"], "vector")
    except Exception:  # noqa: BLE001
        pass  # falls through to lexical, same tolerance reuse_detection.py has

    rows = await pool.fetch(
        "SELECT id, name, io_schema, success_criteria, description FROM task_nodes "
        "WHERE created_by = $1 AND t_invalid IS NULL", CREATED_BY)
    best, best_score = None, 0.0
    for r in rows:
        score = _lexical_overlap(goal_text, r["description"] or r["name"])
        if score > best_score:
            best, best_score = r, score
    if (best is not None and best_score >= LEXICAL_FULL_MATCH_THRESHOLD
            and _passes_gate(best, query_postconditions)):
        await _bump_reuse_count(pool, best["id"])
        return _to_match(best, best_score, "lexical")
    return None


async def _bump_reuse_count(pool, row_id) -> None:
    """Cheap usage counter, not a reward model -- see ResearchHTNAgent
    docstring item 5 for what turning this into a real bandit needs."""
    await pool.execute(
        "UPDATE task_nodes SET success_criteria = success_criteria || "
        "jsonb_build_object('times_reused', "
        "COALESCE((success_criteria->>'times_reused')::int, 0) + 1) "
        "WHERE id = $1", row_id)


async def persist_plan(pool, embedder: Embedder, goal_text: str,
                       decomposition: Optional[list[dict]], steps_used: int,
                       touch_tags: Optional[list[str]] = None) -> None:
    """
    Store a SUCCEEDED goal -> decomposition pair for future reuse.

    Call only after a run actually completed successfully -- there is no
    quarantine/approval step here the way DecompositionService has, because
    what is being stored is the agent's OWN already-executed, already-graded
    work, not a generative proposal from an untrusted source. `decomposition`
    is `run.htn["plan"]` (list of {"id","goal","deps"}, exactly parse_dag's
    input shape) -- reused verbatim through the same validation on the way
    back out, so a stored plan gets no less scrutiny than a freshly-planned
    one.

    `touch_tags`: optional predicted-touch-set tags (e.g.
    `[f"touches:{f}" for f in reachability.files]` from call_graph.py),
    stored under the same `postconditions` key precondition_gate.py's
    extract_postconditions() already reads -- no new gate logic needed, see
    find_reusable_plan's `_passes_gate` above.

    Every row this writes is tagged `internal_proxy: true` in
    success_criteria -- the same flag hierarchy.py/reuse_detection.py
    already exclude on their own reads. retrieval.py does not yet exclude
    it (a known, separately-tracked gap -- see the deferred fix in this
    change's plan), so until that lands these rows can still occupy a
    ranked slot in HybridRetriever's fusion before being silently dropped
    at hydration (no `skill_ref`) -- degraded, not incorrect, but real
    corpus drift worth avoiding once retrieval.py is safe to touch.
    """
    vec = await embedder.embed_one(goal_text, input_type="document")
    success_criteria = {
        "attempts": 1, "successes": 1, "mean_steps": steps_used, "times_reused": 0,
        "internal_proxy": True,
    }
    if touch_tags:
        success_criteria["postconditions"] = touch_tags
    await pool.execute(
        "INSERT INTO task_nodes (name, description, io_schema, success_criteria, "
        " embedding, created_by, provenance) "
        "VALUES ($1,$2,$3,$4,$5::vector,$6,'prior_library')",
        goal_text[:500], goal_text[:2000],
        {"kind": "htn_method", "decomposition": decomposition},
        success_criteria,
        to_pgvector(vec), CREATED_BY)
