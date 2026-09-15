"""
Canonical Goal object (backend/db/83_goals.sql, backend/db/84_goals_
embeddings.sql) -- founder directive "ingestion.md" Sec 2: Goal is the
stable-ID linking primitive between Procedure and Implementation,
deliberately lighter-weight than either (no steps, no invariants, no
verification statistics -- those stay on `procedures`/`implementations`).

This module owns the ONE write path (find_or_create_goal) so every
caller -- app/services/procedures.py::capture_procedure and
app/services/implementation_goals.py's enrichment job today, any future
ingestion source tomorrow -- gets the same dedup discipline (ingestion.md
Sec 8):
  tier 1 -- exact normalized-name match (always on, DB-enforced twice:
    the SELECT below, and migration 83's own partial unique indexes as a
    concurrent-write backstop).
  tier 2 -- alias match (always on): a candidate whose normalized text
    matches an EXISTING goal's stored `aliases` entry is the same goal.
  tier 3/4 -- embedding cosine-similarity match: OPT-IN via the
    `embedder` parameter (default None -- no behavior or cost change for
    a caller that doesn't pass one). Deliberately conservative
    (AUTO_DEDUP_MAX_COSINE_DISTANCE): ingestion.md Sec 8 explicitly warns
    "do NOT automatically merge semantically distinct Goals merely
    because text similarity is high" -- this only auto-merges near-
    paraphrases (distance <= 0.05, i.e. cosine similarity >= 0.95), never
    a loose topical match.
  tier 5 -- LLM adjudication for the ambiguous middle band (embedding
    similarity present but below the auto-merge threshold): NOT
    implemented. A candidate that misses tiers 1-4 always becomes a new,
    distinct row today, even if it might be a true near-duplicate a human
    or LLM would recognize. Real, intentional gap.
  Explicit merge/review workflow (ingestion.md Sec 8's last paragraph):
    `goals.status='merged'` + `merged_into_id` exist as schema support,
    but nothing here flags "these two rows look related, review them" or
    performs a merge -- not implemented.

Also provides the read/product surface this Goal object exists FOR
(ingestion.md Sec 18-19): search_goals (lexical + optional semantic,
RRF-fused), get_goal (with its live Procedures/Implementations), and
create_goal_from_user (the "search near matches, allow create anyway"
flow a frontend/MCP caller drives).
"""
from __future__ import annotations

import re
from typing import Any, Optional

import asyncpg

from app.services.access import AccessScope, TenantScope, scope_predicates
from app.services.embeddings import to_pgvector
from app.services.v0_gate import validate_provenance, validate_scope
from app.utils.ids import uuid7

_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]")
_WHITESPACE_RE = re.compile(r"\s+")

# Tier 3/4 auto-merge threshold (pgvector cosine DISTANCE -- smaller is
# more similar; 0.05 ~= cosine similarity >= 0.95). Deliberately strict:
# this is meant to catch near-exact paraphrases ("find callers" vs "find
# all callers of a function"), never merely-related goals. Configuration,
# not tuned against real data yet -- a real, stated limitation.
AUTO_DEDUP_MAX_COSINE_DISTANCE = 0.05

# RRF's smoothing constant -- same value retrieval.py's own RRF_K uses
# (the original RRF paper's value), kept identical rather than a second
# unexplained constant.
_RRF_K = 60


def normalize_goal_name(text: str) -> str:
    """Exact-match dedup key (ingestion.md Sec 8 tier 1): lowercase,
    replace non-alphanumerics with spaces, collapse whitespace, trim.

    MUST stay in exact lock-step with its SQL twin,
    `normalize_goal_name()` in backend/db/83_goals.sql -- both are
    relied on to agree bit-for-bit (this function for new writes via
    find_or_create_goal, the SQL function for migration 83's own
    backfill and any future direct-SQL query). See
    tests/test_goals_offline.py for the parity check against a real DB.

    Deliberately NOT stemming or semantic normalization -- "find
    references" and "find callers" stay distinct rows under this alone
    (tier 3/4 embedding similarity, when opted into, is what unifies true
    paraphrases; see module docstring).
    """
    lowered = text.strip().lower()
    stripped = _NON_ALNUM_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", stripped).strip()


