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

# Truth-maintenance relations (relate_claims, below): these two assert
# the TARGET claim is no longer current belief (truth_state flips OUT).
RELATIONS = {"SUPERSEDES", "CONTRADICTS"}

# General epistemic/structural relations (link_claims, below; CONSOLIDATED
# directive Phase 2's relation vocabulary). None of these assert the
# target is no longer believed -- they describe HOW two claims relate,
# not a revision of current truth. Deliberately a flat set, not a richer
# taxonomy: the directive names exactly these, no more.
GENERAL_RELATIONS = {
    "SUPPORTS", "REFINES", "DEPENDS_ON", "CONDITIONAL_ON",
    "GENERALIZES", "SPECIALIZES", "DERIVED_FROM", "INSTANTIATES",
    "APPLIES_TO",
}

ALL_CLAIM_RELATIONS = RELATIONS | GENERAL_RELATIONS


def _edge_type_for_relation(relation: str) -> str:
    """`SUPERSEDES` is the only claim relation with a literal matching
    `edge_type` ENUM member (`db/01_ontology.sql` -- frozen, schema.md,
    CLAUDE.md hard rule 3: no ALTER TYPE here). Every OTHER relation --
    CONTRADICTS included -- rides the existing `VALIDATED_BY` bucket with
    `custom_edge_type` carrying the real relation name: the same
    precedent this file's own `_DISPUTED_CLAIM_SQL` already established
    for `VALIDATED_BY`/`CONFLICTS_WITH`, reused rather than reinvented.

    REAL BUG FIXED (found during the CONSOLIDATED-directive Phase 0
    audit): `relate_claims()` previously wrote `edge_type='SUPERSEDES'`
    UNCONDITIONALLY regardless of which of the two relations was passed
    -- a stored CONTRADICTS edge was indistinguishable from a genuine
    SUPERSEDES edge by `edge_type` alone; a caller had to already know to
    additionally filter `custom_edge_type` to tell them apart. Fixed
    here so `edge_type` carries real signal for the one relation that
    has a real enum member, and every relation is queryable uniformly
    via `custom_edge_type` regardless of which bucket it lives in (see
    `get_claim_relations`, below)."""
    return "SUPERSEDES" if relation == "SUPERSEDES" else "VALIDATED_BY"


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
    scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None,
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

    `scope_type`/`scope_entity_id` (found missing this pass): `knowledge_
    nodes` has carried these columns since migration 21 (CHECK-constrained
    in 22 against v0_gate.SCOPE_TYPES), but this writer never set them --
    every real claim was captured with scope_type=NULL, unlike
    capture_procedure() which hard-requires scope via v0_gate.validate_
    scope(). Deliberately kept OPTIONAL here rather than made mandatory
    outright: ~40 existing test callers across 7 files construct claims
    with no scope concept at all, and forcing all of them through the V0
    gate in one pass is real, separate, larger work (and risks masking
    genuine test failures behind a wave of mechanical scope-value
    additions). When a caller DOES supply scope_type, it is validated via
    the SAME v0_gate.validate_scope() capture_procedure uses -- no
    weaker check. Real production inheritance is wired at the two call
    sites that actually have a real scope value available:
    observations.py::promote_observation_to_claim() (derives
    scope_type='project' from the justified episode's project_id when
    present) and skill_ingestion.py::_write_task_nodes() (inherits the
    parent procedure's own scope_type/scope_entity_id onto the task_nodes
    it creates from that procedure's steps).
    """
    if truth_state not in TRUTH_STATES:
        raise ValueError(f"truth_state must be one of {TRUTH_STATES}, got {truth_state!r}")
    if visibility not in ("public", "private"):
        raise ValueError(f"visibility must be 'public' or 'private', got {visibility!r}")
    if scope_type is not None:
        from app.services.v0_gate import validate_scope
        scope_type, scope_entity_id = validate_scope(scope_type, scope_entity_id)

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
                " owner_id, visibility, scope_type, scope_entity_id) "
                "VALUES ('claim', $1, $2, $3::vector, $4, 'company_ingested', $5, $6::visibility_level, $7, $8) "
                "RETURNING id",
                statement[:200], props, to_pgvector(embedding), created_by,
                owner_id, visibility, scope_type, scope_entity_id,
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
    edge_type = _edge_type_for_relation(relation)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO edges (edge_type, custom_edge_type, "
                " source_id, source_table, target_id, target_table, "
                " properties, created_by, provenance) "
                "VALUES ($1::edge_type, $2, $3::uuid, 'knowledge_nodes', "
                " $4::uuid, 'knowledge_nodes', $5, $6, 'company_ingested')",
                edge_type, relation, from_claim_id, to_claim_id, {}, created_by,
            )
            await conn.execute(
                "UPDATE knowledge_nodes SET properties = "
                " properties || '{\"truth_state\": \"OUT\"}'::jsonb "
                "WHERE id = $1::uuid",
                to_claim_id,
            )


async def link_claims(
    pool: asyncpg.Pool,
    *,
    from_claim_id: str,
    to_claim_id: str,
    relation: str,
    created_by: str = CREATED_BY,
    properties: Optional[dict[str, Any]] = None,
) -> None:
    """
    Record a general epistemic/structural relation between two claims
    (CONSOLIDATED directive Phase 2's relation vocabulary --
    SUPPORTS/REFINES/DEPENDS_ON/CONDITIONAL_ON/GENERALIZES/SPECIALIZES/
    DERIVED_FROM/INSTANTIATES/APPLIES_TO).

    Deliberately separate from `relate_claims()`: these relations do NOT
    assert `to_claim_id` is no longer current belief -- no `truth_state`
    side effect. `relate_claims()` stays the one and only Truth
    Maintenance operation (SUPERSEDES/CONTRADICTS specifically); mixing
    a truth-revising and a non-truth-revising write into one function
    would make the truth-maintenance guarantee ("only these two relations
    can ever flip truth_state") a convention instead of a structural fact.

    Rides the same `edges` table, same `VALIDATED_BY` + `custom_edge_type`
    idiom `_edge_type_for_relation` establishes for CONTRADICTS -- no
    migration, no parallel graph table (CLAUDE.md Rule 2 / directive
    Rule 2: extend the existing abstraction, don't create a parallel
    one). Does not validate that either claim id currently exists or is
    live -- same posture `relate_claims()` already has; a caller wanting
    that guarantee resolves it before calling, same as elsewhere in this
    file.
    """
    if relation not in GENERAL_RELATIONS:
        raise ValueError(f"relation must be one of {GENERAL_RELATIONS}, got {relation!r}")
    await pool.execute(
        "INSERT INTO edges (edge_type, custom_edge_type, "
        " source_id, source_table, target_id, target_table, "
        " properties, created_by, provenance) "
        "VALUES ('VALIDATED_BY'::edge_type, $1, $2::uuid, 'knowledge_nodes', "
        " $3::uuid, 'knowledge_nodes', $4, $5, 'company_ingested')",
        relation, from_claim_id, to_claim_id, properties or {}, created_by,
    )


async def get_claim_relations(
    pool: asyncpg.Pool,
    claim_id: str,
    *,
    direction: str = "both",
    relations: Optional[set[str]] = None,
) -> list[dict]:
    """
    Bounded, single-hop read of the real relation edges touching one
    claim -- the primitive a bounded multi-hop traversal service
    (CONSOLIDATED directive's EXPLAIN/DECIDE/RESEARCH modes, a later
    Phase-2 increment) composes over, not the traversal itself (the
    directive's own "do not perform unbounded BFS" rule applies to that
    later layer, not this single-hop read).

    `direction`: "outgoing" (`claim_id` is the edge's source -- e.g. this
    claim SUPERSEDES/SUPPORTS/etc. another), "incoming" (`claim_id` is
    the target), or "both" (default).

    `relations`: optional filter to a subset of `ALL_CLAIM_RELATIONS`
    (mixing RELATIONS and GENERAL_RELATIONS is fine -- both idioms are
    queryable uniformly via `custom_edge_type` regardless of which
    `edge_type` bucket a given relation happens to live in, per
    `_edge_type_for_relation`). Defaults to every known claim relation.

    Returns only LIVE edges (`t_invalid IS NULL`) between two
    `knowledge_nodes` -- this deliberately cannot return the trigger/
    dispute mechanism's own `VALIDATED_BY`/`CONFLICTS_WITH` edges
    (`_DISPUTED_CLAIM_SQL`, above), which run `task_nodes -> knowledge_
    nodes`, not `knowledge_nodes -> knowledge_nodes` -- no collision.
    """
    if direction not in ("outgoing", "incoming", "both"):
        raise ValueError(
            f"direction must be 'outgoing', 'incoming', or 'both', got {direction!r}"
        )
    wanted = relations if relations is not None else ALL_CLAIM_RELATIONS
    unknown = wanted - ALL_CLAIM_RELATIONS
    if unknown:
        raise ValueError(
            f"unknown relation(s) {unknown}, must be a subset of {ALL_CLAIM_RELATIONS}"
        )

    clauses = []
    if direction in ("outgoing", "both"):
        clauses.append(
            "(e.source_id = $1::uuid AND e.source_table = 'knowledge_nodes' "
            "AND e.target_table = 'knowledge_nodes')"
        )
    if direction in ("incoming", "both"):
        clauses.append(
            "(e.target_id = $1::uuid AND e.target_table = 'knowledge_nodes' "
            "AND e.source_table = 'knowledge_nodes')"
        )
    where = " OR ".join(clauses)
    rows = await pool.fetch(
        f"SELECT e.id, e.source_id, e.target_id, e.custom_edge_type AS relation, "
        f"e.created_by, e.t_valid, e.properties "
        f"FROM edges e WHERE ({where}) "
        f"AND e.custom_edge_type = ANY($2::text[]) AND e.t_invalid IS NULL",
        claim_id, list(wanted),
    )
    return [dict(r) for r in rows]


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
