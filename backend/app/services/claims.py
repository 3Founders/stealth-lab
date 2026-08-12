"""
Claim-level hyper-nodes, on top of the existing bi-temporal
knowledge_nodes/task_nodes/edges schema (backend/db/01_ontology.sql) --
no migration, because every piece this needs already exists:

  - a Claim is just node_type='claim'; node_type is TEXT, not an enum.
  - temporal scoping is t_valid/t_invalid, already bi-temporal on every row.
  - truth state (IN/OUT, for a real Truth Maintenance System) lives in
    the existing `properties` JSONB column -- orthogonal to t_valid/
    t_invalid: a claim can be t_valid (still exists, not deleted) but
    truth_state OUT (known superseded/contradicted, no longer believed).
  - justification (pointer to the execution trace or source that produced
    the claim) is episode_links, which already links any node to an
    episode/trace by id.
  - SUPERSEDES is already a real edge_type enum value. CONTRADICTS is
    not, so it rides in `custom_edge_type` -- the same idiom
    failure_capture.py already uses for FAILURE_MODE, which also isn't
    in the enum.
  - "a claim leads to a set of task_nodes" is just the existing
    polymorphic edges table, one row per task_node, same as
    failure_capture.py's single OWNS edge but N-ary here instead of 1:1.

Same WHY-NOT-KnowledgeUpdater reasoning as failure_capture.py: this is a
trusted, internal write of one node plus its edges in one transaction,
not a dispatch through apply()/apply_generated()'s op-type machinery.
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg

CREATED_BY = "claim_capture"

TRUTH_STATES = {"IN", "OUT"}
RELATIONS = {"SUPERSEDES", "CONTRADICTS"}


async def capture_claim(
    pool: asyncpg.Pool,
    *,
    statement: str,
    task_ids: list[str],
    justification_episode_id: Optional[str] = None,
    created_by: str = CREATED_BY,
    truth_state: str = "IN",
    properties: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """
    Write one claim knowledge_node plus one PRODUCES/CLAIM_OF edge to
    EACH live task_node in `task_ids` (task_nodes.skill_ref, the same
    key graph_ingest.py and failure_capture.py both use).

    Returns the new claim's id, or None if none of `task_ids` resolve to
    a live task_node -- a claim that supports nothing has nothing to
    link to, so it is dropped rather than written orphaned. Matches
    capture_failure()'s silent-no-op discipline: best-effort telemetry
    must never be able to fail the run it is attached to.
    """
    if truth_state not in TRUTH_STATES:
        raise ValueError(f"truth_state must be one of {TRUTH_STATES}, got {truth_state!r}")
    props: dict[str, Any] = {
        **(properties or {}),
        "statement": statement,
        "truth_state": truth_state,
    }
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT id FROM task_nodes WHERE skill_ref = ANY($1::text[]) "
                "AND t_invalid IS NULL",
                task_ids,
            )
            if not rows:
                return None
            node_id = await conn.fetchval(
                "INSERT INTO knowledge_nodes "
                "(node_type, name, properties, created_by, provenance) "
                "VALUES ('claim', $1, $2, $3, 'company_ingested') "
                "RETURNING id",
                statement[:200], props, created_by,
            )
            for row in rows:
                await conn.execute(
                    "INSERT INTO edges (edge_type, custom_edge_type, "
                    " source_id, source_table, target_id, target_table, "
                    " properties, created_by, provenance) "
                    "VALUES ('PRODUCES', 'CLAIM_OF', $1, 'knowledge_nodes', "
                    " $2, 'task_nodes', $3, $4, 'company_ingested')",
                    node_id, row["id"], {}, created_by,
                )
            if justification_episode_id is not None:
                await conn.execute(
                    "INSERT INTO episode_links (episode_id, target_id, target_table) "
                    "VALUES ($1::uuid, $2, 'knowledge_nodes')",
                    justification_episode_id, node_id,
                )
    return str(node_id)


async def relate_claims(
    pool: asyncpg.Pool,
    *,
    from_claim_id: str,
    to_claim_id: str,
    relation: str,
    created_by: str = CREATED_BY,
) -> None:
    """
    Record that `from_claim_id` SUPERSEDES or CONTRADICTS `to_claim_id`,
    and flip the target's truth_state to OUT. This is the actual Truth
    Maintenance step: `to_claim_id` is NOT invalidated (t_invalid stays
    NULL, it still exists and is still queryable as history) -- only its
    truth_state changes, so "what did we once believe" and "what do we
    believe now" stay separately answerable from the same row.
    """
    if relation not in RELATIONS:
        raise ValueError(f"relation must be one of {RELATIONS}, got {relation!r}")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO edges (edge_type, custom_edge_type, "
                " source_id, source_table, target_id, target_table, "
                " properties, created_by, provenance) "
                "VALUES ('SUPERSEDES', $1, $2::uuid, 'knowledge_nodes', "
                " $3::uuid, 'knowledge_nodes', $4, $5, 'company_ingested')",
                relation, from_claim_id, to_claim_id, {}, created_by,
            )
            await conn.execute(
                "UPDATE knowledge_nodes SET properties = "
                " properties || '{\"truth_state\": \"OUT\"}'::jsonb "
                "WHERE id = $1::uuid",
                to_claim_id,
            )