def goal_embedding_text(canonical_name: str, description: Optional[str] = None) -> str:
    """Canonical text a Goal's embedding is built from -- name plus
    description when present, never steps/procedures/implementations
    (those belong to THEIR OWN retrieval documents, not Goal's -- Goal
    stays the lightweight object ingestion.md Sec 2 requires)."""
    if description:
        return f"{canonical_name}\n{description}"
    return canonical_name


async def find_or_create_goal(
    pool: asyncpg.Pool,
    *,
    canonical_name: str,
    scope_type: str,
    scope_entity_id: Optional[str] = None,
    provenance: str,
    description: Optional[str] = None,
    expected_outcome: Optional[dict] = None,
    verification_requirement: Optional[dict] = None,
    status: str = "candidate",
    owner_id: Optional[str] = None,
    visibility: str = "public",
    created_from: Optional[str] = None,
    aliases: Optional[list[str]] = None,
    created_by: Optional[str] = None,
    embedder: Optional[Any] = None,
) -> dict[str, Any]:
    """Tier 1 (exact name) + tier 2 (alias) dedup, then tier 3/4
    (embedding similarity, only if `embedder` is given) before inserting.
    Returns {"id": str, "canonical_name": str, "created": bool}.

    `scope_type`/`provenance` are REQUIRED and V0-gated (Band 1.3
    discipline, same as `capture_procedure` -- "nothing enters without
    scope + provenance"), not defaulted to a silent 'global' -- callers
    must make an explicit choice, same as every other write path in this
    codebase.

    `embedder`: an app.services.embeddings.Embedder (or anything with an
    async `embed_one_with_metadata(text, input_type=...)` matching its
    signature). Passing one makes this function ALSO compute and store
    the new/matched goal's embedding, and adds the tier-3/4 semantic
    dedup pass -- a real latency/cost addition (one embedding API call),
    so it is opt-in, never silently applied to an existing caller.
    """
    resolved_scope_type, resolved_scope_entity_id = validate_scope(
        scope_type, scope_entity_id,
        allow_global_entity_id=bool(scope_type == "global" and scope_entity_id),
    )
    validate_provenance(provenance)

    normalized = normalize_goal_name(canonical_name)
    if not normalized:
        raise ValueError("canonical_name must contain at least one alphanumeric character")

    if resolved_scope_type == "global":
        existing = await pool.fetchrow(
            "SELECT id, canonical_name FROM goals "
            "WHERE t_invalid IS NULL AND status <> 'merged' "
            "AND (scope_type IS NULL OR scope_type = 'global') "
            "AND (normalized_name = $1 "
            "     OR EXISTS (SELECT 1 FROM unnest(aliases) a WHERE lower(trim(a)) = lower(trim($2))))",
            normalized, canonical_name,
        )
    else:
        existing = await pool.fetchrow(
            "SELECT id, canonical_name FROM goals "
            "WHERE t_invalid IS NULL AND status <> 'merged' "
            "AND scope_type = $2 AND scope_entity_id = $3 "
            "AND (normalized_name = $1 "
            "     OR EXISTS (SELECT 1 FROM unnest(aliases) a WHERE lower(trim(a)) = lower(trim($4))))",
            normalized, resolved_scope_type, resolved_scope_entity_id, canonical_name,
        )
    if existing:
        return {
            "id": str(existing["id"]),
            "canonical_name": existing["canonical_name"],
            "created": False,
        }

    embedding_vec: Optional[list[float]] = None
    embedding_meta = None
    if embedder is not None:
        embedding_vec, embedding_meta = await embedder.embed_one_with_metadata(
            goal_embedding_text(canonical_name, description), input_type="document",
        )
        # Tier 3/4: a near-identical existing goal, found only by meaning,
        # not text. Conservative on purpose -- see AUTO_DEDUP_MAX_COSINE_DISTANCE.
        if resolved_scope_type == "global":
            semantic_match = await pool.fetchrow(
                "SELECT id, canonical_name, embedding <=> $1::vector AS dist FROM goals "
                "WHERE t_invalid IS NULL AND status <> 'merged' AND embedding IS NOT NULL "
                "AND (scope_type IS NULL OR scope_type = 'global') "
                "ORDER BY dist ASC LIMIT 1",
                to_pgvector(embedding_vec),
            )
        else:
            semantic_match = await pool.fetchrow(
                "SELECT id, canonical_name, embedding <=> $1::vector AS dist FROM goals "
                "WHERE t_invalid IS NULL AND status <> 'merged' AND embedding IS NOT NULL "
                "AND scope_type = $2 AND scope_entity_id = $3 "
                "ORDER BY dist ASC LIMIT 1",
                to_pgvector(embedding_vec), resolved_scope_type, resolved_scope_entity_id,
            )
        if semantic_match and semantic_match["dist"] <= AUTO_DEDUP_MAX_COSINE_DISTANCE:
            return {
                "id": str(semantic_match["id"]),
                "canonical_name": semantic_match["canonical_name"],
                "created": False,
            }

    goal_id = uuid7()
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO goals (
                id, canonical_name, normalized_name, description, expected_outcome,
                verification_requirement, status, provenance, created_from, owner_id,
                visibility, aliases, created_by, scope_type, scope_entity_id,
                embedding, embedding_model_id, embedding_provider, embedding_text_hash
            ) VALUES (
                $1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, $9, $10,
                $11::visibility_level, $12, $13, $14, $15,
                $16::vector, $17, $18, $19
            )
            RETURNING id, canonical_name
            """,
            str(goal_id), canonical_name, normalized, description,
            expected_outcome if expected_outcome is not None else {},
            verification_requirement if verification_requirement is not None else {},
            status, provenance, created_from, owner_id, visibility,
            aliases or [], created_by, resolved_scope_type, resolved_scope_entity_id,
            to_pgvector(embedding_vec) if embedding_vec is not None else None,
            embedding_meta.model_id if embedding_meta is not None else None,
            embedding_meta.provider if embedding_meta is not None else None,
            embedding_meta.text_sha256 if embedding_meta is not None else None,
        )
    except asyncpg.UniqueViolationError:
        # Lost a race against a concurrent insert of the identical
        # (normalized_name, scope) pair -- migration 83's own partial
        # unique index caught it. Re-select the winner rather than
        # raising a spurious error for what is, semantically, a
        # successful dedup.
        return await find_or_create_goal(
            pool, canonical_name=canonical_name, scope_type=scope_type,
            scope_entity_id=scope_entity_id, provenance=provenance,
        )
    return {"id": str(row["id"]), "canonical_name": row["canonical_name"], "created": True}


async def get_goal(
    pool: asyncpg.Pool,
    goal_id: str,
    *,
    scope: Optional[AccessScope] = None,
    tenant_scope: Optional[TenantScope] = None,
) -> Optional[dict[str, Any]]:
    """One Goal plus its live Procedures/Implementations (ingestion.md
    Sec 18: "Return... available Procedures, available Implementations").
    Summaries only (id/name/status) -- never the full procedure/
    implementation row, keeping this a lightweight Goal-centric view, not
    a second copy of `get_procedure`/`inspect_implementation`.

    Scope-checked the same way every other single-row-by-id reader in
    this codebase is (CLAUDE.md: retrieval is not authorization) --
    returns None for a row that exists but is not visible to `scope`,
    identical to a genuinely missing id (no enumeration signal).
    """
    scope = scope or AccessScope.unrestricted()
    tenant_scope = tenant_scope or TenantScope.unrestricted()
    scope_sql, scope_params, next_idx = scope_predicates(scope, tenant_scope, param_index=2)
    row = await pool.fetchrow(
        f"SELECT * FROM goals WHERE id = $1 AND t_invalid IS NULL AND {scope_sql}",
        goal_id, *scope_params,
    )
    if not row:
        return None
    goal = dict(row)
    goal.pop("embedding", None)  # never serialize a raw vector to a caller
    goal["id"] = str(goal["id"])
    # str-cast every id -- asyncpg returns a real uuid.UUID object, which
    # does not compare equal to the str ids find_or_create_goal returns
    # (a real inconsistency this module's own live verification caught:
    # `search_goals` result ids failing a plain `==` against
    # `find_or_create_goal`'s). Kept consistent everywhere in this module.
    goal["procedures"] = [
        {**dict(r), "id": str(r["id"]), "procedure_id": str(r["procedure_id"])}
        for r in await pool.fetch(
            "SELECT id, procedure_id, name, verification_state, availability "
            "FROM procedures WHERE achieves_goal_id = $1 AND t_invalid IS NULL "
            "ORDER BY t_created DESC LIMIT 50",
            goal_id,
        )
    ]
    goal["implementations"] = [
        {**dict(r), "id": str(r["id"])}
        for r in await pool.fetch(
            "SELECT id, name, provider, kind, status FROM implementations "
            "WHERE goal_id = $1 ORDER BY t_created DESC LIMIT 50",
            goal_id,
        )
    ]
    return goal


async def search_goals(
    pool: asyncpg.Pool,
    *,
    query_text: Optional[str] = None,
    query_embedding: Optional[list[float]] = None,
    scope: Optional[AccessScope] = None,
    tenant_scope: Optional[TenantScope] = None,
    status: Optional[str] = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Lexical + optional semantic search over goals, RRF-fused
    (ingestion.md Sec 18). At least one of `query_text`/`query_embedding`
    is required -- a caller with neither has nothing to search for.

    This is the "search near matches" half of ingestion.md Sec 19's
    create-Goal flow, and the general Goal-search surface Sec 18 asks
    for. Deliberately NOT the heavyweight multi-table
    `retrieval.ProceduralRetriever` -- Goal is one table with no
    hierarchy/graph expansion, so a small dedicated fuse is simpler and
    correct, not a shortcut.
    """
    if not query_text and not query_embedding:
        raise ValueError("search_goals requires query_text and/or query_embedding")
    scope = scope or AccessScope.unrestricted()
    tenant_scope = tenant_scope or TenantScope.unrestricted()

    legs: dict[str, list[tuple[str, int]]] = {}  # goal_id -> [(leg_name, rank), ...]

    if query_text:
        # Fixed params: $1=query_text, $2=limit*3. Scope params start at
        # $3; the optional status param lands wherever scope_predicates
        # says the next free index is -- never a hardcoded guess, since
        # scope_predicates may consume a variable number of placeholders
        # depending on `scope`/`tenant_scope`.
        scope_sql, scope_params, next_idx = scope_predicates(scope, tenant_scope, alias="goals", param_index=3)
        status_sql = f"status = ${next_idx}" if status else "TRUE"
        rows = await pool.fetch(
            f"""
            SELECT id, ts_rank(
                to_tsvector('english', canonical_name || ' ' || COALESCE(description, '')),
                plainto_tsquery('english', $1)
            ) AS rank
            FROM goals
            WHERE t_invalid IS NULL AND {scope_sql} AND {status_sql}
              AND to_tsvector('english', canonical_name || ' ' || COALESCE(description, ''))
                  @@ plainto_tsquery('english', $1)
            ORDER BY rank DESC LIMIT $2
            """,
            query_text, limit * 3, *scope_params, *([status] if status else []),
        )
        for i, r in enumerate(rows):
            legs.setdefault(str(r["id"]), []).append(("lexical", i))

    if query_embedding:
        scope_sql, scope_params, next_idx = scope_predicates(scope, tenant_scope, alias="goals", param_index=3)
        status_sql = f"status = ${next_idx}" if status else "TRUE"
        rows = await pool.fetch(
            f"""
            SELECT id FROM goals
            WHERE t_invalid IS NULL AND embedding IS NOT NULL AND {scope_sql} AND {status_sql}
            ORDER BY embedding <=> $1::vector ASC LIMIT $2
            """,
            to_pgvector(query_embedding), limit * 3, *scope_params, *([status] if status else []),
        )
        for i, r in enumerate(rows):
            legs.setdefault(str(r["id"]), []).append(("semantic", i))

    if not legs:
        return []

    fused = sorted(
        legs.items(),
        key=lambda kv: sum(1.0 / (_RRF_K + rank + 1) for _leg, rank in kv[1]),
        reverse=True,
    )
    top_ids = [gid for gid, _ranks in fused[:limit]]
    rows = await pool.fetch(
        "SELECT id, canonical_name, description, status, scope_type, scope_entity_id, "
        "expected_outcome, verification_requirement FROM goals WHERE id = ANY($1::uuid[])",
        top_ids,
    )
    by_id = {str(r["id"]): {**dict(r), "id": str(r["id"])} for r in rows}
    return [by_id[gid] for gid in top_ids if gid in by_id]


