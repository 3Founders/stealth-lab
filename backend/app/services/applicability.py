"""
Applicability function (ticket 12, memory-substrate map): is procedure P
applicable to current state S? spec.md is emphatic this is NOT semantic
similarity -- the CBR literature the ticket cites draws exactly this
distinction: similarity is a cheap a-priori approximation of reusability
and is often wrong. This module implements the resolved design directly:
a non-compensatory filter cascade on hard constraints (preconditions,
state, scope, exclusions, temporal validity, verification status,
availability, staleness), with semantic similarity ranking only the
survivors -- never compensating for a violated hard constraint.

Deliberately the mirror image of ticket 14 (retrieval): hard constraints
here disqualify; soft signals there fuse. The two must not be unified --
see this module's docstrings on why a violated precondition is not "a
low score", it's a disqualification.

HONEST SCOPE for this pass:
- Preconditions are checked via project_state() (ticket 10), fail-closed
  under CWA -- "no claim found" and "precondition unsatisfied" are the
  same answer, exactly as ticket 12 resolved (rejecting three-valued
  logic to preserve ticket 10's closed-world assumption).
- Coarse tag-based filtering (ticket 12's first, cheap layer) is NOT
  wired in here -- precondition_gate.py's existing tag machinery is a
  separate, already-real mechanism this pass doesn't touch or duplicate.
- Executable environment checks (ticket 12's fourth layer) are deferred,
  exactly as the ticket itself defers them ("need sandboxing and result
  caching, nothing in milestone 1 forces them").
- "Relevant local graph neighbourhood" ranking is NOT implemented --
  that's ticket 14's territory (local retrieval hierarchy), explicitly
  not built in this pass. Soft ranking here is semantic similarity only.
- Version-space scope/exclusion narrowing (automatic) is NOT
  implemented -- ticket 12 itself defers automation to fog and only
  requires *recording* evidence, which this module does not yet do
  either (recording adaptation-failure conditions is real, separate
  follow-on work, honestly not done here).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import asyncpg

from app.services.access import AccessScope, next_param_index, visibility_predicate
from app.services.embeddings import to_pgvector
from app.services.invariants import check_invariants_async
from app.services.relevance_gate import passes_relevance_gate
from app.services.retrieval import fuse_rrf
from app.services.state import project_state

# Ticket 12's cold-start answer: "disable procedure retrieval entirely
# while evidence is thin... fall back to generative planning." The
# ticket does not give a specific number for "enough evidence" --
# this threshold is a judgement call, not derived from the ticket's own
# text, and is flagged as such rather than presented as resolved.
MIN_VERIFIED_PROCEDURES_TO_ENABLE_RETRIEVAL = 1


@dataclass
class ApplicabilityResult:
    procedure_row_id: str
    applicable: bool
    # Names of every hard constraint that failed, e.g. "temporal_validity",
    # "verification_state", "precondition:subject=foo,predicate=bar" --
    # kept even when applicable=False for exactly one reason (fail-fast
    # short-circuit means only the FIRST failure is usually recorded,
    # not every one), so this is diagnostic, not exhaustive.
    failed_constraints: list[str] = field(default_factory=list)
    similarity_score: Optional[float] = None
    # Additive (MCP hardening B1): the candidate row itself, attached only
    # by diagnose_candidates() below -- find_applicable_procedures() never
    # sets this, and every existing construction site
    # (`ApplicabilityResult(row_id, False, [...])`) stays byte-identical
    # since this is a trailing, defaulted field.
    procedure: Optional[dict] = None


async def should_disable_procedure_retrieval(
    pool: asyncpg.Pool, access_scope: Optional[AccessScope] = None,
) -> bool:
    """
    Ticket 12's cold-start answer, made real and callable rather than
    left as a design note: while too few procedures have real recorded
    verification evidence, procedure retrieval should be disabled
    entirely and the caller should fall back to generative planning
    (ticket 15's planner-as-default-not-fallback phase). Returns True
    when retrieval should be DISABLED (few verified procedures exist).

    TENANT-SCOPED (ticket 1.8c): the count goes through access.py's
    visibility_predicate() -- ticket 09's non-negotiable rule that every
    query carries the visibility filter. The gate must answer "does THIS
    viewer have enough verified, visible evidence", not "does the whole
    commons": unlocking retrieval because OTHER tenants' procedures got
    verified ends this viewer's cold start on evidence they may not be
    able to reuse, and symmetrically hides real cold-start state behind
    someone else's progress. Default `unrestricted()` preserves the
    previous global-count behavior for existing internal callers;
    request paths must pass a real scope (same convention as
    project_state()).
    """
    scope = access_scope or AccessScope.unrestricted()
    vis_sql, vis_params = visibility_predicate(scope, param_index=1)
    count = await pool.fetchval(
        "SELECT count(*) FROM procedures WHERE verification_state = 'verified' "
        "AND availability = 'active' AND t_invalid IS NULL "
        f"AND {vis_sql}",
        *vis_params,
    )
    return count < MIN_VERIFIED_PROCEDURES_TO_ENABLE_RETRIEVAL


def _new_state_cache() -> dict:
    """
    One memo table per applicability cascade (ticket 1.8c). Every
    candidate's precondition check used to re-run project_state()
    per predicate per candidate -- and since derived preconditions are
    overwhelmingly project-scoped (`project:<id>`), a single cascade
    re-fetched the SAME projection once per candidate, plus again for
    each additional precondition sharing a subject inside one candidate.
    The projection is deterministic within one cascade call: same pool,
    same as_of, same access scope throughout -- so the first fetch is
    authoritative and every later hit is free.

    Scope of validity is deliberately narrow: this cache lives for one
    find_applicable_procedures()/check_hard_constraints() call only.
    It MUST NOT outlive the request that built it -- a claim written
    mid-flight would otherwise be invisible until eviction, which is a
    stale-read bug, not an optimization.
    """
    return {}


async def _project_state_cached(
    pool: asyncpg.Pool,
    state_cache: dict,
    *,
    subject: str,
    as_of: datetime,
    scope: Optional[AccessScope],
) -> list[dict]:
    key = (subject, as_of)
    cached = state_cache.get(key)
    if cached is None:
        cached = await project_state(pool, subjects=[subject], as_of=as_of, scope=scope)
        state_cache[key] = cached
    return cached


async def _claim_matches_precondition(
    pool: asyncpg.Pool,
    claim_id: str,
    *,
    predicate: Optional[str],
    expected_object: Optional[str],
    as_of: datetime,
    scope: Optional[AccessScope],
) -> bool:
    """
    Real-claim-provenance narrowing (the "confirmed gap" §9 of the
    architecture audit names): when a precondition entry carries a
    claim_id, the check narrows from "does ANY live claim with this
    subject exist that happens to match predicate/object" (the
    claim_id-less, still-default path in check_hard_constraints' loop)
    to "does THIS SPECIFIC claim exist, remain live, and itself carry
    the expected predicate/object" -- a real narrowing, not an ignored
    hint. Only called when precondition.get("claim_id") is truthy;
    claim_id-less preconditions never reach this function, which is
    exactly what keeps that path byte-identical to before.

    "Live" mirrors project_state()'s own bi-temporal + epistemic
    definition exactly (t_valid <= as_of, t_invalid NULL or still in
    the future, truth_state == 'IN') -- a superseded/contradicted claim
    must fail this the same way it disappears from project_state()'s
    projection, not by some looser or stricter rule invented here.
    Fails closed (False) on a malformed/missing claim_id, same CWA
    posture as the rest of this cascade.
    """
    try:
        claim_uuid = str(UUID(str(claim_id)))
    except (ValueError, TypeError, AttributeError):
        return False  # malformed claim_id -- fail closed, never a raised DB error

    scope = scope or AccessScope.unrestricted()
    vis_sql, vis_params = visibility_predicate(scope, param_index=3)
    rows = await pool.fetch(
        f"SELECT properties, t_valid, t_invalid FROM knowledge_nodes "
        f"WHERE id = $1::uuid AND node_type = 'claim' "
        f"AND properties->>'truth_state' = 'IN' "
        f"AND t_valid <= $2 AND (t_invalid IS NULL OR t_invalid > $2) "
        f"AND {vis_sql}",
        claim_uuid, as_of, *vis_params,
    )
    if not rows:
        return False
    properties = dict(rows[0]["properties"])
    return properties.get("predicate") == predicate and properties.get("object") == expected_object


def _scope_matches(procedure_scope: dict, current_scope: dict) -> bool:
    """
    A procedure's `scope` narrows where it applies (ticket 12: "scope
    and exclusions... machine-writable, not just human-authored" --
    representation only in this pass, automated narrowing deferred).
    Empty procedure_scope means unrestricted. Each key present on the
    procedure must have at least one overlapping value with the
    caller's current_scope for that same key; a key the procedure
    doesn't mention imposes no constraint.
    """
    if not procedure_scope:
        return True
    for key, allowed_values in procedure_scope.items():
        current_values = current_scope.get(key)
        if not current_values:
            return False  # procedure requires this key scoped; caller didn't supply it
        allowed_set = set(allowed_values) if isinstance(allowed_values, list) else {allowed_values}
        current_set = set(current_values) if isinstance(current_values, list) else {current_values}
        if not (allowed_set & current_set):
            return False
    return True


def _excluded(procedure_exclusions: list, current_scope: dict) -> bool:
    """Each exclusion entry is {"key": ..., "values": [...]}; the
    procedure is excluded if ANY exclusion's values overlap the
    caller's current_scope for that key."""
    for exclusion in procedure_exclusions:
        key = exclusion.get("key")
        excluded_values = exclusion.get("values", [])
        current_values = current_scope.get(key)
        if not current_values:
            continue
        excluded_set = set(excluded_values) if isinstance(excluded_values, list) else {excluded_values}
        current_set = set(current_values) if isinstance(current_values, list) else {current_values}
        if excluded_set & current_set:
            return True
    return False


