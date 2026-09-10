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

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

import asyncpg
from pydantic import BaseModel, Field

from app.services.access import AccessScope, TenantScope, scope_predicates
from app.services.embeddings import Embedder, to_pgvector

logger = logging.getLogger(__name__)

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
    source_ref: Optional[str] = None,
    ingestion_context_id: Optional[str] = None,
    observation_id: Optional[str] = None,
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

    ANCHORING (B7 / V4-hardening "CLAIM CREATION"): a Claim no longer has
    to be anchored to a task_node. It is written when AT LEAST ONE of the
    following holds:

      (a) >=1 `task_ids` resolves to a live task_node   (existing), or
      (b) `justification_episode_id` is given            (existing), or
      (c) `source_ref` OR `ingestion_context_id` OR `observation_id` is
          given -- document / observation provenance     (NEW).

    (c) exists so a Claim extracted from a document corpus or promoted
    from an observation -- neither of which has a task_node to point at --
    is first-class, not degraded. What (c) is NOT: a licence to write a
    totally unprovenanced opaque claim. If NONE of (a)/(b)/(c) hold the
    call is still a silent no-op (`return None`, logged at info) -- the
    same "nothing to anchor to" safety net capture_failure() keeps, so
    best-effort telemetry can never fail the run it rides on.

    When accepted via (c) with no task_ids / episode: the knowledge_node
    is still written; `ingestion_context_id` is set on its own column
    (migration 65) when given; a `claim_sources (claim_id, observation_id)`
    row is written when `observation_id` is given (same join table
    observations.py's promotion path uses); `source_ref`, which has no
    column of its own, is stashed in `properties['source_ref']` rather
    than dropped. The PRODUCES/CLAIM_OF edge loop naturally no-ops with no
    task_ids (it needs a task_node target).

    Returns the new claim's id, or None per the anchoring rule above.

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
    # `source_ref` has no column of its own on knowledge_nodes -- keep it
    # rather than drop it. `setdefault` so an explicit properties value wins.
    if source_ref is not None:
        props.setdefault("source_ref", source_ref)

    # B7: a document/observation provenance ref is a valid anchor on its
    # own. When one is present (or an episode is), acceptability is known
    # without touching the DB -- mirror the existing early-return and skip
    # the embedding spend when there is demonstrably nothing to anchor to.
    has_provenance_ref = bool(source_ref or ingestion_context_id or observation_id)
    accepted_pre_db = has_provenance_ref or justification_episode_id is not None
    if not accepted_pre_db and not task_ids:
        logger.info("capture_claim: no anchor and no provenance ref; dropping")
        return None

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
            #
            # B7: a document/observation provenance ref
            # (source_ref / ingestion_context_id / observation_id) is also
            # a valid anchor. Only when NONE of task_nodes / episode /
            # provenance-ref is present is there nothing to anchor to.
            if not rows and justification_episode_id is None and not has_provenance_ref:
                logger.info("capture_claim: no anchor and no provenance ref; dropping")
                return None
            if ingestion_context_id is not None:
                node_id = await conn.fetchval(
                    "INSERT INTO knowledge_nodes "
                    "(node_type, name, properties, embedding, created_by, provenance, "
                    " owner_id, visibility, scope_type, scope_entity_id, ingestion_context_id) "
                    "VALUES ('claim', $1, $2, $3::vector, $4, 'company_ingested', $5, "
                    " $6::visibility_level, $7, $8, $9::uuid) "
                    "RETURNING id",
                    statement[:200], props, to_pgvector(embedding), created_by,
                    owner_id, visibility, scope_type, scope_entity_id, ingestion_context_id,
                )
            else:
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
            if observation_id is not None:
                # Same join table + ON CONFLICT idempotency as
                # observations.py::promote_observation_to_claim, but written
                # inside this transaction so the claim and its source link
                # commit atomically.
                await conn.execute(
                    "INSERT INTO claim_sources (claim_id, observation_id) "
                    "VALUES ($1::uuid, $2::uuid) ON CONFLICT DO NOTHING",
                    node_id, observation_id,
                )
    return str(node_id)


async def relate_claims(
    pool: asyncpg.Pool,
    *,
    from_claim_id: str,
    to_claim_id: str,
    relation: str,
    created_by: str = CREATED_BY,
    propagate: bool = True,
) -> list[str]:
    """
    Record that `from_claim_id` SUPERSEDES or CONTRADICTS `to_claim_id`,
    and flip the target's truth_state to OUT. This is the actual Truth
    Maintenance step: `to_claim_id` is NOT invalidated (t_invalid stays
    NULL, it still exists and is still queryable as history) -- only its
    truth_state changes, so "what did we once believe" and "what do we
    believe now" stay separately answerable from the same row.

    IMPACT PROPAGATION (wired in after the write commits, not inside the
    same transaction as the belief-revision write above -- a downstream
    procedure-staleness side effect must never roll back an otherwise-
    successful truth-maintenance write, and `app.services.claim_impact`'s
    `mark_procedure_stale` calls acquire their own connections from
    `pool`, which would deadlock nested inside this function's own
    `conn.transaction()` block): once `to_claim_id`'s truth_state is OUT,
    any LIVE procedure whose precondition names `to_claim_id` specifically
    (via the `claim_id` field a precondition can carry --
    `procedure_extraction/derive.py::precondition_with_claim`, now
    auto-populated by `derive_preconditions()`) is marked stale via the
    real, existing `mark_procedure_stale` -- that claim's own predicate/
    object can no longer be relied on to still hold. `propagate=False`
    opts out (a caller that already knows no procedure could reference
    this claim, or that wants propagation done separately/batched, is
    free to skip it) -- default `True` is the safe, honest default: a
    superseded/contradicted claim silently leaving a dependent procedure
    looking valid is exactly the gap CONSOLIDATED Phase 3 exists to close.

    Returns the list of procedure row ids marked stale as a result (empty
    if `propagate=False` or nothing referenced this claim).
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

    # B8: a SUPERSEDES/CONTRADICTS edge is a belief-revising event for the
    # target claim -- its stored belief_score/claim_status must follow.
    # Best-effort and lazily imported (claim_belief imports claims):
    # the truth-maintenance write above has already committed and must not
    # be undone by a downstream belief-recompute failure.
    try:
        from app.services import claim_belief

        await claim_belief.recompute_claim_belief(
            pool, to_claim_id, changeset_reason="claim relation changed"
        )
    except Exception:  # pragma: no cover - defensive
        logger.warning(
            "relate_claims: belief recompute failed for claim %s", to_claim_id,
            exc_info=True,
        )

    if not propagate:
        return []
    from app.services.claim_impact import propagate_claim_change
    # propagate_claim_change now returns a dict
    # ({"marked_stale": [...], "explanatory_untouched": [...]}) so it can
    # report explanatory-role refs it deliberately did NOT invalidate.
    # relate_claims' own contract is unchanged: the list of procedure row
    # ids actually marked stale.
    impact = await propagate_claim_change(
        pool, to_claim_id,
        reason=f"claim {to_claim_id} was {relation.lower()} by {from_claim_id} ({created_by})",
        detected_by="claim_impact.relate_claims",
    )
    return impact["marked_stale"]


async def supersede_claim(
    pool: asyncpg.Pool,
    *,
    prior_claim_id: str,
    statement: str,
    task_ids: list[str],
    justification_episode_id: Optional[str] = None,
    created_by: str = CREATED_BY,
    reason: Optional[str] = None,
    subject: Optional[str] = None,
    predicate: Optional[str] = None,
    object: Optional[str] = None,  # noqa: A002 -- matches capture_claim's own field name
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
    Give claims the same version-chain concept `procedures` already has
    (`family_id` + `version`, `supersede_procedure()` in procedures.py) --
    but living inside `properties` JSONB rather than real columns:
    claims share `knowledge_nodes` with 6 other virtual node types, so a
    new `claim_family_id`/`claim_version` column pair would only ever
    serve one of them. No migration for this.

    Mechanics -- deliberately NOT a rewrite of either primitive it
    composes, both already correct and left untouched:
      1. Read `prior_claim_id`'s own properties. If it already carries a
         `claim_family_id` (it is itself a non-root version), the family
         is that value and the new version is `prior.claim_version + 1`.
         Otherwise `prior_claim_id` IS the family root (this is its
         first-ever supersession) -- the family id becomes
         `prior_claim_id` itself, and the new version is 2 (the root is
         implicitly version 1, never written explicitly onto the root
         row -- `get_claim_version_chain` below knows this convention).
      2. Create the new claim via the EXISTING `capture_claim()` --
         reused, not duplicated -- with `claim_family_id`/`claim_version`
         folded into `properties`. `reason`, when given, rides along in
         `properties['supersession_reason']`: `relate_claims()` itself
         takes no properties argument and is not touched to add one.
      3. Call the EXISTING `relate_claims(relation="SUPERSEDES")` to
         write the new->prior edge and flip the prior claim's
         `truth_state` to OUT -- the real Truth Maintenance step, already
         correct, not reinvented here.

    Returns the new claim's id, or None if `capture_claim()` itself
    returned None (none of `task_ids` resolved to a live task_node and
    no `justification_episode_id` was given -- capture_claim's own
    no-op contract, unchanged by going through this wrapper).

    Raises ValueError if `prior_claim_id` does not resolve to a live
    claim row -- unlike `supersede_procedure()`'s silent-None-on-missing
    (a valid concurrent-supersede race there), a caller superseding a
    claim that was never captured is a real caller error, not a race
    this function is expected to absorb.
    """
    prior = await pool.fetchrow(
        "SELECT properties FROM knowledge_nodes WHERE id = $1::uuid "
        "AND node_type = 'claim' AND t_invalid IS NULL",
        prior_claim_id,
    )
    if prior is None:
        raise ValueError(
            f"supersede_claim: no live claim found with id {prior_claim_id!r}"
        )
    prior_props = dict(prior["properties"])
    family_id = prior_props.get("claim_family_id") or str(prior_claim_id)
    new_version = prior_props.get("claim_version", 1) + 1

    merged_properties: dict[str, Any] = {
        **(properties or {}),
        "claim_family_id": family_id,
        "claim_version": new_version,
    }
    if reason is not None:
        merged_properties["supersession_reason"] = reason

    new_claim_id = await capture_claim(
        pool,
        statement=statement,
        task_ids=task_ids,
        justification_episode_id=justification_episode_id,
        created_by=created_by,
        subject=subject,
        predicate=predicate,
        object=object,
        claim_type=claim_type,
        confidence=confidence,
        extraction_version=extraction_version,
        epistemic_status=epistemic_status,
        properties=merged_properties,
        embedder=embedder,
        owner_id=owner_id,
        visibility=visibility,
        scope_type=scope_type,
        scope_entity_id=scope_entity_id,
    )
    if new_claim_id is None:
        return None

    await relate_claims(
        pool,
        from_claim_id=new_claim_id,
        to_claim_id=prior_claim_id,
        relation="SUPERSEDES",
        created_by=created_by,
    )
    return new_claim_id


async def get_claim_version_chain(pool: asyncpg.Pool, claim_id: str) -> list[dict]:
    """
    Walk one claim's version chain (`supersede_claim()`, above) and
    return every version, oldest to newest -- bounded by definition
    (a version chain is a strictly linear structure, not a general
    graph traversal like `get_claim_relations`).

    Works from ANY version id in the chain, not just the root: resolves
    `claim_id`'s own family (its `claim_family_id` if it has one,
    otherwise its own id -- the same root convention `supersede_claim`
    establishes), then reads every live claim row that either IS that
    root or carries that root as its `claim_family_id`.

    Returns `[]` if `claim_id` does not resolve to a live claim at all
    (matches `has_open_conflict_trigger`'s posture of tolerating an
    unresolvable id for a read rather than raising, unlike
    `supersede_claim`'s write-path ValueError).
    """
    anchor = await pool.fetchrow(
        "SELECT properties FROM knowledge_nodes WHERE id = $1::uuid "
        "AND node_type = 'claim' AND t_invalid IS NULL",
        claim_id,
    )
    if anchor is None:
        return []
    family_id = anchor["properties"].get("claim_family_id") or str(claim_id)

    rows = await pool.fetch(
        "SELECT id, properties FROM knowledge_nodes "
        "WHERE node_type = 'claim' AND t_invalid IS NULL "
        "AND (id = $1::uuid OR properties->>'claim_family_id' = $1::text) "
        "ORDER BY COALESCE((properties->>'claim_version')::int, 1) ASC",
        family_id,
    )
    return [dict(r) for r in rows]


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
        # belief_score/belief_method/claim_status (B8/B9): the stored,
        # evidence-derived belief and its DERIVED status projection travel
        # with every current-truth claim read shape, so a caller never has
        # to issue a second query to learn how strongly a claim is held.
        f"SELECT k.id, k.name, k.properties, "
        f"k.belief_score, k.belief_method, k.claim_status "
        f"FROM knowledge_nodes k "
        f"{task_join} "
        f"WHERE k.node_type = 'claim' AND k.t_invalid IS NULL "
        f"AND COALESCE(k.properties->>'truth_state', 'IN') <> 'OUT' "
        f"AND NOT {_DISPUTED_CLAIM_SQL.format(alias='k')} "
        f"AND {vis_sql}",
        *params,
    )
    return [dict(r) for r in rows]


