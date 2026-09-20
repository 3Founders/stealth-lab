"""
Global search + "best known way to do X" recommendation (directive §37-38).

Pure composition over the existing retrieval/applicability/capability
stack -- this module invents no ranking formula, no cascade, and no new
storage. Every real decision (what disqualifies a procedure, how RRF
fuses semantic+lexical signals, how capability is estimated) already
lives in `retrieval.py` / `applicability.py` / `procedure_extraction/
capability.py`; this file only calls them and shapes the result.

NAMING COLLISION, DELIBERATE AND DISCLOSED: `mcp_server/server.py` also
has a function named `find_best_way`, in `plan_only` mode. That one
COMPILES AND PERSISTS a real `ExecutionPlan`/`TaskGraph` (see
`app/execution/plans.py`, `plan_persistence.py`) for an MCP-embedded host
agent to execute -- it writes rows. `find_best_way` in THIS module does
none of that: it is a pure READ recommendation ("what procedure would
best accomplish this goal, and why"), calls no compiler, and persists
nothing. Same name, two different objects, on purpose -- do not merge
them, and do not assume one wraps the other.

WHAT "OBJECT TYPES" MEANS HERE: the directive's object-type vocabulary
(§37) is wider than what exists as a real, addressable entity in the
schema today. `procedure`, `task` (`task_nodes` rows), and `claim`
(`knowledge_nodes` rows with `node_type='claim'`) are real, distinct,
independently queryable things. `solution` and `problem` are NOT --
per `.scratch/backend_architecture_audit.md` §3, "Solutions" has no
disconnected table and is modeled as a read composition over
procedures+evidence (which IS what searching object_type='procedure'
already gives you), and "Problems" (an active-problems marketplace
entity) does not exist in the schema at all yet -- it needs a real,
separate migration decision, deliberately out of scope for a read-only
composition module. Asking this module to search either raises
`ValueError` rather than silently returning nothing or pretending to
search a table that isn't there.

CLAIM/TASK RETRIEVAL MECHANISM, AND WHY: both legs reuse
`retrieval.py::HybridRetriever`'s real vector+lexical+RRF pipeline --
`_vector_search`, `_lexical_search`, and the module-level `fuse_rrf` it
already exports -- but call those THREE PRIMITIVES DIRECTLY rather than
`HybridRetriever.retrieve()`. Two real, disclosed reasons, both found by
running this module against a live corpus while writing it (not
theoretical):

1. `retrieve()`'s own graph-expansion stage is NOT actually gated by
   `expand_depth=0`. `GraphStore.traverse_from`'s recursive CTE always
   seeds the frontier with the entrypoints themselves at depth 0, and its
   OUTER select returns every live edge touching ANY frontier row
   regardless of the recursion bound -- so even `expand_depth=0` returns
   the entrypoints' own direct edges, not zero edges. A search endpoint
   wants ONLY direct hits, never graph neighbours (that is what
   `unified_retrieval.py`'s own context-assembly use case is for, a
   different job) -- there is no `expand_depth` value that gets that from
   `retrieve()` as written.
2. `retrieve()`'s own expansion-hydrate step has a live, real, pre-
   existing gap: it assumes an expanded neighbour's table is only ever
   `task_nodes` or (implicitly) `knowledge_nodes` (`ntable == 'task_nodes'
   else knowledge_nodes`-shaped branching for both the TMS truth_state
   filter and the description column) -- but `edges.source_table`/
   `target_table` has allowed `'procedures'` since migration 21, and this
   repo's own real corpus has procedure-linked edges reachable from
   ordinary task_node/knowledge_node entrypoints. Reaching one during
   expansion raises `UndefinedColumnError` (querying a `properties`
   column `procedures` doesn't have) -- confirmed by running this module
   against the live dev database, not a hypothetical. This is a real,
   pre-existing gap in `retrieval.py` itself, out of scope to fix here
   (this module must not edit retrieval.py) -- it is avoided, not
   papered over, by never calling the code path that hits it.

Neither issue touches `_vector_search`/`_lexical_search`/`fuse_rrf`
themselves -- those three are reused completely unmodified, still the
SAME real embedding+lexical+RRF signal `retrieve()` itself is built from.
Only the wrapper method (hydrate-entrypoints-then-expand) is bypassed.
This module does its own minimal hydration afterward (`_fetch_claim_
fields` / `_fetch_task_fields`), which for the claim leg ALSO doubles as
the `node_type='claim'` post-filter `knowledge_nodes`' seven virtual
types otherwise require (HybridRetriever has no `node_type` filter to
plug into at the table level either way -- there is no existing
"claims-only" retriever to reuse verbatim). Because that filter runs
AFTER the fused top_k cut, both legs ask for a wider `top_k` than the
caller's requested `limit` (`_CLAIM_OVERFETCH`) to compensate; an honest
under-fill (fewer than `limit` hits) is still possible and never padded.

RANKING, KEPT DELIBERATELY SEPARATE PER CLAUDE.MD'S HARD RULE: "Retrieval
fuses by RRF; applicability is a non-compensatory cascade... keep them
apart." This module does NOT merge procedure/claim/task results into one
cross-type ranked list -- there is no existing formula that compares an
applicability-gated procedure's fused similarity+capability score against
a claim's or task's plain RRF score, and inventing one here would be
exactly the violation that rule warns against. `search_global` returns
results GROUPED by object_type, each list ordered by that type's own
real, existing ranking mechanism:
  - procedure: `applicability.find_applicable_procedures`'s own cascade
    (hard disqualification) + its own similarity/capability RRF fusion
    of survivors -- reused verbatim, never re-ranked here.
  - claim / task: `retrieval.py`'s own RRF fusion of vector+lexical
    hits -- reused verbatim via HybridRetriever.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Optional
from uuid import UUID

import asyncpg

from app.services.access import AccessScope, TenantScope, scope_predicates
from app.services.applicability import (
    ProcedureNotFound,
    check_procedure_reuse,
    find_applicable_procedures,
)
from app.services.embeddings import Embedder
from app.services.relevance_gate import (
    RELEVANCE_GATE_MIN_SIMILARITY,
    RELEVANCE_GATE_VERSION,
    passes_relevance_gate,
    relevance_label,
    relevance_reason,
)
from app.services.retrieval import NOT_TRUTH_STATE_OUT, HybridRetriever, fuse_rrf
from app.services.retrieval_document import (
    build_applicability_summary,
    build_failure_modes,
)

log = logging.getLogger("stealthlab.retrieval")


def _q_tag(query: str) -> str:
    """A stable, non-reversible query tag for logs -- length + 10 hex of
    sha256. Never logs the query text itself or any procedure content."""
    return f"len={len(query)} h={hashlib.sha256(query.encode('utf-8')).hexdigest()[:10]}"


VALID_OBJECT_TYPES: tuple[str, ...] = ("procedure", "task", "claim")

# object types the directive names (§37) that are NOT real, addressable
# entities yet -- see module docstring. Named explicitly so the error
# message is precise about WHY, not just "invalid value".
_KNOWN_UNSUPPORTED_OBJECT_TYPES = {
    "solution": (
        "'solution' is not a distinct searchable entity -- it is a read "
        "composition over procedures + evidence (search object_types="
        "['procedure'] instead; see .scratch/backend_architecture_audit.md §3)"
    ),
    "problem": (
        "'problem' does not exist in the schema yet -- the active-problems "
        "marketplace entity needs its own migration, deliberately deferred "
        "out of this read-only composition module (see "
        ".scratch/backend_architecture_audit.md §3)"
    ),
}

# HybridRetriever's own top_k is cut BEFORE this module's node_type='claim'
# post-filter runs, so a caller asking for `limit` claims needs a wider
# candidate pull to have a fair chance of `limit` real claims surviving
# the filter. A fixed multiplier, not a computed one -- there is no
# principled way to know the claim/other-virtual-type ratio in advance.
_CLAIM_OVERFETCH = 4

DEFAULT_PER_TYPE_LIMIT = 20


def _reject_unsupported_object_types(object_types: list[str]) -> None:
    for ot in object_types:
        if ot in _KNOWN_UNSUPPORTED_OBJECT_TYPES:
            raise ValueError(
                f"search_global: object_type {ot!r} is not searchable -- "
                f"{_KNOWN_UNSUPPORTED_OBJECT_TYPES[ot]}"
            )
        if ot not in VALID_OBJECT_TYPES:
            raise ValueError(
                f"search_global: unknown object_type {ot!r} -- valid values "
                f"are {VALID_OBJECT_TYPES}"
            )


def _resolve_object_types(object_types: Optional[list[str]]) -> list[str]:
    if object_types is None:
        return list(VALID_OBJECT_TYPES)
    _reject_unsupported_object_types(object_types)
    # De-dup while preserving order -- a caller passing duplicates
    # shouldn't get duplicated result buckets.
    seen: list[str] = []
    for ot in object_types:
        if ot not in seen:
            seen.append(ot)
    return seen


def _scope_filter_matches(
    row_scope_type: Optional[str],
    row_scope_entity_id: Optional[str],
    *,
    scope_type: Optional[str],
    repository_id: Optional[str],
    project_id: Optional[str],
) -> bool:
    """
    V0's scope_type/scope_entity_id shard-key columns (db/21) -- NOT
    procedures.scope, which is a different, JSONB applicability-narrowing
    concept applicability.py's own cascade already owns (see that
    module's docstring: "distinct from procedures.scope"). This is a
    search-result FILTER over the shard key, applied identically across
    all three object types.

    `repository_id`/`project_id` are convenience shortcuts for the two
    scope_type values a caller overwhelmingly wants to filter by; an
    explicit `scope_type` narrows further. Every condition supplied must
    hold (AND) -- passing both repository_id and project_id together is
    the caller's own contradiction (scope_type can't be both 'repository'
    and 'project' on one row), not this function's job to reject.
    """
    if scope_type is not None and row_scope_type != scope_type:
        return False
    if repository_id is not None and not (
        row_scope_type == "repository" and row_scope_entity_id == repository_id
    ):
        return False
    if project_id is not None and not (
        row_scope_type == "project" and row_scope_entity_id == project_id
    ):
        return False
    return True


async def _search_procedures(
    pool: asyncpg.Pool,
    query_vec: Optional[list[float]],
    *,
    query_text: Optional[str] = None,
    embedding_model_id: Optional[str],
    scope: AccessScope,
    current_scope: Optional[dict],
    require_verified: bool,
    invariant_bindings: Optional[dict],
    limit: int,
    scope_type: Optional[str],
    repository_id: Optional[str],
    project_id: Optional[str],
    embedder: Optional[Embedder] = None,
    local_claims: Optional[list] = None,
    meta_out: Optional[dict] = None,
) -> list[dict]:
    """The procedure leg of global search -- CONVERGED onto the canonical retrieval service
    (app.services.retrieval_service): Goal resolution first, then goal-constrained Procedure
    retrieval, hard constraints, JEV/NLI judgment. This function only shapes the service's
    result into the row format REST/frontends already consume. It has no ranking logic of its
    own (the previous find_applicable_procedures cascade + relevance gate composition was removed).

    `meta_out`, when given, receives the service's retrieval metadata (mode, degraded, reasons,
    goal resolution) so callers can surface it.
    """
    from app.services import retrieval_service as rs

    res = await rs.search_procedures(
        pool, query_text or "", scope=scope, local_claims=local_claims or [], embedder=embedder,
        current_scope=current_scope or {}, require_verified=require_verified, invariant_bindings=invariant_bindings)
    if meta_out is not None:
        meta_out.update(res.meta.as_dict())
        meta_out["goal_resolution"] = {"status": res.goals.resolution, "goals": [h.brief() for h in res.goals.resolved]}
    out: list[dict] = []
    for item in res.procedures.ranked:
        proc = item["_row"]
        if not _scope_filter_matches(
            proc.get("scope_type"), proc.get("scope_entity_id"),
            scope_type=scope_type, repository_id=repository_id, project_id=project_id,
        ):
            continue
        stats = proc.get("verification_stats") or {}
        rel = item.get("relation")
        out.append({
            "id": str(proc["id"]),
            "procedure_id": str(proc["procedure_id"]),
            "name": proc["name"],
            "goal": proc["goal"],
            # --- human-facing ---
            "display_name": proc.get("display_name") or proc["name"],
            "display_description": proc.get("display_description") or proc["goal"],
            "applicability_summary": build_applicability_summary(proc),
            # label from the MODEL's verdict (no similarity cutoff exists any more); None when unjudged (degraded)
            "relevance_label": {"applies": "strong", "partial": "relevant"}.get(rel) if item.get("judged") else None,
            "relevance_reason": relevance_reason(query_text, proc) if query_text else None,
            "verification_state": proc["verification_state"],
            "evidence_summary": {
                "successes": stats.get("successes", 0),
                "attempts": stats.get("attempts", 0),
                "distinct_contexts": stats.get("distinct_contexts", 0),
            },
            "failure_modes": build_failure_modes(proc),
            "provenance": proc.get("provenance"),
            "scope": proc.get("scope") or {},
            # --- internal / debug ---
            "staleness": proc["staleness"],
            "availability": proc["availability"],
            "approval_status": proc.get("approval_status"),
            "scope_type": proc.get("scope_type"),
            "scope_entity_id": proc.get("scope_entity_id"),
            "similarity_score": item.get("rrf"),
            "version": proc.get("version"),
            "goal_id": item.get("goal_id"),
            "semantic": {"judged": item.get("judged"), "relation": rel, "confidence": item.get("confidence")},
        })
        if len(out) >= limit:
            break
    log.info("procedure_search %s goal_status=%s mode=%s degraded=%s returned=%d zero_result=%s",
             _q_tag(query_text or ""), res.goals.resolution, res.meta.mode, res.meta.degraded, len(out), len(out) == 0)
    return out


async def _rrf_search_leg(
    retriever: HybridRetriever,
    query: str,
    query_vec: Optional[list[float]],
    top_k: int,
) -> list[tuple[UUID, str, float, list[str]]]:
    """
    The real embedding+lexical+RRF signal, reused directly rather than
    through `HybridRetriever.retrieve()` -- see the module docstring's
    "CLAIM/TASK RETRIEVAL MECHANISM" section for the two concrete,
    confirmed reasons `retrieve()`'s own wrapper is not used here
    (its expansion stage is not actually gated by expand_depth, and its
    expansion-hydrate step has a live gap for edges touching a
    `procedures` endpoint). `_vector_search`, `_lexical_search`, and
    `fuse_rrf` are HybridRetriever's/retrieval.py's own real primitives,
    called verbatim -- this function differs from `retrieve()`'s own body
    only by stopping BEFORE the hydrate-then-expand stage.

    Degrades to lexical-only on a missing/failed embedding, same posture
    `retrieve()` itself documents (a query_vec of None skips the vector
    leg rather than raising).
    """
    vector_hits: list[tuple[UUID, str, int]] = []
    if query_vec is not None:
        try:
            vector_hits = await retriever._vector_search(query_vec, top_k * 2)  # noqa: SLF001
        except Exception:  # noqa: BLE001 -- degrade to lexical-only, same as retrieve()
            vector_hits = []
    lexical_hits = await retriever._lexical_search(query, top_k * 2)  # noqa: SLF001

    scores, matched = fuse_rrf([(vector_hits, "semantic"), (lexical_hits, "keyword")])
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    return [(node_id, table, score, matched[(node_id, table)]) for (node_id, table), score in ranked]


async def _fetch_claim_fields(
    pool: asyncpg.Pool, ids: list[UUID], *, scope: AccessScope, tenant_scope: TenantScope,
) -> dict[str, dict]:
    """Batched hydrate for claim rows, doubling as the node_type='claim'
    post-filter -- a row absent from the returned dict was either not a
    live claim or not visible to this scope."""
    if not ids:
        return {}
    scope_sql, scope_params, _ = scope_predicates(scope, tenant_scope, param_index=2)
    rows = await pool.fetch(
        f"""
        SELECT id, name, subject, predicate, object, properties, scope_type, scope_entity_id
        FROM knowledge_nodes
        WHERE id = ANY($1::uuid[]) AND node_type = 'claim'
          AND t_invalid IS NULL AND {NOT_TRUTH_STATE_OUT}
          AND {scope_sql}
        """,
        ids, *scope_params,
    )
    return {str(r["id"]): dict(r) for r in rows}


async def _search_claims(
    pool: asyncpg.Pool,
    query: str,
    query_vec: Optional[list[float]],
    *,
    scope: AccessScope,
    tenant_scope: TenantScope,
    embedder: Embedder,
    limit: int,
    scope_type: Optional[str],
    repository_id: Optional[str],
    project_id: Optional[str],
) -> list[dict]:
    """The claim leg -- see module docstring's "CLAIM/TASK RETRIEVAL
    MECHANISM" section."""
    retriever = HybridRetriever(
        pool, embedder=embedder, scope=scope, tenant_scope=tenant_scope,
        tables=("knowledge_nodes",),
    )
    hits = await _rrf_search_leg(retriever, query, query_vec, top_k=limit * _CLAIM_OVERFETCH)
    ids = [h[0] for h in hits]
    fields_by_id = await _fetch_claim_fields(pool, ids, scope=scope, tenant_scope=tenant_scope)

    out: list[dict] = []
    for node_id, _table, score, matched_by in hits:
        fields = fields_by_id.get(str(node_id))
        if fields is None:
            continue  # not a live, visible claim -- filtered out, not padded
        if not _scope_filter_matches(
            fields.get("scope_type"), fields.get("scope_entity_id"),
            scope_type=scope_type, repository_id=repository_id, project_id=project_id,
        ):
            continue
        props = dict(fields.get("properties") or {})
        out.append({
            "id": str(node_id),
            "name": fields.get("name"),
            "subject": fields.get("subject"),
            "predicate": fields.get("predicate"),
            "object": fields.get("object"),
            "truth_state": props.get("truth_state"),
            "scope_type": fields.get("scope_type"),
            "scope_entity_id": fields.get("scope_entity_id"),
            "score": score,
            "matched_by": matched_by,
        })
        if len(out) >= limit:
            break
    return out