async def check_hard_constraints(
    pool: asyncpg.Pool,
    procedure: dict,
    *,
    current_scope: Optional[dict] = None,
    access_scope: Optional[AccessScope] = None,
    require_verified: bool = True,
    as_of: Optional[datetime] = None,
    invariant_bindings: Optional[dict[str, float]] = None,
    state_cache: Optional[dict] = None,
) -> ApplicabilityResult:
    """
    The non-compensatory filter cascade itself. Short-circuits on the
    FIRST failed hard constraint -- this is deliberate, not an
    oversight: a non-compensatory filter doesn't need to enumerate every
    violation, only confirm at least one exists, and short-circuiting is
    also the "fail fast" mitigation ticket 15 names for match-cost
    (don't evaluate every precondition once one has already
    disqualified the procedure).

    `require_verified`: ticket 13's exact wording -- "verified gates
    automatic retrieval; a candidate procedure remains explicitly
    invocable." Pass False for an explicit-invocation caller (a human or
    agent deliberately naming this procedure), True (the default) for
    anything doing automatic candidate selection.

    `current_scope`: caller-supplied dict describing the real current
    task context (e.g. {"repo": [...], "files": [...]}), checked
    against the procedure's own `scope`/`exclusions` fields.

    `state_cache`: optional memo table shared across one cascade (see
    _new_state_cache). Callers that check many candidates against the
    same subjects should pass ONE cache through the whole loop; a None
    cache still dedupes repeated subjects WITHIN this procedure. Valid
    only while pool/as_of/access_scope are unchanged -- which is exactly
    the contract inside one cascade call.
    """
    current_scope = current_scope or {}
    as_of = as_of or datetime.now(timezone.utc)
    state_cache = state_cache if state_cache is not None else {}
    row_id = str(procedure["id"])

    # Temporal validity.
    if procedure["t_invalid"] is not None and procedure["t_invalid"] <= as_of:
        return ApplicabilityResult(row_id, False, ["temporal_validity"])

    # Ticket 13's three orthogonal axes -- all three gate applicability,
    # not just verification_state. A stale-but-verified procedure must
    # not be treated as applicable ("a stale procedure is reused as
    # though verified" is exactly the failure ticket 13 names).
    if procedure["staleness"] == "stale":
        return ApplicabilityResult(row_id, False, ["staleness"])
    if procedure["availability"] != "active":
        return ApplicabilityResult(row_id, False, ["availability"])
    if require_verified and procedure["verification_state"] != "verified":
        return ApplicabilityResult(row_id, False, ["verification_state"])
    # REAL GAP CLOSED (found while wiring the first real caller of this
    # function): migration 20 added approval_status as a column, but
    # nothing ever checked it -- a procedure could reach 'verified' via
    # pure statistics without a human ever having approved it, and
    # automatic retrieval would happily surface it. Tied to the SAME
    # require_verified flag as verification_state, not checked
    # unconditionally: ticket 13's own rule ("verified gates automatic
    # retrieval; a candidate procedure remains explicitly invocable")
    # extends consistently to approval -- explicit invocation
    # (require_verified=False, a human/agent naming this procedure by
    # id) bypasses both gates the same way; only AUTOMATIC selection
    # requires both real evidence AND a human sign-off. Gating this
    # unconditionally would also break test_applicability_e2e.py's
    # existing suite, which predates approval_status and never sets it.
    if require_verified and procedure.get("approval_status") != "approved":
        return ApplicabilityResult(row_id, False, ["approval_status"])

    # Scope / exclusions.
    if not _scope_matches(procedure["scope"] or {}, current_scope):
        return ApplicabilityResult(row_id, False, ["scope"])
    if _excluded(procedure["exclusions"] or [], current_scope):
        return ApplicabilityResult(row_id, False, ["exclusions"])

    # Preconditions -- structured predicates over the claim graph,
    # checked via project_state() (ticket 10), fail-closed under CWA.
    # "No claim found" and "precondition unsatisfied" are the same
    # answer here, deliberately -- ticket 12 rejected three-valued logic
    # specifically to preserve this.
    for precondition in (procedure["preconditions"] or []):
        subject = precondition.get("subject")
        predicate = precondition.get("predicate")
        expected_object = precondition.get("object")
        claim_id = precondition.get("claim_id")
        if not subject:
            continue  # malformed precondition entry -- not this function's job to validate authoring

        # AUTHOR-TIME PROVENANCE narrowing: when this precondition entry
        # names the specific claim that justified it, check THAT claim
        # (real, live, own predicate/object matching) rather than "some
        # claim with this subject happens to match" -- claim_id, when
        # present, disqualifies on its own even if another claim with
        # the same subject/predicate/object exists. Absent (the
        # overwhelmingly common case today -- nothing populates it yet),
        # this falls through to the exact pre-existing subject-based
        # check, unchanged.
        if claim_id:
            satisfied = await _claim_matches_precondition(
                pool, claim_id, predicate=predicate, expected_object=expected_object,
                as_of=as_of, scope=access_scope,
            )
        else:
            claims = await _project_state_cached(
                pool, state_cache, subject=subject, as_of=as_of, scope=access_scope,
            )
            satisfied = any(
                c["properties"].get("predicate") == predicate
                and c["properties"].get("object") == expected_object
                for c in claims
            )
        if not satisfied:
            return ApplicabilityResult(
                row_id, False,
                [f"precondition:subject={subject},predicate={predicate},object={expected_object}"],
            )

    # Numeric invariants -- LAST in the cascade, deliberately. Two
    # reasons, both real: (1) this is the only stage that can invoke a
    # solver, making it the most expensive check here, and
    # find_applicable_procedures() explicitly orders candidates
    # cheapest-to-match-first for exactly that kind of cost reason --
    # running it before the equality checks above would invert that; (2)
    # it is a no-op for every procedure row that exists today (all have
    # empty `invariants`), so it must not sit in front of checks that do
    # real work.
    #
    # The solve runs OFF the event loop (check_invariants_async ->
    # asyncio.to_thread) and under a bounded solver timeout
    # (invariants.DEFAULT_SOLVER_TIMEOUT_MS): a pathological expression
    # must cost one worker thread for milliseconds, never the loop every
    # concurrent request multiplexes on.
    #
    # UNDECIDABLE IS NOT DISQUALIFYING, and that asymmetry is the whole
    # point. At retrieval time nobody has stated an amount yet, so a
    # procedure whose invariant references unbound quantities is the
    # NORMAL case -- treating that as inapplicable would make every
    # invariant-bearing procedure permanently unretrievable, which is the
    # same "looks correct, never fires" failure V1 exists to prevent.
    # Only a definite violation (all variables bound, relation false)
    # disqualifies.
    invariant_result = await check_invariants_async(
        procedure.get("invariants") or [], invariant_bindings or {},
    )
    if invariant_result.violated or invariant_result.errors:
        return ApplicabilityResult(
            row_id, False,
            [f"invariant:{v}" for v in (invariant_result.violated + invariant_result.errors)],
        )

    return ApplicabilityResult(row_id, True, [])