# CONSOLIDATED directive §5's 7-state claim lifecycle (proposed/supported/
# current/disputed/contradicted/stale/retired), computed at read time --
# not a stored column. This is a direct extension of the precedent this
# file already established with `_DISPUTED_CLAIM_SQL`/
# `has_open_conflict_trigger`: "disputed" is real, live-queryable status
# derived from an open conflict trigger, never a cached value that can
# itself drift out of sync with the truth it's supposed to reflect. The
# prior architecture audit (.scratch/final_architecture_audit.md §5)
# explicitly recommends extending that pattern for the remaining states
# rather than adding a new stored `status` column -- done here, no
# migration, no new column.
#
# First, deliberately simple threshold for what counts as "stale": a
# claim's `t_valid` older than this many days with nothing having
# reaffirmed it since. Future work may want this to vary per domain
# (a fast-moving API-compatibility claim staling faster than a stable
# architectural one) -- not attempted in this pass, a single global
# constant is the honest scope of what's built today.
CLAIM_STALE_THRESHOLD_DAYS = 180

# Relations that count as "this claim has been reaffirmed since it was
# captured" for the staleness/supported checks below -- the subset of
# GENERAL_RELATIONS that assert epistemic backing of the target claim,
# not just structural association (DEPENDS_ON/CONDITIONAL_ON/
# APPLIES_TO/etc. describe a relationship, not a reaffirmation).
_REAFFIRMING_RELATIONS = {"SUPPORTS", "GENERALIZES", "DERIVED_FROM"}