async def _fetch_task_fields(
    pool: asyncpg.Pool, ids: list[UUID], *, scope: AccessScope, tenant_scope: TenantScope,
) -> dict[str, dict]:
    if not ids:
        return {}
    scope_sql, scope_params, _ = scope_predicates(scope, tenant_scope, param_index=2)
    rows = await pool.fetch(
        f"""
        SELECT id, name, description, scope_type, scope_entity_id
        FROM task_nodes
        WHERE id = ANY($1::uuid[]) AND t_invalid IS NULL AND {scope_sql}
        """,
        ids, *scope_params,
    )
    return {str(r["id"]): dict(r) for r in rows}


async def _search_tasks(
    pool: asyncpg.Pool,
    query: str,
    query_vec: Optional[list[float]],
    *,
    scope: AccessScope,
    tenant_scope: TenantScope,
    embedder: Embedder,
    limit: int,
    scope_type: Optional[str],
    repository_id: Optional[str],
    project_id: Optional[str],
) -> list[dict]:
    """The task leg -- see module docstring's "CLAIM/TASK RETRIEVAL
    MECHANISM" section. task_nodes carries no node_type split the way
    knowledge_nodes does, so the follow-up hydrate here is a pure
    visibility check + scope_type/entity filter, not also a type filter."""
    has_scope_filter = scope_type is not None or repository_id is not None or project_id is not None
    top_k = limit * _CLAIM_OVERFETCH if has_scope_filter else limit
    retriever = HybridRetriever(
        pool, embedder=embedder, scope=scope, tenant_scope=tenant_scope,
        tables=("task_nodes",),
    )
    hits = await _rrf_search_leg(retriever, query, query_vec, top_k=top_k)
    ids = [h[0] for h in hits]
    fields_by_id = await _fetch_task_fields(pool, ids, scope=scope, tenant_scope=tenant_scope)

    out: list[dict] = []
    for node_id, _table, score, matched_by in hits:
        fields = fields_by_id.get(str(node_id))
        if fields is None:
            continue  # not live/visible -- filtered out, not padded
        if not _scope_filter_matches(
            fields.get("scope_type"), fields.get("scope_entity_id"),
            scope_type=scope_type, repository_id=repository_id, project_id=project_id,
        ):
            continue
        out.append({
            "id": str(node_id),
            "name": fields.get("name"),
            "description": fields.get("description"),
            "scope_type": fields.get("scope_type"),
            "scope_entity_id": fields.get("scope_entity_id"),
            "score": score,
            "matched_by": matched_by,
        })
        if len(out) >= limit:
            break
    return out