_CANDIDATE_BASE_WHERE = (
    "t_invalid IS NULL AND staleness != 'stale' AND availability = 'active' "
    # db/39: ordinary user-facing retrieval never offers engineering/test/
    # demo-created procedures as candidates -- fail-closed (new rows are
    # excluded by default unless a real capture path explicitly marks
    # itself real, db/40 backfills the known-real exceptions). Direct SQL
    # (tests, admin tooling) is unaffected -- only this cascade's own
    # candidate pool is filtered. See
    # backend/tests/test_retrieval_fixture_isolation_e2e.py and
    # .scratch/final_agent_experiment/corpus-eligibility-review.md.
    "AND is_engineering_fixture = false"
)
# NOTE: excluding `is_engineering_fixture` rows was evaluated as a
# precision lever and REJECTED -- on this corpus the flag marks 2475 of
# 2478 live procedures (the entire bulk-ingested skill set, not just test
# junk), so filtering on it empties product search. The relevance gate
# (services/relevance_gate.py) is the real defence against irrelevant
# hits; migration 45 only guarantees the column exists.


# Lexical candidate leg (plan Part 5). Matches the GIN index built in
# migration 44 -- to_tsvector('english', coalesce(retrieval_document,'')) --
# so it searches the full canonical procedural text (name/goal/steps/tools/
# domain/constraints), not the short goal. plainto_tsquery ANDs every word,
# which almost never matches a natural-language query against one document;
# rewrite ' & ' -> ' | ' so a shared MEANINGFUL word is enough, exactly the
# fix retrieval.py::_lexical_search already applies for the node legs.
# EGRESS: never `SELECT *` a candidate row over the wire. `embedding` is a
# 1024-dim VECTOR that asyncpg ships as ~15 KB of text PER ROW, and
# `retrieval_document` is up to ~5 KB -- neither is read anywhere in the
# cascade or the result shaping (the `<=>` distance runs server-side and
# returns a float, not the vector). Pulling `SELECT *` for
# candidate_pool_size (=200) rows on EVERY search was ~3 MB of pure waste
# per query. This list is every `procedures` column EXCEPT the three heavy
# ones; keep it in sync if a migration adds a column the cascade needs.
PROCEDURE_COLS_NO_HEAVY = (
    "id, procedure_id, family_id, name, goal, steps, parameter_schema, "
    "preconditions, required_state, expected_effects, postconditions, invariants, "
    "failure_conditions, scope, exclusions, verification_state, staleness, "
    "availability, verification_stats, evidence_refs, source_episode_ids, "
    "migrated_from_task_node_id, provenance, domain, domain_payload, version, "
    "t_valid, t_invalid, t_created, t_expired, created_by, created_at, updated_at, "
    "visibility, owner_id, approval_status, approved_by, approved_at, "
    "capability_statement, extracted_by, scope_type, scope_entity_id, "
    "embedding_model_id, embedding_dim, is_engineering_fixture, embedding_provider, "
    "embedding_input_type, embedding_text_hash, retrieval_document_version, "
    "display_name, display_description, display_metadata_version, tenant_id"
)


_PROC_LEXICAL_SQL = (
    f"SELECT id FROM procedures WHERE {_CANDIDATE_BASE_WHERE} "
    "AND to_tsvector('english', coalesce(retrieval_document, '')) "
    "    @@ to_tsquery('english', regexp_replace("
    "        plainto_tsquery('english', $1)::text, ' & ', ' | ', 'g')) "
    "ORDER BY ts_rank("
    "    to_tsvector('english', coalesce(retrieval_document, '')), "
    "    to_tsquery('english', regexp_replace("
    "        plainto_tsquery('english', $1)::text, ' & ', ' | ', 'g'))) DESC "
    "LIMIT $2"
)