async def get_claim_lifecycle_state(
    pool: asyncpg.Pool,
    claim_id: str,
    *,
    as_of: Optional[datetime] = None,
) -> str:
    """
    Compute one claim's lifecycle state from real, existing signals only
    -- `truth_state`, `has_open_conflict_trigger` (the existing disputed
    check, reused verbatim), live `SUPERSEDES`/`CONTRADICTS` edges
    targeting this claim, `t_valid` age, and incoming reaffirming
    relations (`get_claim_relations`). No new stored column; nothing here
    is cached, so this can never itself drift stale.

    Precedence (first match wins -- checked in exactly this order):

      1. `disputed`      -- truth_state == 'IN' AND has_open_conflict_trigger()
                             is True. Reuses that function verbatim.
      2. `retired`        -- truth_state == 'OUT' AND a live SUPERSEDES edge
                             targets this claim (it was superseded, not
                             contradicted).
      3. `contradicted`   -- truth_state == 'OUT', not retired, AND a live
                             CONTRADICTS edge targets this claim. If truth_state
                             is 'OUT' but NEITHER a SUPERSEDES nor a CONTRADICTS
                             edge is found (should not happen given
                             `relate_claims` is the only truth_state writer,
                             handled defensively anyway), this falls back to
                             `retired` as the honest default for "no longer
                             believed, cause unknown" rather than raising.
      4. `stale`          -- truth_state == 'IN', not disputed, AND `t_valid`
                             is older than `CLAIM_STALE_THRESHOLD_DAYS` (relative
                             to `as_of`, default `datetime.now(timezone.utc)`)
                             AND no live incoming SUPPORTS/GENERALIZES/
                             DERIVED_FROM edge exists (nothing has reaffirmed it
                             since).
      5. `supported`      -- truth_state == 'IN', not disputed, not stale, AND
                             at least one live incoming SUPPORTS/GENERALIZES/
                             DERIVED_FROM edge exists.
      6. `current`        -- default: truth_state == 'IN', none of the above
                             apply. The overwhelmingly common case today, and
                             that is expected/correct.
      7. `proposed`       -- NOT reachable from this function. Claims have no
                             approval-like workflow today (unlike procedures'
                             `approval_status` column) -- there is no real,
                             honest signal to compute "proposed" from. A future
                             approval workflow for claims (mirroring
                             `procedures.approval_status`) would be the real
                             prerequisite; that is explicitly out of scope for
                             this pass. This function must never silently
                             invent a "proposed" result for what is actually
                             just `current` -- it simply cannot return
                             "proposed" at all right now.

    `as_of` lets a caller pin "now" for the staleness check (default
    `None` -> `datetime.now(timezone.utc)`) -- tests can pass a fixed
    `t_valid` plus a controlled `as_of` instead of waiting 180 real days.

    Raises `ValueError` if `claim_id` does not resolve to a live claim --
    there is no honest lifecycle state for a claim that does not exist,
    matching `supersede_claim`'s write-path posture (a real caller error,
    not a race this read is expected to silently absorb).
    """
    row = await pool.fetchrow(
        "SELECT properties, t_valid FROM knowledge_nodes "
        "WHERE id = $1::uuid AND node_type = 'claim' AND t_invalid IS NULL",
        claim_id,
    )
    if row is None:
        raise ValueError(
            f"get_claim_lifecycle_state: no live claim found with id {claim_id!r}"
        )
    truth_state = row["properties"].get("truth_state", "IN")

    if truth_state == "IN":
        if await has_open_conflict_trigger(pool, claim_id):
            return "disputed"

        reaffirmations = await get_claim_relations(
            pool, claim_id, direction="incoming", relations=_REAFFIRMING_RELATIONS,
        )
        if reaffirmations:
            return "supported"

        t_valid = row["t_valid"]
        now = as_of or datetime.now(timezone.utc)
        if t_valid is not None and (now - t_valid) > timedelta(days=CLAIM_STALE_THRESHOLD_DAYS):
            return "stale"

        return "current"

    # truth_state == 'OUT'
    retired = await pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM edges WHERE source_table = 'knowledge_nodes' "
        "AND target_id = $1::uuid AND target_table = 'knowledge_nodes' "
        "AND custom_edge_type = 'SUPERSEDES' AND t_invalid IS NULL)",
        claim_id,
    )
    if retired:
        return "retired"

    contradicted = await pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM edges WHERE source_table = 'knowledge_nodes' "
        "AND target_id = $1::uuid AND target_table = 'knowledge_nodes' "
        "AND custom_edge_type = 'CONTRADICTS' AND t_invalid IS NULL)",
        claim_id,
    )
    if contradicted:
        return "contradicted"

    # Defensive fallback: truth_state is OUT but neither edge was found.
    # relate_claims() is the only truth_state writer and always creates
    # one of the two edges, so this should be unreachable -- but "no
    # longer believed, cause unknown" is honestly closer to `retired`
    # than to crashing a caller that just wants a lifecycle string.
    return "retired"