async def search_global(
    pool: asyncpg.Pool,
    query: str,
    *,
    object_types: Optional[list[str]] = None,
    scope_type: Optional[str] = None,
    repository_id: Optional[str] = None,
    project_id: Optional[str] = None,
    filters: Optional[dict[str, Any]] = None,
    limit: int = DEFAULT_PER_TYPE_LIMIT,
    scope: AccessScope,
    tenant_scope: Optional[TenantScope] = None,
    embedder: Optional[Embedder] = None,
) -> dict[str, Any]:
    """
    Read-only global search across procedure / task / claim object types.
    See the module docstring for the full design rationale (object-type
    scope, claim-retrieval mechanism, why results are grouped rather than
    cross-ranked). Every returned row is scope_predicates()-filtered --
    either directly (claim/task's own SQL) or via find_applicable_
    procedures' access_scope + this module's own post-filter (procedure).

    `filters` is a small, documented escape hatch, not a general query
    language -- recognized keys (all optional, unrecognized keys are
    ignored rather than rejected, since this is meant to stay a thin
    passthrough, not grow its own schema):
      - "current_scope": dict -- procedures.scope/exclusions narrowing
        context for find_applicable_procedures' OWN cascade (a DIFFERENT
        concept from scope_type/repository_id/project_id above -- see
        `_scope_filter_matches`'s docstring).
      - "require_verified": bool, default False (search is a browse, not
        an auto-invocation decision -- see `_search_procedures`).
      - "invariant_bindings": dict, forwarded to the procedure cascade.

    `tenant_scope` defaults to TenantScope.unrestricted(), matching this
    repo's existing REST precedent (`app/api/graph.py`) for the same
    reason: no request dependency resolves a real tenant yet.
    """
    resolved_types = _resolve_object_types(object_types)
    tenant = tenant_scope or TenantScope.unrestricted()
    filters = filters or {}
    embedder = embedder or Embedder()

    query_vec: Optional[list[float]] = None
    try:
        query_vec = await embedder.embed_one(query, input_type="query")
    except Exception:  # noqa: BLE001 -- degrade to lexical-only, same posture as HybridRetriever itself
        query_vec = None

    results: dict[str, list[dict]] = {}

    retrieval_meta: dict = {}
    if "procedure" in resolved_types:
        results["procedure"] = await _search_procedures(
            pool, query_vec,
            embedder=embedder, local_claims=filters.get("local_claims"), meta_out=retrieval_meta,
            query_text=query,
            embedding_model_id=embedder.embedding_model_id() if query_vec is not None else None,
            scope=scope,
            current_scope=filters.get("current_scope"),
            require_verified=bool(filters.get("require_verified", False)),
            invariant_bindings=filters.get("invariant_bindings"),
            limit=limit,
            scope_type=scope_type, repository_id=repository_id, project_id=project_id,
        )

    if "claim" in resolved_types:
        results["claim"] = await _search_claims(
            pool, query, query_vec,
            scope=scope, tenant_scope=tenant, embedder=embedder, limit=limit,
            scope_type=scope_type, repository_id=repository_id, project_id=project_id,
        )

    if "task" in resolved_types:
        results["task"] = await _search_tasks(
            pool, query, query_vec,
            scope=scope, tenant_scope=tenant, embedder=embedder, limit=limit,
            scope_type=scope_type, repository_id=repository_id, project_id=project_id,
        )

    return {
        "query": query,
        "object_types": resolved_types,
        "results": results,
        "counts": {k: len(v) for k, v in results.items()},
        "retrieval": retrieval_meta or None,
    }