async def _fetch_candidate_pool(
    pool: asyncpg.Pool, goal_embedding: Optional[list[float]], candidate_pool_size: int,
    embedding_model_id: Optional[str] = None, goal_text: Optional[str] = None,
    access_scope: Optional[AccessScope] = None,
) -> list[asyncpg.Record]:
    """The pre-filter feeding find_applicable_procedures' cascade -- see
    that function's own docstring for why this fuses cost and relevance
    rather than ranking by cost alone.

    Without a goal_embedding, there is no relevance signal to fuse with,
    so this is EXACTLY the old single-query cost-only fetch, byte for
    byte -- a caller that never had an embedding sees no change at all,
    not even an extra round trip (proven by
    test_full_cascade_shares_one_cache_and_scopes_its_gate, which pins
    the exact fetch-call count).

    With BOTH a goal_embedding and a goal_text, a third candidate leg --
    lexical full-text over the canonical retrieval_document -- is
    RRF-fused alongside cost and vector similarity (plan Part 5). Same
    fuse_rrf primitive, no new scoring formula; this only widens WHAT gets
    offered to the hard-constraint cascade, never what passes it."""
    if goal_embedding is None:
        return await pool.fetch(
            f"SELECT {PROCEDURE_COLS_NO_HEAVY} FROM procedures WHERE {_CANDIDATE_BASE_WHERE} "
            "ORDER BY jsonb_array_length(preconditions) ASC LIMIT $1",
            candidate_pool_size,
        )

    cost_rows = await pool.fetch(
        f"SELECT id FROM procedures WHERE {_CANDIDATE_BASE_WHERE} "
        "ORDER BY jsonb_array_length(preconditions) ASC LIMIT $1",
        candidate_pool_size,
    )
    if embedding_model_id is None:
        similarity_rows = await pool.fetch(
            f"SELECT id FROM procedures WHERE {_CANDIDATE_BASE_WHERE} "
            "AND embedding IS NOT NULL "
            "ORDER BY embedding <=> $1::vector ASC LIMIT $2",
            to_pgvector(goal_embedding), candidate_pool_size,
        )
    else:
        # pgvector has no awareness of model provenance: vectors from two
        # embedding models may have the same dimension but no shared
        # semantic geometry. Only compare vectors from the query's space.
        similarity_rows = await pool.fetch(
            f"SELECT id FROM procedures WHERE {_CANDIDATE_BASE_WHERE} "
            "AND embedding IS NOT NULL AND embedding_model_id = $2 "
            "ORDER BY embedding <=> $1::vector ASC LIMIT $3",
            to_pgvector(goal_embedding), embedding_model_id, candidate_pool_size,
        )
    ranked_lists = [
        ([(r["id"], "procedures", i) for i, r in enumerate(cost_rows)], "cost"),
        ([(r["id"], "procedures", i) for i, r in enumerate(similarity_rows)], "relevance"),
    ]
    if goal_text and goal_text.strip():
        lexical_rows = await pool.fetch(
            _PROC_LEXICAL_SQL, goal_text, candidate_pool_size,
        )
        ranked_lists.append(
            ([(r["id"], "procedures", i) for i, r in enumerate(lexical_rows)], "lexical")
        )
    scores, _ = fuse_rrf(ranked_lists)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    ids = [key[0] for key, _ in ranked[:candidate_pool_size]]

    if not ids:
        return []
    # ACCESS FILTERING BEFORE RANKING (plan Part 20 / ticket 09): the
    # id-gathering legs above are unranked noise, but this is where a
    # candidate id becomes a full row that can be returned, ranked, and
    # fed to relevance_reason(). A row the viewer cannot see is dropped
    # here -- a private procedure never reaches presentation or any
    # explanation field.
    vis_sql, vis_params = visibility_predicate(
        access_scope or AccessScope.unrestricted(), param_index=2
    )
    rows = await pool.fetch(
        f"SELECT {PROCEDURE_COLS_NO_HEAVY} FROM procedures WHERE id = ANY($1::uuid[]) "
        f"AND {_CANDIDATE_BASE_WHERE} AND {vis_sql}",
        ids, *vis_params,
    )
    by_id = {row["id"]: row for row in rows}
    # Preserve fused order -- a row can be legitimately absent here if it
    # went stale/inactive between the id-only fetch and this one; skip it
    # rather than raise, same tolerance the rest of this cascade has for
    # a candidate disappearing mid-match.
    return [by_id[i] for i in ids if i in by_id]


async def _capability_ranked_hits(
    pool: asyncpg.Pool, survivors: list[dict],
) -> list[tuple[UUID, str, int]]:
    """
    Phase 3 capability wiring (procedure_extraction/capability.py, real
    Wilson-lower-bound math, previously ZERO real callers): a ranking
    signal over hard-cascade SURVIVORS only -- capability never
    disqualifies (that stays check_hard_constraints' job, unchanged), it
    only ranks among procedures already proven applicable, exactly like
    the similarity signal it is fused against in find_applicable_
    procedures below. This is the founder's own G2 example made real:
    a lower-similarity, higher-capability applicable procedure can now
    outrank a higher-similarity, weak-capability one.

    Reuses, does not reinvent:
      - procedure_extraction/failure_handlers.py::capability_for_stream(),
        the SAME recompute check_procedure_reuse's capability_note (this
        module, below) and handle_capability_demotion already call over
        the SAME target_type='procedure' evidence rows
        procedure_evidence_stats (db/24) counts -- not a second
        capability computation living in two places.
      - the query shape (evidence_type IN DEMOTION_EVIDENCE_TYPES,
        outcome_status IN ('success','failure'), no direction filter --
        outcome_to_evidence() defaults a failure's direction to
        'contradicts', so filtering on direction='supports' silently
        drops every real failure from the stream; outcome_status alone
        already restricts to real outcome-bearing rows)
        copied verbatim from check_procedure_reuse's own stream fetch a
        few hundred lines below, so the two call sites can never
        silently disagree about what counts as an attempt.
      - retrieval.py::fuse_rrf() at the call site below -- no second
        ranking mechanism, per Rule 6.

    Lazy import for the same reason check_procedure_reuse's is lazy:
    failure_handlers.py imports THIS module (`_scope_matches`) at its
    own module level, so a module-level import here would be circular.

    BATCHED: one evidence query for the whole survivor set, not one
    round trip per candidate -- the candidate pool can be dozens of rows
    and this ranking step must not turn into an N+1.
    """
    from app.services.procedure_extraction.failure_handlers import (
        DEMOTION_EVIDENCE_TYPES, DEMOTION_STREAM_LIMIT, capability_for_stream,
    )
    if not survivors:
        return []

    ids = [s["id"] for s in survivors]
    types_sql = ", ".join(f"'{t}'" for t in DEMOTION_EVIDENCE_TYPES)
    rows = await pool.fetch(
        f"""
        SELECT target_id, target_version, outcome_status, context_key, independence_group
        FROM evidence
        WHERE target_type = 'procedure'
          AND target_id = ANY($1::uuid[])
          AND t_invalid IS NULL
          AND evidence_type IN ({types_sql})
          AND outcome_status IN ('success', 'failure')
        ORDER BY t_created ASC, id ASC
        LIMIT {int(DEMOTION_STREAM_LIMIT)}
        """,
        ids,
    )
    # Evidence is pinned to the procedure's OWN version (evidence.py's
    # hard rule -- "procedure-targeted evidence pins target_version") --
    # group by (target_id, target_version), never target_id alone, so a
    # candidate never inherits a prior version's outcome stream.
    by_key: dict[tuple[str, object], list] = {}
    for row in rows:
        key = (str(row["target_id"]), row["target_version"])
        by_key.setdefault(key, []).append(row)

    scored: list[tuple[str, float]] = []
    for s in survivors:
        stream = by_key.get((str(s["id"]), s.get("version")), [])
        record = capability_for_stream("procedure", str(s["id"]), stream)
        scored.append((str(s["id"]), record.p_estimate))

    # Highest P first; Python's sort is stable so ties (e.g. all-zero,
    # no evidence anywhere) preserve the cascade's own survivor order
    # rather than reshuffling arbitrarily.
    scored.sort(key=lambda kv: kv[1], reverse=True)
    return [(UUID(pid), "procedures", i) for i, (pid, _) in enumerate(scored)]