async def create_goal_from_user(
    pool: asyncpg.Pool,
    *,
    canonical_name: str,
    scope_type: str,
    scope_entity_id: Optional[str] = None,
    owner_id: str,
    description: Optional[str] = None,
    expected_outcome: Optional[dict] = None,
    verification_requirement: Optional[dict] = None,
    embedder: Optional[Any] = None,
    allow_create_anyway: bool = False,
    near_match_limit: int = 5,
) -> dict[str, Any]:
    """ingestion.md Sec 19's user-facing flow: search near matches first,
    let the caller explicitly override ("create anyway") when nothing
    close enough already exists. Returns one of:

      {"outcome": "matched", "goal": {...}}
        -- find_or_create_goal's own tiers 1-4 found/created the SAME
        goal object it always would (exact name, alias, or -- if
        `embedder` given -- a near-identical embedding). Never surfaced
        as a "near match" to review; it IS the goal.

      {"outcome": "near_matches", "candidates": [...]}
        -- `allow_create_anyway` is False and a lexical/semantic search
        found plausible-but-not-identical existing goals (ingestion.md
        Sec 19: "inspect close matches" before creating). No row written.
        The caller re-calls with `allow_create_anyway=True` to proceed.

      {"outcome": "created", "goal": {...}}
        -- either no near matches existed, or the caller explicitly
        confirmed via `allow_create_anyway`.

    New user-created goals start `status='candidate'` (find_or_create_goal's
    own default) -- ingestion.md Sec 19's "candidate lifecycle", never
    born active/reviewed.
    """
    if not allow_create_anyway:
        query_embedding = None
        if embedder is not None:
            query_embedding, _meta = await embedder.embed_one_with_metadata(
                goal_embedding_text(canonical_name, description), input_type="query",
            )
        scope = (
            AccessScope.for_user(owner_id) if scope_type != "global" else AccessScope.unrestricted()
        )
        candidates = await search_goals(
            pool, query_text=canonical_name, query_embedding=query_embedding,
            scope=scope, limit=near_match_limit,
        )
        # find_or_create_goal's own exact-name/alias tier would already
        # have matched an identical row -- only surface candidates that
        # are NOT that exact match, so a caller isn't shown "here's a
        # near match" for the goal it's about to get anyway.
        normalized = normalize_goal_name(canonical_name)
        distinct_candidates = [
            c for c in candidates if normalize_goal_name(c["canonical_name"]) != normalized
        ]
        if distinct_candidates:
            return {"outcome": "near_matches", "candidates": distinct_candidates}

    result = await find_or_create_goal(
        pool, canonical_name=canonical_name, scope_type=scope_type,
        scope_entity_id=scope_entity_id, provenance="system_pending_review",
        description=description, expected_outcome=expected_outcome,
        verification_requirement=verification_requirement,
        owner_id=owner_id, visibility="public", created_from="user_created",
        created_by=owner_id, embedder=embedder,
    )
    return {"outcome": "matched" if not result["created"] else "created", "goal": result}
