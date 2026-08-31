"""
Claim-level hyper-nodes, on top of the existing bi-temporal
knowledge_nodes/task_nodes/edges schema (backend/db/01_ontology.sql) --
no migration for the base representation, because every piece this needs
already exists:

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

Ticket 03 (memory-substrate map): NODE_TYPE_SCHEMAS is a real, validated
registry for node_type='claim' specifically -- the same pattern ticket 02
established for domain_payload (a dict-keyed Pydantic-model registry,
validated in the service layer, not a DB constraint -- no ORM/Alembic in
this repo). Deliberately scoped to 'claim' only; the other 6 existing
virtual types (failure_mode, hierarchy_group, code_location, policy,
policy_document, fact) are NOT retroactively migrated onto this pattern
here -- that's real, separate cleanup ticket 03 explicitly declined to
fold in.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

import asyncpg
from pydantic import BaseModel, Field

from app.services.access import AccessScope, TenantScope, scope_predicates
from app.services.embeddings import Embedder, to_pgvector

CREATED_BY = "claim_capture"

TRUTH_STATES = {"IN", "OUT"}
RELATIONS = {"SUPERSEDES", "CONTRADICTS"}


class ClaimProperties(BaseModel):
    """
    Real, validated schema for node_type='claim' properties (ticket 03,
    amended by ticket 10). Every field here was named explicitly in the
    resolved ticket text -- nothing invented beyond it.

    `epistemic_status` (ticket 10's amendment): 'observed' (deterministically
    derived from trace events) vs 'inferred' (semantically extracted,
    model-derived). Ticket 04 owns HOW this value gets assigned when a
    claim is produced from an observation -- see
    app/services/observations.py's claim-promotion helper.

    `confidence` stays here even though ticket 04 explicitly forbids it on
    the raw `observations` table -- a claim is one step removed from raw
    extraction, and this field is real estate for a future calibrated
    signal (ticket 04's own fog item: conformal prediction against a
    calibration set), not populated with anything today. Left unset by
    default rather than populated with an uncalibrated guess, same
    reasoning ticket 04 already established.
    """

    model_config = {"extra": "allow"}  # properties may carry additional,
    # unvalidated keys (e.g. this claim's own free-form domain context)
    # -- this registry validates the fields ticket 03/10 named, it does
    # not forbid a caller from attaching more.

    statement: str
    subject: Optional[str] = None
    predicate: Optional[str] = None
    object: Optional[str] = None
    truth_state: Literal["IN", "OUT"] = "IN"
    claim_type: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    extraction_version: Optional[str] = None
    epistemic_status: Optional[Literal["observed", "inferred"]] = None


NODE_TYPE_SCHEMAS: dict[str, type[BaseModel]] = {
    "claim": ClaimProperties,
}
"""
Ticket 03's registry, scoped to 'claim' only. Mirrors ticket 02's real
DOMAIN_PAYLOAD_SCHEMAS pattern (dict[key, type[BaseModel]], validated at
write time in the service layer) -- same idiom, different key shape
(node_type alone here, vs (concept, domain) there), because claims are
explicitly one of the concepts ticket 02 named as NOT domain-shaped.
"""


async def capture_claim(
    pool: asyncpg.Pool,
    *,
    statement: str,
    task_ids: list[str],
    justification_episode_id: Optional[str] = None,
    created_by: str = CREATED_BY,
    truth_state: str = "IN",
    subject: Optional[str] = None,
    predicate: Optional[str] = None,
    object: Optional[str] = None,  # noqa: A002 -- matches the real triple field name (ticket 03)
    claim_type: Optional[str] = None,
    confidence: Optional[float] = None,
    extraction_version: Optional[str] = None,
    epistemic_status: Optional[str] = None,
    properties: Optional[dict[str, Any]] = None,
    embedder: Optional[Embedder] = None,
    owner_id: Optional[str] = None,
    visibility: str = "public",
) -> Optional[str]:
    """
    Write one claim knowledge_node plus one PRODUCES/CLAIM_OF edge to
    EACH live task_node in `task_ids` (task_nodes.skill_ref, the same
    key graph_ingest.py and failure_capture.py both use).

    REAL BUG FIXED (ticket 03's own finding, confirmed directly against
    this file before fixing it): this function previously omitted
    `embedding` from its INSERT entirely -- claims were written but
    invisible to the real retrieval stack (HybridRetriever filters on
    `embedding IS NOT NULL` throughout). Fixed by computing one from
    `statement`, same as every other real embedded write path in this
    codebase. `embedder` is injectable (mirrors KnowledgeUpdater's own
    lazy-construction pattern) so tests don't need real network access.

    Properties are now validated against ClaimProperties (ticket 03's
    NODE_TYPE_SCHEMAS registry) before insert -- a malformed
    confidence/epistemic_status value fails loudly here, not silently at
    some later read.

    Returns the new claim's id, or None if none of `task_ids` resolve to
    a live task_node -- a claim that supports nothing has nothing to
    link to, so it is dropped rather than written orphaned. Matches
    capture_failure()'s silent-no-op discipline: best-effort telemetry
    must never be able to fail the run it is attached to.

    REAL GAP FIXED (found while working ticket 09's production gaps,
    confirmed by grepping the whole app/ tree): this INSERT never set
    `owner_id`/`visibility` at all, so every claim silently fell back to
    the schema default (`visibility='public'`, `owner_id=NULL`) --
    exactly the tenant_id cautionary case access.py's own docstring
    warns about, except at write time rather than read time. A caller
    passing `AccessScope.for_user(...)` to a scoped reader would still
    never see anything as "theirs", because nothing had ever recorded
    whose it was. `owner_id`/`visibility` are now real parameters here,
    not decorative columns.
    """
    if truth_state not in TRUTH_STATES:
        raise ValueError(f"truth_state must be one of {TRUTH_STATES}, got {truth_state!r}")
    if visibility not in ("public", "private"):
        raise ValueError(f"visibility must be 'public' or 'private', got {visibility!r}")

    validated = ClaimProperties(
        statement=statement,
        subject=subject,
        predicate=predicate,
        object=object,
        truth_state=truth_state,
        claim_type=claim_type,
        confidence=confidence,
        extraction_version=extraction_version,
        epistemic_status=epistemic_status,
    )
    props: dict[str, Any] = {
        **(properties or {}),
        **validated.model_dump(exclude_none=True),
    }

    embedder = embedder or Embedder()
    embedding = await embedder.embed_one(statement, input_type="document")

    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT id FROM task_nodes WHERE skill_ref = ANY($1::text[]) "
                "AND t_invalid IS NULL",
                task_ids,
            )
            # Option B (approved 2026-08-28): a claim may be justified by an
            # EPISODE instead of by a task_node. Before this, `not rows`
            # returned unconditionally, which fired ahead of the
            # `justification_episode_id is not None` branch below and made
            # that branch dead code on the task-less path -- so a
            # trace-derived observation, which has no task_node to resolve
            # against, could never become a claim at all.
            #
            # Both inputs missing is still nothing to anchor to, and still
            # returns None: that safety net is deliberate and must not
            # regress. The PRODUCES/CLAIM_OF loop below no-ops naturally on
            # an empty `rows`, so an episode-justified claim simply carries
            # no task edge -- which is the real provenance shape, not a
            # degraded one.
            if not rows and justification_episode_id is None:
                return None
            node_id = await conn.fetchval(
                "INSERT INTO knowledge_nodes "
                "(node_type, name, properties, embedding, created_by, provenance, "
                " owner_id, visibility) "
                "VALUES ('claim', $1, $2, $3::vector, $4, 'company_ingested', $5, $6::visibility_level) "
                "RETURNING id",
                statement[:200], props, to_pgvector(embedding), created_by,
                owner_id, visibility,
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


# Phase 31: "a claim that is contradicted should not continue appearing as
# current truth." Two belief-revision mechanisms already exist and both stay
# untouched here:
#   - relate_claims() above flips properties->>'truth_state' to 'OUT' -- a
#     RESOLVED contradiction/supersession.
#   - KnowledgeUpdater's debate-approval path (knowledge_update.py) sets
#     t_invalid on an approved REPLACE.
# Neither fires while a knowledge_conflict.py trigger is merely OPEN: the
# debate hasn't concluded, so truth_state is still 'IN' and t_invalid is
# still NULL, and the claim silently keeps reading as live truth even
# though a conflict against it is under active review. Deliberately NOT
# auto-invalidating on trigger creation -- the debate may conclude the
# flagged claim was right, or that neither side actually conflicts, and
# guessing wrong here would be a worse error than temporarily hiding a
# claim that turns out fine (same "borderline cases get reviewed, not
# auto-resolved" posture BAND0_DECISIONS.md takes elsewhere). Instead, a
# claim under active dispute is excluded from "current truth" queries
# until its debate resolves one way or the other.
#
# "Unresolved" is defined identically to triggers.py's own
# TriggerDetector.record() ("a trigger row exists whose debate is not yet
# APPROVED/REJECTED, including no debate opened at all") -- one
# definition, spelled out once here, not reinvented and risking drift.
# {alias} is the knowledge_nodes table alias in the enclosing query.
_DISPUTED_CLAIM_SQL = (
    "EXISTS (SELECT 1 FROM edges e "
    "JOIN triggers t ON t.task_node_id = e.source_id "
    "LEFT JOIN debates d ON d.trigger_id = t.id "
    "WHERE e.t_invalid IS NULL AND e.edge_type = 'VALIDATED_BY' "
    "AND e.custom_edge_type = 'CONFLICTS_WITH' "
    "AND e.source_table = 'task_nodes' "
    "AND e.target_table = 'knowledge_nodes' AND e.target_id = {alias}.id "
    "AND (d.id IS NULL OR d.state NOT IN ('APPROVED', 'REJECTED')))"
)


async def has_open_conflict_trigger(pool: asyncpg.Pool, claim_id: str) -> bool:
    """
    Point-check: does `claim_id` have an open, unresolved
    knowledge_conflict.py trigger against it right now? Same predicate
    `list_current_claims` uses internally, exposed standalone for a
    caller (or a test) that only has one claim id and doesn't want to
    run the full listing query.
    """
    return bool(await pool.fetchval(
        f"SELECT {_DISPUTED_CLAIM_SQL.format(alias='k')} "
        "FROM knowledge_nodes k WHERE k.id = $1::uuid",
        claim_id,
    ))


async def list_current_claims(
    pool: asyncpg.Pool,
    *,
    task_id: Optional[str] = None,
    scope: Optional[AccessScope] = None,
    tenant_scope: Optional[TenantScope] = None,
) -> list[dict]:
    """
    "Current truth" claims: live (t_invalid IS NULL), not superseded/
    contradicted-and-resolved (truth_state <> 'OUT'), and not presently
    under active, unresolved dispute (see `_DISPUTED_CLAIM_SQL` above).
    `task_id` optionally restricts to claims PRODUCES/CLAIM_OF-linked to
    one task_node (task_nodes.skill_ref, same key capture_claim() itself
    resolves task_ids against).

    Tenant/visibility SQL comes only from scope_predicates() per this
    repo's own rule; default unrestricted() renders literal TRUE.
    """
    scope = scope or AccessScope.unrestricted()
    tenant = tenant_scope or TenantScope.unrestricted()
    vis_sql, vis_params, next_index = scope_predicates(
        scope, tenant, alias="k", param_index=1,
    )
    params: list[Any] = list(vis_params)
    task_join = ""
    if task_id is not None:
        params.append(task_id)
        task_join = (
            "JOIN edges ce ON ce.source_id = k.id AND ce.source_table = 'knowledge_nodes' "
            "AND ce.target_table = 'task_nodes' AND ce.edge_type = 'PRODUCES' "
            "AND ce.custom_edge_type = 'CLAIM_OF' AND ce.t_invalid IS NULL "
            f"JOIN task_nodes tn ON tn.id = ce.target_id AND tn.skill_ref = ${next_index}"
        )

    rows = await pool.fetch(
        f"SELECT k.id, k.name, k.properties FROM knowledge_nodes k "
        f"{task_join} "
        f"WHERE k.node_type = 'claim' AND k.t_invalid IS NULL "
        f"AND COALESCE(k.properties->>'truth_state', 'IN') <> 'OUT' "
        f"AND NOT {_DISPUTED_CLAIM_SQL.format(alias='k')} "
        f"AND {vis_sql}",
        *params,
    )
    return [dict(r) for r in rows]