async def find_applicable_procedures(
    pool: asyncpg.Pool,
    *,
    goal_embedding: Optional[list[float]] = None,
    current_scope: Optional[dict] = None,
    access_scope: Optional[AccessScope] = None,
    require_verified: bool = True,
    limit: int = 10,
    candidate_pool_size: int = 200,
    invariant_bindings: Optional[dict[str, float]] = None,
    embedding_model_id: Optional[str] = None,
    goal_text: Optional[str] = None,
) -> list[dict]:
    """
    Real ticket-12 pipeline, end to end: cold-start gate, then the
    non-compensatory hard filter, then semantic-similarity ranking of
    survivors ONLY (never the reverse order -- ranking before filtering
    would let a high similarity score influence which candidates get
    fetched, which is a milder form of the criterion-compensation
    antipattern this whole design exists to avoid).

    Returns [] immediately if should_disable_procedure_retrieval()
    holds -- an empty list is the caller's real signal to fall back to
    generative planning (ticket 15), not an error.

    INDEX FRESHNESS (G14 / spec B37): the vector + lexical legs are
    advisory. Every surviving candidate is re-fetched from its live
    `procedures` row (the `visibility_predicate` re-fetch below, and
    `_resolve_live_procedure` on the MCP path) before the hard-constraint
    cascade and before ranking, so an embedding that lags its source text
    can never cause selection on outdated canonical content -- there is no
    detached index to be stale against. `procedure_index_lag` (migration
    72) + `app.services.index_freshness.get_index_lag` surface rows whose
    embedding is behind canonical, for the resumable
    `scripts/backfill_procedure_embeddings.py` rebuild.

    `candidate_pool_size`: ticket 15's match-cost-aware ordering ("order
    candidate procedures cheapest-to-match first") is preserved, but it is
    no longer the ONLY signal choosing which candidates are even fetched.

    BUG FIX (this pass): the candidate pre-filter used to rank by
    precondition-count ALONE, with zero embedding term -- at N procedures,
    a `LIMIT candidate_pool_size` on that ordering makes most of the
    corpus permanently unreachable regardless of relevance, and the
    exclusion gets WORSE as the corpus grows, not better. This does not
    touch this module's own "not semantic similarity" principle (module
    docstring, top of file) -- the hard-constraint cascade below still
    decides pass/fail on constraints alone, unchanged. What changes is
    only WHICH candidates are even offered to that cascade: when a
    `goal_embedding` is available, this now RRF-fuses two independent
    candidate rankings (cost-cheapest, and embedding-nearest) via
    `retrieval.py::fuse_rrf` -- the same fusion primitive `retrieval.py`
    already uses for its own soft-signal ranking, reused here rather than
    reinvented, applied at the pre-filter stage where fusing signals is
    legitimate (deciding what to LOOK AT), not at the cascade stage where
    it would not be (deciding what PASSES).
    """
    # COLD-START GATE -- skipped on an explicit unverified opt-in.
    #
    # The gate exists so that BY DEFAULT a substrate with too little
    # verification evidence falls back to generative planning rather than
    # surfacing procedures nothing has earned trust in. That default is
    # right and is unchanged: every require_verified=True caller still
    # hits it, including retrieve_precedent's own path below.
    #
    # But require_verified=False is not the default -- it is a caller
    # explicitly asking to consider unverified candidates on their own
    # merits (find_best_way's `allow_unverified_procedures`, ticket 13's
    # named opt-in). Running the gate first made that flag UNREACHABLE and
    # created a chicken-and-egg the loop cannot bootstrap out of:
    #     retrieval is disabled until >=1 VERIFIED procedure exists
    #  -> a procedure is verified only by accruing execution evidence
    #  -> evidence accrues only when a matched procedure is actually USED
    #  -> nothing can ever match, so nothing can ever become verified.
    #
    # Measured 2026-08-28 with a real model (gemma-4-31B-it) against a
    # real repo: a freshly-extracted procedure stayed invisible to
    # find_best_way even with allow_unverified_procedures=True, and matched
    # immediately once this gate was bypassed -- one blocker, isolated.
    # Opting in IS the cold-start case, so the opt-in must win over the
    # gate that exists to manage cold start.
    if require_verified and await should_disable_procedure_retrieval(pool, access_scope):
        return []

    rows = await _fetch_candidate_pool(
        pool, goal_embedding, candidate_pool_size, embedding_model_id,
        goal_text=goal_text, access_scope=access_scope,
    )

    # ONE memo table + ONE pinned timestamp for the whole cascade: same
    # pool, same as_of, same access scope throughout -- exactly the
    # validity contract _new_state_cache documents. Letting each
    # candidate default its own now() would put different microsecond
    # timestamps in the cache keys and quietly defeat the memo.
    cascade_as_of = datetime.now(timezone.utc)
    state_cache = _new_state_cache()
    survivors = []
    for row in rows:
        procedure = dict(row)
        result = await check_hard_constraints(
            pool, procedure, current_scope=current_scope, access_scope=access_scope,
            require_verified=require_verified, invariant_bindings=invariant_bindings,
            as_of=cascade_as_of, state_cache=state_cache,
        )
        if result.applicable:
            survivors.append(procedure)

    if not survivors:
        return []

    if goal_embedding is None:
        # No query embedding supplied -- return hard-filter survivors
        # unranked rather than fabricating a similarity order. Real,
        # honest degradation, not silently swapped for e.g. recency.
        # Capability's own signal is deliberately NOT fused here either:
        # it costs a real evidence query, and the byte-for-byte "no
        # embedding -> no extra round trip" contract this cascade already
        # keeps for _fetch_candidate_pool's own pre-filter (proven by
        # test_full_cascade_shares_one_cache_and_scopes_its_gate's pinned
        # fetch-call count) extends the same way to this ranking step.
        return survivors[:limit]

    survivor_ids = [s["id"] for s in survivors]
    if embedding_model_id is None:
        ranked = await pool.fetch(
            "SELECT id, 1 - (embedding <=> $1::vector) AS similarity FROM procedures "
            "WHERE id = ANY($2::uuid[]) AND embedding IS NOT NULL "
            "ORDER BY embedding <=> $1::vector ASC "
            "LIMIT $3",
            to_pgvector(goal_embedding), survivor_ids, limit,
        )
    else:
        ranked = await pool.fetch(
            "SELECT id, 1 - (embedding <=> $1::vector) AS similarity FROM procedures "
            "WHERE id = ANY($2::uuid[]) AND embedding IS NOT NULL "
            "AND embedding_model_id = $3 "
            "ORDER BY embedding <=> $1::vector ASC "
            "LIMIT $4",
            to_pgvector(goal_embedding), survivor_ids, embedding_model_id, limit,
        )
    ranked_ids = {str(r["id"]): r["similarity"] for r in ranked}

    # Phase 3 -- fuse similarity with the real capability signal via the
    # SAME fuse_rrf primitive this module's own _fetch_candidate_pool
    # already reuses for its cost/relevance pre-filter, rather than a
    # second ranking mechanism (Rule 6). similarity_hits carries only the
    # survivors the embedding query actually ranked; capability_hits
    # covers EVERY survivor (capability_for_stream degrades honestly to
    # p_estimate=0.0 on an empty stream), so a survivor with no stored
    # embedding at all is no longer silently unranked -- it now ranks on
    # capability alone instead of being appended last unconditionally.
    similarity_order = sorted(ranked_ids.items(), key=lambda kv: kv[1], reverse=True)
    similarity_hits: list[tuple[UUID, str, int]] = [
        (UUID(rid), "procedures", i) for i, (rid, _sim) in enumerate(similarity_order)
    ]
    capability_hits = await _capability_ranked_hits(pool, survivors)
    fused_scores, _matched = fuse_rrf(
        [(similarity_hits, "relevance"), (capability_hits, "capability")]
    )
    fused_order = sorted(fused_scores.items(), key=lambda kv: kv[1], reverse=True)

    survivors_by_id = {str(s["id"]): s for s in survivors}
    result_list = []
    for (pid, _table), _score in fused_order:
        rid = str(pid)
        if rid not in survivors_by_id:
            continue
        # Retrieval relevance gate (services/relevance_gate.py -- the ONE
        # measured cutoff, RELEVANCE_GATE_MIN_SIMILARITY): a candidate whose
        # real embedding similarity is below it never SURFACES as a result,
        # even though it still legitimately took part in the RRF
        # fusion/ranking math above (Rule 6's "rank survivors, don't
        # re-filter" contract stays intact). A candidate with NO stored
        # embedding (capability-only ranking, the "honest degradation" path
        # a few lines up) is NOT dropped -- passes_relevance_gate(None) is
        # True, because there is no real similarity value to judge it
        # against. This is the same gate domain_search applies to search
        # results; applying it here too means every retrieval entrypoint
        # (search_global AND a direct find_applicable_procedures / MCP
        # caller) gets one consistent relevance floor, not two.
        if not passes_relevance_gate(ranked_ids.get(rid)):
            continue
        proc = dict(survivors_by_id[rid])
        if rid in ranked_ids:
            proc["_similarity_score"] = ranked_ids[rid]
        result_list.append(proc)
        if len(result_list) >= limit:
            break
    return result_list