def _verdict_to_dict(verdict) -> dict[str, Any]:
    return {
        "verdict": verdict.verdict,
        "procedure": verdict.procedure,
        "reason": verdict.reason,
        "evidence": verdict.evidence,
        "capability_note": verdict.capability_note,
    }


async def find_best_way(
    pool: asyncpg.Pool,
    goal: str,
    *,
    context: Optional[dict] = None,
    scope_constraint: Optional[dict] = None,
    constraints: Optional[dict] = None,
    scope: AccessScope,
    embedder: Optional[Embedder] = None,
) -> dict[str, Any]:
    """
    Pure READ "best known way to do X" recommendation. See the module
    docstring's naming-collision note -- this is NOT
    `mcp_server/server.py`'s `find_best_way(mode="plan_only")`, which
    compiles and persists a real plan. This function compiles nothing,
    persists nothing; it is `applicability.find_applicable_procedures`
    plus `applicability.check_procedure_reuse` (both reused verbatim,
    never reimplemented) shaped into a recommendation.

    `goal`: free-text goal description, embedded once (query input_type)
    and passed as `find_applicable_procedures`' `goal_embedding` -- the
    SAME similarity+capability RRF fusion `find_applicable_procedures`
    already does for its own candidates, not a second ranking pass.

    `context`: forwarded to `find_applicable_procedures`' own
    `current_scope` -- the procedure's OWN scope/exclusions/precondition
    narrowing (e.g. {"repo": ["backend"]}), exactly like
    `check_hard_constraints`' own `current_scope` parameter.

    `scope_constraint`: optional {"scope_type": ..., "repository_id":
    ..., "project_id": ...} -- the V0 shard-key filter, same meaning and
    same Python post-filter caveat as `search_global`'s own scope_type/
    repository_id/project_id (see `_scope_filter_matches`).

    `constraints`: {"invariant_bindings": dict, "allow_unverified": bool
    (default False -- a RECOMMENDATION defaults to requiring real,
    approved verification evidence, unlike `search_global`'s browse-mode
    default; explicit opt-in mirrors ticket 13's own named cold-start
    escape hatch), "limit": int (default 3, how many alternates besides
    the top pick)}.

    HONEST EMPTY RESULT: when find_applicable_procedures returns no
    survivors (cold-start gate closed, or every candidate disqualified,
    or the scope_constraint post-filter removes everyone), this returns
    `recommendation: None` with a real reason -- NEVER a fabricated
    "best" pick. `confidence` is "none" in that case, never a made-up
    number.

    For each surfaced candidate, `check_procedure_reuse` (the SAME
    ALLOW/WOULD_REFUSE decision `demo.md` C5's refusal-with-receipts
    tool uses) is called to surface the real narrative + evidence
    citations + capability note in the response, rather than a bare id --
    directly satisfying "claim consistency, capability, evidence
    surfaced in the response". Composed, not reimplemented: this module
    writes no cascade logic and no capability arithmetic of its own.
    """
    # CONVERGED (docs/retrieval_architecture.md): the recommendation is produced
    # by the ONE canonical retrieval service -- Goal resolution -> goal-constrained
    # Procedure retrieval -> hard constraints -> JEV/NLI -> evidence -> selection.
    # This function only adapts request/response shapes for the REST/MCP callers.
    from app.services import retrieval_service as rs

    constraints = constraints or {}
    scope_constraint = scope_constraint or {}
    embedder = embedder or Embedder()
    allow_unverified = bool(constraints.get("allow_unverified", False))
    alt_limit = int(constraints.get("limit", 3))
    cfg = rs.RetrievalConfig(procedure_alternatives=max(alt_limit, 1))

    result = await rs.find_best_way(
        pool, goal, local_claims=constraints.get("local_claims") or [], scope=scope, embedder=embedder,
        cfg=cfg, current_scope=context or {}, require_verified=not allow_unverified)

    if scope_constraint and result["procedures"]:
        keep = []
        for item in result["procedures"]:
            from app.services.shards import home_pool
            row = await (await home_pool(pool, "procedure", str(item["id"]), by_row_id=True)).fetchrow(
                "SELECT scope_type, scope_entity_id FROM procedures WHERE id = $1::uuid", item["id"])
            if row is not None and _scope_filter_matches(
                row["scope_type"], row["scope_entity_id"], scope_type=scope_constraint.get("scope_type"),
                repository_id=scope_constraint.get("repository_id"), project_id=scope_constraint.get("project_id")):
                keep.append(item["procedure_id"])
        result["procedures"] = [i for i in result["procedures"] if i["procedure_id"] in keep]
        result["alternatives"] = [i for i in result["alternatives"] if i["procedure_id"] in keep]
        if result["recommendation"] and result["recommendation"]["procedure_id"] not in keep:
            result["recommendation"] = None
            result["confidence"] = "none"
            result["reason"] = "the selected procedure is outside the requested scope_constraint"

    # refusal-with-receipts verdict (existing ALLOW / WOULD_REFUSE decision) for what we surface
    for item in ([result["recommendation"]] if result["recommendation"] else []) + result["alternatives"]:
        try:
            verdict = await check_procedure_reuse(
                pool, procedure_id=str(item["procedure_id"]), current_scope=context or {}, access_scope=scope)
        except ProcedureNotFound:
            continue
        item.update(_verdict_to_dict(verdict))

    if result["recommendation"] is None and not result["reason"]:
        result["reason"] = "no applicable procedure: honest empty result, not a fabricated recommendation"
    rec = result["recommendation"]
    if rec is None:
        result["confidence"] = "none"
    else:
        result["confidence"] = "high" if rec.get("verification_state") == "verified" and rec.get("verdict") == "ALLOW" else "low"
    return result