async def diagnose_candidates(
    pool: asyncpg.Pool,
    *,
    goal_embedding: Optional[list[float]] = None,
    current_scope: Optional[dict] = None,
    access_scope: Optional[AccessScope] = None,
    require_verified: bool = True,
    invariant_bindings: Optional[dict[str, float]] = None,
    embedding_model_id: Optional[str] = None,
    goal_text: Optional[str] = None,
    limit: int = 3,
    candidate_pool_size: int = 200,
) -> list[ApplicabilityResult]:
    """
    Additive diagnostic sibling to find_applicable_procedures() (MCP
    hardening B1: `find_best_way`'s router needs to know WHY the single
    BEST-MATCHING candidate did not qualify -- e.g. to distinguish
    "genuinely inapplicable" from "blocked on an unknown precondition,
    ask". find_applicable_procedures() itself only ever returns
    survivors and intentionally discards every failure reason (see its
    own docstring), so re-implementing that discarding is not an option
    -- but its own `_fetch_candidate_pool` pre-filter is DELIBERATELY
    cost-aware (RRF-fuses "cheapest to verify" with relevance), which is
    right for THAT function's job (find something applicable, cheaply)
    and wrong for this one: over a large corpus, procedures with fewer
    preconditions crowd the cost leg and can bury the actual closest
    semantic match outside a small `limit`, even though check_hard_
    constraints() would have flagged its one precondition as UNKNOWN
    rather than genuinely disqualifying -- confirmed live (a freshly
    captured procedure whose embedding is a near-exact match for its own
    goal text still ranked outside the top 3 of the cost-fused pool in a
    ~2500-row corpus). "The single best-matching procedure" is a
    nearest-neighbor question, not a cost-aware one, so this ranks by
    embedding similarity ALONE (falling back to _fetch_candidate_pool's
    existing cost/lexical fusion only when no goal_embedding is given --
    there is no relevance signal to rank by in that case, matching that
    function's own honest-degradation precedent). Reuses the SAME
    _CANDIDATE_BASE_WHERE/PROCEDURE_COLS_NO_HEAVY/visibility_predicate
    building blocks find_applicable_procedures() itself is built from --
    a different ranking query, not a new access-control or filtering
    mechanism.

    Does not change find_applicable_procedures()'s behavior, callers, or
    return value in any way -- this is a pure, non-mutating read.
    """
    if require_verified and await should_disable_procedure_retrieval(pool, access_scope):
        return []

    if goal_embedding is not None:
        vis_sql, vis_params = visibility_predicate(
            access_scope or AccessScope.unrestricted(),
            param_index=4 if embedding_model_id is not None else 3,
        )
        if embedding_model_id is None:
            rows = await pool.fetch(
                f"SELECT {PROCEDURE_COLS_NO_HEAVY} FROM procedures "
                f"WHERE {_CANDIDATE_BASE_WHERE} AND embedding IS NOT NULL AND {vis_sql} "
                "ORDER BY embedding <=> $1::vector ASC LIMIT $2",
                to_pgvector(goal_embedding), limit, *vis_params,
            )
        else:
            rows = await pool.fetch(
                f"SELECT {PROCEDURE_COLS_NO_HEAVY} FROM procedures "
                f"WHERE {_CANDIDATE_BASE_WHERE} AND embedding IS NOT NULL "
                f"AND embedding_model_id = $2 AND {vis_sql} "
                "ORDER BY embedding <=> $1::vector ASC LIMIT $3",
                to_pgvector(goal_embedding), embedding_model_id, limit, *vis_params,
            )
    else:
        rows = (await _fetch_candidate_pool(
            pool, goal_embedding, candidate_pool_size, embedding_model_id,
            goal_text=goal_text, access_scope=access_scope,
        ))[:limit]

    cascade_as_of = datetime.now(timezone.utc)
    state_cache = _new_state_cache()
    results: list[ApplicabilityResult] = []
    for row in rows[:limit]:
        procedure = dict(row)
        result = await check_hard_constraints(
            pool, procedure, current_scope=current_scope, access_scope=access_scope,
            require_verified=require_verified, invariant_bindings=invariant_bindings,
            as_of=cascade_as_of, state_cache=state_cache,
        )
        result.procedure = procedure
        results.append(result)
    return results


# The relevance floor lives in ONE place: services/relevance_gate.py's
# measured RELEVANCE_GATE_MIN_SIMILARITY (swept from
# tests/data/retrieval_eval_v1.jsonl, revised only by re-running
# scripts/eval_retrieval_quality.py). find_applicable_procedures applies
# it via passes_relevance_gate() above so a direct caller and a
# search_global caller see the same cutoff. The earlier hand-calibrated
# _MIN_RELEVANCE_SIMILARITY=0.45 (a second, competing source of truth)
# was removed 2026-09-09.


# ===========================================================================
# demo.md C4 -- retrieve_precedent (MCP server, backend/app/mcp_server/
# server.py): a real, disclosed gap (bootstrap_demo.py's own "HONEST
# FINDING" / Question #7) is that retrieve_precedent's candidate set --
# reuse_detection._vector_candidates -- only ever queries task_nodes and
# knowledge_nodes; `procedures` is never in it, even though the embedding
# column, HNSW index, and a real vector-similarity query over procedures
# all already exist (right above, inside find_applicable_procedures).
# This function is the procedures-table counterpart retrieve_precedent
# fuses in alongside _vector_candidates' results, closing that gap
# without duplicating either query path.
# ===========================================================================


async def verified_procedure_candidates(
    pool: asyncpg.Pool,
    query_vec: list[float],
    access_scope: Optional[AccessScope] = None,
    limit: int = 5,
) -> list[dict]:
    """
    Verified-only procedure candidates for retrieve_precedent. Reuses,
    does not duplicate:
      - should_disable_procedure_retrieval() -- the SAME cold-start gate
        find_applicable_procedures() itself opens with, called here
        verbatim rather than reimplemented, so retrieve_precedent and
        automatic candidate selection can never disagree about "is
        retrieval unlocked yet."
      - the verified-only WHERE shape check_hard_constraints'
        require_verified=True branch enforces everywhere else
        (verification_state='verified', approval_status='approved',
        availability='active', not stale, live) -- and the SAME
        `1 - (embedding <=> $1::vector)` ranking query shape used just
        above in find_applicable_procedures.

    2026-08-27 founder ruling: considered surfacing unverified/candidate
    procedures here too -- REJECTED. Letting retrieve_precedent surface a
    not-yet-verified procedure would let a caller treat "similar
    precedent found" as an implicit reuse signal without ever going
    through check_procedure_reuse's real applicability/precondition
    cascade -- exactly the false-reuse failure mode this whole substrate
    exists to prevent. See .scratch/build-board.md's CORE-B queue (this
    item) for the full reasoning; revisit only with an explicit new
    founder ruling, not unilaterally.

    Deliberately NOT find_applicable_procedures(): that runs the full
    hard-constraint cascade (scope/exclusions/preconditions/invariants
    evaluated against a caller-supplied `current_scope`), and
    retrieve_precedent's only input is free-text `query` -- it has no
    current_scope to supply. Calling find_applicable_procedures() with an
    empty one would silently exclude every procedure with a non-empty
    `scope` field (_scope_matches treats an unsupplied key as a hard
    fail, not "unconstrained") -- a second, different bug, not this one.
    """
    if await should_disable_procedure_retrieval(pool, access_scope):
        return []

    scope = access_scope or AccessScope.unrestricted()
    vis_sql, vis_params = visibility_predicate(scope, param_index=2)
    limit_index = next_param_index(scope, 2)
    rows = await pool.fetch(
        f"SELECT id, name, goal, 1 - (embedding <=> $1::vector) AS similarity "
        f"FROM procedures "
        f"WHERE verification_state = 'verified' AND approval_status = 'approved' "
        f"AND availability = 'active' AND staleness != 'stale' "
        f"AND t_invalid IS NULL AND embedding IS NOT NULL "
        f"AND {vis_sql} "
        f"ORDER BY similarity DESC LIMIT ${limit_index}",
        to_pgvector(query_vec), *vis_params, limit,
    )
    return [dict(r) for r in rows]


# ===========================================================================
# demo.md C5 -- "refusal with receipts": ALLOW / WOULD_REFUSE a SPECIFICALLY
# NAMED procedure right now, for the MCP server's check_procedure tool
# (backend/app/mcp_server/server.py -- thin wrapper, no decision logic of
# its own). Audit mode only (demo.md §2 item 3 / §4): this is diagnostic
# output for the calling agent, it never blocks anything.
#
# Deliberately reuses, not reinvents:
#   - check_hard_constraints() above IS the applicability decision -- same
#     non-compensatory cascade an automatic find_applicable_procedures()
#     call would run, just against ONE named procedure instead of a
#     candidate pool.
#   - procedure_extraction/failure_handlers.capability_for_stream() for the
#     capability_note -- the SAME evidence-stream recompute
#     handle_capability_demotion already uses over the SAME
#     target_type='procedure' rows procedure_evidence_stats (db/24) counts,
#     imported lazily below to avoid a real import cycle: that package's
#     __init__.py itself imports THIS module (`_scope_matches`), so a
#     module-level import here would try to finish loading applicability.py
#     before it has, which Python cannot do.
#
# NOT reused: precondition_gate.py's postcondition Jaccard check. That gate
# needs STRUCTURED postcondition tags on BOTH sides (candidate AND query);
# `query` here is free natural-language text with no extracted tags, so
# calling postconditions_compatible(tags, None) would trivially return True
# every time (query_postconditions=None short-circuits it) -- dead code
# dressed up as a check, not a real gate. It stays a real gate at its
# actual call site (hierarchy.py's batch match, where both sides really do
# carry structured tags).
# ===========================================================================

# Ticket 13's own named exception: "verified gates automatic retrieval; a
# candidate procedure remains explicitly invocable." check_procedure's
# whole point is a caller naming ONE procedure_id explicitly -- exactly
# that case, not automatic candidate selection -- so require_verified is
# deliberately False here, unlike find_applicable_procedures' default.
# Refusing on verification_state/approval_status alone would conflate "not
# yet promoted" with "actively unsafe to reuse", which is exactly the
# distinction ticket 13 draws; the unresolved "not enough evidence yet"
# question is answered separately, honestly, by capability_note below.
CHECK_PROCEDURE_REQUIRE_VERIFIED = False


class ProcedureNotFound(Exception):
    """procedure_id didn't resolve to a live (t_invalid IS NULL) procedures
    row -- a caller error (wrong/stale id), distinct from a WOULD_REFUSE
    verdict about a real procedure that does exist."""


@dataclass
class ProcedureVerdict:
    """demo.md §3's pinned check_procedure response shape, field for
    field -- the MCP tool json.dumps()'s this directly, unmodified."""

    verdict: str            # "ALLOW" | "WOULD_REFUSE"
    procedure: str
    reason: str
    evidence: list[str]
    capability_note: str


def _parse_precondition_constraint(constraint: str) -> dict[str, Optional[str]]:
    """Reverses ApplicabilityResult's own
    f"precondition:subject={s},predicate={p},object={o}" format string.
    v1, deliberately narrow like precondition_gate.py's own tag matching:
    a subject/predicate/object containing a literal ',' or '=' would
    confuse this parser -- no real procedure data does today (confirmed by
    the same grep discipline this codebase already applies elsewhere), so
    this is a disclosed limit, not silently assumed safe."""
    body = constraint[len("precondition:"):]
    parts: dict[str, Optional[str]] = {}
    for piece in body.split(","):
        if "=" not in piece:
            continue
        key, _, value = piece.partition("=")
        parts[key] = value if value != "None" else None
    return parts


async def _precondition_narrative(
    pool: asyncpg.Pool, constraint: str, procedure_row_id: str,
) -> tuple[str, list[str]]:
    """Builds the demo.md-style rich reason for a failed precondition:
    names the real current claim and, when one exists, the real claim
    that superseded it (claims.py's relate_claims() SUPERSEDES edge --
    read-only here, no new write logic).

    HONEST GAP, disclosed rather than papered over: 1.9c's universal
    ChangeSet coverage explicitly scoped supersession of knowledge_nodes
    OUT of v1 (db/25_universal_changesets.sql's own header: "Supersession
    and revision paths for knowledge_nodes... arrive with their §20
    revision machinery"). relate_claims() writes the SUPERSEDES edge
    directly, with no change_sets row -- so there is no changeset id to
    cite for a claim supersession today. evidence cites the real claim
    ids (edges.source_id/target_id) instead of a changeset id in that
    case; it is honest, verifiable evidence, just not the SAME kind
    demo.md's illustrative example shows.
    """
    parts = _parse_precondition_constraint(constraint)
    subject, predicate, expected = parts.get("subject"), parts.get("predicate"), parts.get("object")

    claim_row = await pool.fetchrow(
        "SELECT id, properties FROM knowledge_nodes "
        "WHERE node_type = 'claim' AND properties->>'subject' = $1 "
        "AND properties->>'predicate' = $2 AND t_invalid IS NULL "
        "ORDER BY t_valid DESC LIMIT 1",
        subject, predicate,
    )
    if claim_row is None:
        return (
            f"no claim satisfies precondition subject={subject!r} predicate={predicate!r} "
            f"object={expected!r} -- CWA fail-closed (state.py/ticket 10: 'no claim found' "
            f"and 'precondition unsatisfied' are the same answer)",
            [f"procedure:{procedure_row_id}"],
        )

    claim_id = str(claim_row["id"])
    props = dict(claim_row["properties"] or {})
    if props.get("truth_state") != "OUT":
        # Believed IN, but the recorded object disagrees with what this
        # procedure's precondition requires -- a real mismatch, not a
        # supersession story.
        return (
            f"precondition subject={subject!r} predicate={predicate!r} currently holds "
            f"object={props.get('object')!r}, not the required {expected!r} (claim {claim_id})",
            [f"claim:{claim_id}"],
        )

    superseder = await pool.fetchrow(
        "SELECT source_id FROM edges WHERE edge_type = 'SUPERSEDES' "
        "AND target_id = $1::uuid AND target_table = 'knowledge_nodes' "
        "ORDER BY id DESC LIMIT 1",
        claim_row["id"],
    )
    if superseder is None:
        return (
            f"precondition claim {claim_id} (subject={subject!r} predicate={predicate!r} "
            f"object={expected!r}) is no longer believed (truth_state=OUT) -- no SUPERSEDES "
            f"edge recorded against it",
            [f"claim:{claim_id}"],
        )

    superseder_id = str(superseder["source_id"])
    return (
        f"precondition claim {claim_id} (subject={subject!r} predicate={predicate!r} "
        f"object={expected!r}) superseded by {superseder_id}",
        [f"claim:{claim_id}", f"claim:{superseder_id}"],
    )


async def _verdict_narrative(
    pool: asyncpg.Pool, procedure: dict, result: ApplicabilityResult,
) -> tuple[str, list[str]]:
    row_id = str(procedure["id"])
    if result.applicable:
        n_preconditions = len(procedure.get("preconditions") or [])
        return (
            f"all real hard constraints satisfied (temporal validity, staleness, "
            f"availability, scope/exclusions, {n_preconditions} precondition(s), "
            f"invariants) -- require_verified={CHECK_PROCEDURE_REQUIRE_VERIFIED} "
            f"(explicit invocation, ticket 13)",
            [f"procedure:{row_id}"],
        )

    constraint = result.failed_constraints[0] if result.failed_constraints else "unknown"

    if constraint == "staleness":
        return (f"procedure is marked stale (applicability.py's staleness axis, ticket 13)",
                [f"procedure:{row_id}"])
    if constraint == "temporal_validity":
        return (f"procedure version is no longer temporally valid as of this call "
                f"(t_invalid={procedure.get('t_invalid')})", [f"procedure:{row_id}"])
    if constraint == "availability":
        return (f"procedure availability is {procedure.get('availability')!r}, not 'active'",
                [f"procedure:{row_id}"])
    if constraint == "scope":
        return (f"caller's current context does not satisfy this procedure's required "
                f"scope ({procedure.get('scope')})", [f"procedure:{row_id}"])
    if constraint == "exclusions":
        return ("caller's current context matches a recorded exclusion on this procedure",
                [f"procedure:{row_id}"])
    if constraint.startswith("precondition:"):
        return await _precondition_narrative(pool, constraint, row_id)
    if constraint.startswith("invariant:"):
        return (f"numeric invariant violated: {constraint[len('invariant:'):]}",
                [f"procedure:{row_id}"])
    return (f"hard constraint failed: {constraint}", [f"procedure:{row_id}"])


async def check_procedure_reuse(
    pool: asyncpg.Pool,
    *,
    procedure_id: str,
    current_scope: Optional[dict] = None,
    access_scope: Optional[AccessScope] = None,
) -> ProcedureVerdict:
    """
    demo.md C5's real decision core. ALLOW or WOULD_REFUSE reuse of the
    procedure named by `procedure_id` (the STABLE `procedures.procedure_id`
    handle, constant across a version chain -- NOT a per-version row id),
    resolved to its current live version (t_invalid IS NULL).

    `current_scope`: caller's real current task context, forwarded
    unchanged to check_hard_constraints()'s own `current_scope` param
    (see that function's docstring). Defaults to None/{} -- unrestricted
    -- so existing callers that don't have a real scope to supply (and
    the offline test suite) are unaffected. A procedure with a real
    `scope` requirement WILL fail the scope gate before ever reaching the
    precondition cascade if the caller doesn't supply a current_scope
    that satisfies it -- pass the real one when the point of the call is
    to exercise preconditions, not scope.

    Raises ProcedureNotFound if procedure_id is not a valid UUID or does
    not resolve to a live row -- the MCP tool wrapper turns that into a
    plain "REFUSED: ..." string, same style as this server's other tools'
    bad-input handling.
    """
    # Lazy import -- see this section's header comment for why a
    # module-level import here would be circular.
    from app.services.procedure_extraction.failure_handlers import (
        DEMOTION_EVIDENCE_TYPES, DEMOTION_STREAM_LIMIT, capability_for_stream,
    )

    try:
        proc_uuid = UUID(str(procedure_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ProcedureNotFound(f"{procedure_id!r} is not a valid procedure id (UUID)") from exc

    row = await pool.fetchrow(
        f"SELECT {PROCEDURE_COLS_NO_HEAVY} FROM procedures "
        "WHERE procedure_id = $1::uuid AND t_invalid IS NULL",
        proc_uuid,
    )
    if row is None:
        raise ProcedureNotFound(f"no live procedure for procedure_id={procedure_id}")
    procedure = dict(row)

    result = await check_hard_constraints(
        pool, procedure, current_scope=current_scope, access_scope=access_scope,
        require_verified=CHECK_PROCEDURE_REQUIRE_VERIFIED,
    )
    reason, evidence = await _verdict_narrative(pool, procedure, result)

    # Same attempt discipline procedure_evidence_stats (db/24) and
    # handle_capability_demotion already use: target_type='procedure',
    # joined on THIS version row's own (id, version) pair.
    types_sql = ", ".join(f"'{t}'" for t in DEMOTION_EVIDENCE_TYPES)
    stream_rows = await pool.fetch(
        f"""
        SELECT outcome_status, context_key, independence_group
        FROM evidence
        WHERE target_type = 'procedure'
          AND target_id = $1::uuid AND target_version = $2
          AND t_invalid IS NULL
          AND evidence_type IN ({types_sql})
          AND outcome_status IN ('success', 'failure')
        ORDER BY t_created ASC, id ASC
        LIMIT {int(DEMOTION_STREAM_LIMIT)}
        """,
        procedure["id"], procedure["version"],
    )
    capability = capability_for_stream("procedure", str(procedure["id"]), stream_rows)
    routing = capability.routing.value if hasattr(capability.routing, "value") else str(capability.routing)
    capability_note = (
        f"{capability.success_count} successes / {capability.evidence_count} attempts "
        f"recorded (P_lower={capability.p_estimate:.2f}, level={capability.level_label}, "
        f"routing={routing})"
    )
    # At 0 recorded attempts, `routing` is just capability_for_stream's
    # generic no-evidence-yet default -- it does NOT drive this verdict
    # (result.applicable does, above), but sat next to a fresh ALLOW it
    # reads as self-contradictory ("ALLOW ... routing=refuse_reuse").
    # Disclose the non-relationship rather than let it look like a typo.
    if capability.evidence_count == 0:
        capability_note += " -- routing is evidence-based guidance, unrelated to this verdict"

    return ProcedureVerdict(
        verdict="ALLOW" if result.applicable else "WOULD_REFUSE",
        procedure=procedure.get("name") or str(procedure_id),
        reason=reason,
        evidence=evidence,
        capability_note=capability_note,
    )
