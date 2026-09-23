"""
Canonical Goal object (backend/db/83_goals.sql, backend/db/84_goals_
embeddings.sql) -- founder directive "ingestion.md" Sec 2: Goal is the
stable-ID linking primitive for Procedures, deliberately lighter-weight
(no steps, no invariants, no verification statistics -- those stay on
`procedures`).

This module owns the ONE write path (find_or_create_goal) so every
caller -- app/services/procedures.py::capture_procedure and any
ingestion source -- gets the same dedup discipline (ingestion.md
Sec 8):
  tier 1 -- exact normalized-name match (always on, DB-enforced twice:
    the SELECT below, and migration 83's own partial unique indexes as a
    concurrent-write backstop).
  tier 2 -- alias match (always on): a candidate whose normalized text
    matches an EXISTING goal's stored `aliases` entry is the same goal.
  semantic -- FTS + vector candidates (RRF-fused) judged by the semantic
    chain (JEV -> Gemini -> Gemma): same / narrower / broader / related /
    distinct (app/services/identity_resolution.py). Persisted to
    `identity_decisions`. The former SimHash and cosine-threshold auto-merge
    tiers and the raw-client adjudication were REMOVED: a similarity number
    never decides identity, a model does. Migration 86's `simhash` column is
    now unused (kept; dropping it is a later cleanup migration).
  Explicit merge/review workflow (ingestion.md Sec 8's last paragraph):
    `goals.status='merged'` + `merged_into_id` exist as schema support,
    but nothing here flags "these two rows look related, review them" or
    performs a merge -- not implemented.

find_or_create_goal also gates NEW rows on ingestion.md Sec 20's quality
bar (`describe_goal_quality_issue`/`GoalQualityRejected`) -- a candidate
matching a disclosed low-quality pattern (raw command echo, bare tool
name, hyper-specific file path/repo mention, vague filler-word label)
raises rather than gets written, right before the INSERT only (never
retroactively blocking a dedup hit against an existing row, good or
bad -- see that check's own comment). Heuristic, not full semantic
judgment -- a real, disclosed scope limit, not every bad Goal Sec 20
describes is mechanically detectable.

Also provides the read/product surface this Goal object exists FOR
(ingestion.md Sec 18-19): search_goals (lexical + optional semantic,
RRF-fused), get_goal (with its live Procedures), and
create_goal_from_user (the "search near matches, allow create anyway"
flow a frontend/MCP caller drives).
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

import asyncpg

from app.services.access import AccessScope, TenantScope, scope_predicates
from app.services.embeddings import to_pgvector
from app.services.v0_gate import validate_provenance, validate_scope
from app.utils.ids import uuid7

_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]")
_WHITESPACE_RE = re.compile(r"\s+")


class GoalQualityRejected(ValueError):
    """Raised by find_or_create_goal (ingestion.md Sec 20) when
    `canonical_name` matches one of the disclosed low-quality patterns
    below -- a real rejection, not a warning, so a caller that doesn't
    explicitly catch it fails loudly rather than writing corpus-polluting
    junk (same "refuse rather than fabricate" posture this module's own
    tier 5 adjudication and the skill_extraction package's abstain
    contract already use elsewhere)."""


# ingestion.md Sec 20's own BAD examples ("use rg command", "fix stuff",
# "run this exact command in repo X") are the calibration set for this
# heuristic -- deliberately narrow and mechanical, NOT a claim of full
# semantic quality judgment (that would need its own LLM call per new
# Goal; a real, disclosed scope limit, not attempted here). Only catches
# the clear/mechanical failure modes Sec 20 names; a subtly-bad-but-not-
# mechanically-detectable Goal (e.g. an oddly-specific but grammatically
# fine phrase) still gets through -- left to the explicit merge/review
# workflow ingestion.md Sec 8 already describes as schema-supported-but-
# unimplemented, not solved here.
_VAGUE_FILLER_WORDS = frozenset({
    "stuff", "things", "thing", "issue", "issues", "problem", "problems",
    "bug", "bugs", "it", "this", "that", "fix", "handle", "do", "make",
    "misc", "stuffs", "whatever", "something", "somehow",
})
_COMMAND_TOOL_NAMES = frozenset({
    "rg", "grep", "git", "npm", "npx", "pnpm", "yarn", "pytest", "python",
    "python3", "node", "curl", "wget", "make", "cargo", "go", "docker",
    "kubectl", "ls", "cat", "sed", "awk", "bash", "sh", "pip",
})
# "use rg command" / "run the deploy script" -- a bare tool/command name
# sandwiched between an imperative verb and a generic noun for "a command",
# not a description of the outcome it produces.
_COMMAND_ECHO_RE = re.compile(
    r"^(use|run|call|execute|invoke) \w+ (command|script|tool|cli)$"
)
# "run this exact command in repo X" -- names the ACT of running something
# specific, not a reusable outcome.
_RUN_THIS_RE = re.compile(r"\brun (this|the) (exact |specific )?(command|script)\b")
# "src/generated/api.yaml" -- a literal file path is a hyper-specific local
# binding (ingestion.md Sec 9/20), not a generalizable outcome.
_FILE_PATH_RE = re.compile(r"[\w.-]+/[\w.-]+\.\w{1,6}\b")
_REPO_MENTION_RE = re.compile(r"\bin repo\b|\bin this repo(sitory)?\b|\brepo [a-z0-9_-]+\b")
# A backtick-delimited span with whitespace inside is a raw command/snippet
# echoed verbatim (e.g. "run `git commit -m \"...\"`") -- the failure mode
# Sec 20 actually means to catch. A single bare identifier in backticks
# (e.g. "Use the `openpyxl` library...", "the `themes/` directory") is just
# inline-code markdown around ordinary technical vocabulary inside an
# otherwise well-formed outcome sentence -- confirmed live 2026-09-22: this
# was rejecting ~45% of step-level goals from real anthropics/skills
# extractions for exactly that reason, not for describing a raw command.
_BACKTICK_SPAN_RE = re.compile(r"`([^`]*)`")


def describe_goal_quality_issue(canonical_name: str) -> Optional[str]:
    """None if `canonical_name` passes ingestion.md Sec 20's quality bar;
    otherwise a short human-readable reason it was rejected. Pure,
    read-only -- callers decide what to do with a rejection (raise,
    skip-and-log, etc.), this function only classifies."""
    normalized = normalize_goal_name(canonical_name)
    tokens = [t for t in normalized.split(" ") if t]
    if not tokens:
        return None  # the separate empty-name check in find_or_create_goal owns this case
    lowered = canonical_name.strip().lower()
    if any(" " in span for span in _BACKTICK_SPAN_RE.findall(canonical_name)):
        return "contains literal code/command syntax (backtick) -- not a described outcome"
    if _COMMAND_ECHO_RE.match(normalized) or _RUN_THIS_RE.search(lowered):
        return "reads as a raw command/tool invocation, not a reusable outcome"
    if _FILE_PATH_RE.search(canonical_name):
        return "names a hyper-specific literal file path, not a generalizable outcome"
    if _REPO_MENTION_RE.search(lowered):
        return "hyper-specific accidental local binding (names a specific repo)"
    if len(tokens) == 1 and tokens[0] in _COMMAND_TOOL_NAMES:
        return "is a bare tool/command name, not a described outcome"
    if len(tokens) <= 3 and all(t in _VAGUE_FILLER_WORDS for t in tokens):
        return "meaningless/vague label with no concrete outcome"
    return None

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


async def _find_remote_exact(pool, normalized: str, canonical_name: str, scope_type: str, scope_entity_id) -> Optional[dict]:
    """Exact identity across ALL shards: the global goal_names index (name) and the goal projection
    (aliases) -- then the canonical row is read from its home shard."""
    from app.services.shards import HOME_SHARD, pools_for

    scope_key = "global" if scope_type == "global" else f"{scope_type}:{scope_entity_id or ''}"
    hit = await pool.fetchrow(
        "SELECT goal_id::text AS id, home_shard_id FROM goal_names WHERE scope_key = $1 AND normalized_name = $2",
        scope_key, normalized)
    if hit is None:
        hit = await pool.fetchrow(
            "SELECT goal_id::text AS id, home_shard_id FROM goal_search_index WHERE home_shard_id <> $1 AND status <> 'merged' "
            "AND COALESCE(scope_type, 'global') = $2 AND COALESCE(scope_entity_id, '') = COALESCE($3, '') "
            "AND EXISTS (SELECT 1 FROM unnest(aliases) a WHERE lower(trim(a)) = lower(trim($4))) LIMIT 1",
            HOME_SHARD, scope_type, scope_entity_id, canonical_name)
    if hit is None or hit["home_shard_id"] == HOME_SHARD:
        return None
    shard_pool = await pools_for(pool).get(hit["home_shard_id"])
    row = await shard_pool.fetchrow("SELECT id, canonical_name FROM goals WHERE id = $1::uuid", hit["id"])
    if row is None:
        return None
    return {"id": str(row["id"]), "canonical_name": row["canonical_name"], "created": False,
            "home_shard_id": hit["home_shard_id"], "decision": "exact_match"}


async def _finish_remote_goal(pool, goal_id: str) -> None:
    """After a remote canonical write: queue the projection (durable) and apply it now (best effort),
    so the goal is searchable/identifiable immediately by concurrent workers."""
    from app.services.search_projection import enqueue, project_object
    from app.services.shards import pools_for

    await enqueue(pool, "goal", goal_id)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await project_object(conn, "goal", goal_id, pools=pools_for(pool))
    except Exception:  # noqa: BLE001 -- the outbox entry repairs it
        pass


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
    client: Optional[Any] = None,           # DEPRECATED: ignored (identity uses `judge`)
    adjudication_model: str = "",           # DEPRECATED: ignored
    judge: Optional[Any] = None,
    on_unavailable: Optional[str] = None,
    job_id: Optional[int] = None,
    idempotency_key: Optional[str] = None,
) -> dict[str, Any]:
    """Exact-name/alias identity, then semantic identity resolution
    (identity_resolution.resolve_goal_identity: FTS+vector candidates ->
    JEV/NLI judge) before inserting. Returns {"id": str, "canonical_name":
    str, "created": bool}.

    `scope_type`/`provenance` are REQUIRED and V0-gated (Band 1.3
    discipline, same as `capture_procedure` -- "nothing enters without
    scope + provenance"), not defaulted to a silent 'global' -- callers
    must make an explicit choice, same as every other write path in this
    codebase.

    `embedder`: an app.services.embeddings.Embedder (or anything with an
    async `embed_one_with_metadata(text, input_type=...)`). Passing one
    stores the goal's embedding and enables the vector leg of candidate
    generation (FTS always runs) -- one embedding call, opt-in.

    `judge`: an app.services.semantic.chain.SemanticJudge (default: built
    from settings). `on_unavailable`: "raise" (default when providers are
    configured -- retryable, no silent duplicate) or "create" (default when
    none are configured; recorded as decision `judge_unavailable`).
    `job_id`/`idempotency_key` tie the identity decision to one ingestion
    job so a replay reuses it. `client`/`adjudication_model` are DEPRECATED
    no-ops kept so existing call sites keep working.
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

    from app.services.shards import HOME_SHARD, multi_shard, pools_for, record_route

    if await multi_shard(pool):
        remote = await _find_remote_exact(pool, normalized, canonical_name, resolved_scope_type, resolved_scope_entity_id)
        if remote:
            return remote

    # Private/org writes (everything a user pushes from a local .stealth cache) must never be
    # blocked by a model outage: they are recorded and can be reviewed/merged later.
    if on_unavailable is None and visibility != "public":
        on_unavailable = "create"

    # Semantic identity (docs/dedup_and_identity.md): FTS + vector candidates
    # over canonical goals, RRF-fused, then a MODEL (JEV -> Gemini -> Gemma via
    # app.services.semantic) decides same / narrower / broader / related /
    # distinct. No SimHash or cosine-threshold merge exists any more. The
    # decision is persisted to identity_decisions. If the judge chain is down
    # while candidates exist this raises SemanticJudgmentUnavailable (a worker
    # retries the job) unless `on_unavailable="create"`.
    from app.services.identity_resolution import propose_goal_relations, resolve_goal_identity

    embedding_vec: Optional[list[float]] = None
    embedding_meta = None
    if embedder is not None:
        embedding_vec, embedding_meta = await embedder.embed_one_with_metadata(
            goal_embedding_text(canonical_name, description), input_type="document",
        )
    outcome = await resolve_goal_identity(
        pool, name=canonical_name, description=description, scope_type=resolved_scope_type,
        scope_entity_id=resolved_scope_entity_id, embedding=embedding_vec,
        embedding_model=embedding_meta.model_id if embedding_meta is not None else None,
        judge=judge, on_unavailable=on_unavailable, job_id=job_id, idempotency_key=idempotency_key,
    )
    if outcome.action == "reuse":
        from app.services.shards import home_pool as _home_pool
        owner_pool = await _home_pool(pool, "goal", outcome.resolved_id)
        matched = await owner_pool.fetchrow(
            "UPDATE goals SET aliases = CASE WHEN $2 = ANY(aliases) OR normalized_name = $3 "
            "THEN aliases ELSE array_append(aliases, $2) END WHERE id = $1::uuid "
            "RETURNING id, canonical_name, home_shard_id", outcome.resolved_id, canonical_name, normalized)
        if matched is not None:
            return {"id": str(matched["id"]), "canonical_name": matched["canonical_name"], "created": False,
                    "home_shard_id": matched.get("home_shard_id", "K000"), "decision": outcome.decision}

    # ingestion.md Sec 20's quality gate runs ONLY here -- right before a
    # genuinely NEW row would be created -- never earlier. Every dedup
    # tier above (1/2/2.5/3/4/5) still matches an EXISTING row by this
    # exact text regardless of its own quality, bad-or-good: this gate
    # stops NEW corpus pollution, it does not retroactively re-judge
    # legacy rows (CLAUDE.md hard rule 1: "no backfills... legacy rows
    # stay quarantined") or break a caller's dedup hit against one.
    quality_issue = describe_goal_quality_issue(canonical_name)
    if quality_issue is not None:
        raise GoalQualityRejected(
            f"canonical_name {canonical_name!r} rejected: {quality_issue}"
        )

    goal_id = uuid7()
    from app.services.shards import cached_shards, choose_shard, writable_shards

    home_shard = choose_shard(str(goal_id), writable_shards(await cached_shards(pool), visibility=visibility))
    wpool = pool
    if home_shard != HOME_SHARD:
        # REMOTE canonical write. The control database arbitrates exact identity through the
        # global goal_names index BEFORE the row exists anywhere, then routes, then writes.
        scope_key = "global" if resolved_scope_type == "global" else f"{resolved_scope_type}:{resolved_scope_entity_id or ''}"
        claimed = await pool.fetchrow(
            "INSERT INTO goal_names (scope_key, normalized_name, goal_id, home_shard_id) VALUES ($1, $2, $3::uuid, $4) "
            "ON CONFLICT (scope_key, normalized_name) DO NOTHING RETURNING goal_id", scope_key, normalized, str(goal_id), home_shard)
        if claimed is None:
            winner = await _find_remote_exact(pool, normalized, canonical_name, resolved_scope_type, resolved_scope_entity_id)
            if winner:
                return winner
            return await find_or_create_goal(
                pool, canonical_name=canonical_name, scope_type=scope_type, scope_entity_id=scope_entity_id,
                provenance=provenance, embedder=embedder, judge=judge, on_unavailable=on_unavailable,
                job_id=job_id, idempotency_key=idempotency_key, visibility=visibility)
        await record_route(pool, "goal", str(goal_id), home_shard)
        wpool = await pools_for(pool).get(home_shard)
    try:
        row = await wpool.fetchrow(
            """
            INSERT INTO goals (
                id, canonical_name, normalized_name, description, expected_outcome,
                verification_requirement, status, provenance, created_from, owner_id,
                visibility, aliases, created_by, scope_type, scope_entity_id,
                embedding, embedding_model_id, embedding_provider, embedding_text_hash,
                home_shard_id
            ) VALUES (
                $1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, $9, $10,
                $11::visibility_level, $12, $13, $14, $15,
                $16::vector, $17, $18, $19, $20
            )
            RETURNING id, canonical_name, home_shard_id
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
            home_shard,
        )
        if home_shard != HOME_SHARD:
            await _finish_remote_goal(pool, str(goal_id))
    except asyncpg.UniqueViolationError:
        # Lost a race against a concurrent insert of the identical
        # (normalized_name, scope) pair -- migration 83's partial unique index
        # caught it. Re-select the winner: semantically a successful dedup.
        return await find_or_create_goal(
            pool, canonical_name=canonical_name, scope_type=scope_type,
            scope_entity_id=scope_entity_id, provenance=provenance,
            embedder=embedder, judge=judge, on_unavailable=on_unavailable,
            job_id=job_id, idempotency_key=idempotency_key, visibility=visibility,
        )
    except Exception:
        if home_shard != HOME_SHARD:  # the remote write failed: release the global name claim and route
            await pool.execute("DELETE FROM goal_names WHERE goal_id = $1::uuid", str(goal_id))
            await pool.execute("DELETE FROM object_routes WHERE object_type = 'goal' AND object_id = $1::uuid", str(goal_id))
        raise
    if outcome.relations:
        await propose_goal_relations(pool, str(row["id"]), outcome.relations, decision_id=outcome.decision_id)
    return {"id": str(row["id"]), "canonical_name": row["canonical_name"], "created": True,
            "home_shard_id": row.get("home_shard_id", home_shard), "decision": outcome.decision}


async def find_or_create_goal_cached(
    pool: asyncpg.Pool,
    *,
    canonical_name: str,
    scope_type: str,
    scope_entity_id: Optional[str] = None,
    goal_cache: Optional[dict[tuple, dict[str, Any]]],
    **kwargs: Any,
) -> dict[str, Any]:
    """Thin wrapper over `find_or_create_goal`: skip the DB round trip
    AND the embedding/semantic-judge call entirely when this exact
    (scope, normalized name) was already resolved earlier in the SAME
    caller-owned `goal_cache` dict.

    WHY A WRAPPER, NOT CACHE LOGIC INSIDE `find_or_create_goal` ITSELF:
    that function has five distinct return points (exact match, remote
    exact match, judge-reuse, fresh insert, unique-violation-race retry)
    -- threading a cache write into every one of them is exactly the
    kind of edit that silently misses a path. Wrapping the whole call
    means every return value, from every tier, gets cached uniformly,
    with zero risk of divergence from the real function's own logic.

    `goal_cache=None` (the default at every existing call site unless a
    caller explicitly opts in) makes this byte-for-byte identical to
    calling `find_or_create_goal` directly -- no behavior change for any
    caller that doesn't pass a cache. A caller that DOES pass one owns
    its lifetime (typically one dict per document/job, so a repeated
    goal string within that document -- e.g. the same step goal restated
    across several extracted procedures -- resolves once, not N times);
    sharing one cache across multiple DB transactions/jobs is a caller
    decision this wrapper does not make for you.

    NOT a semantic/fuzzy cache: the key is `normalize_goal_name()` (tier
    1's own exact-match normalization), same as `find_or_create_goal`'s
    own first DB check -- a near-duplicate-but-not-identical goal string
    still goes through the real function's own tier 3/4/5 dedup, exactly
    as before. This cache only removes REPEATED identical work, never
    changes which row two different strings resolve to.
    """
    key = (scope_type, scope_entity_id, normalize_goal_name(canonical_name))
    if goal_cache is not None and key in goal_cache:
        return goal_cache[key]
    result = await find_or_create_goal(
        pool, canonical_name=canonical_name, scope_type=scope_type,
        scope_entity_id=scope_entity_id, **kwargs,
    )
    if goal_cache is not None:
        goal_cache[key] = result
    return result


async def get_goal(
    pool: asyncpg.Pool,
    goal_id: str,
    *,
    scope: Optional[AccessScope] = None,
    tenant_scope: Optional[TenantScope] = None,
) -> Optional[dict[str, Any]]:
    """One Goal plus its live Procedures (ingestion.md Sec 18). Summaries only
    (id/name/status) -- never the full procedure row, keeping this a
    lightweight Goal-centric view, not a second copy of `get_procedure`.

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
        for r in (await __import__("app.services.shards", fromlist=["fanout_fetch"]).fanout_fetch(
            pool,
            "SELECT id, procedure_id, name, verification_state, availability "
            "FROM procedures WHERE achieves_goal_id = $1 AND t_invalid IS NULL "
            "ORDER BY t_created DESC LIMIT 50",
            goal_id,
        ))[:50]
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
    # CONVERGED: the same candidate machinery as Tier-1 retrieval (global goal projection: FTS + ANN + RRF,
    # visibility-filtered, canonical rows hydrated from each goal's home shard). No second goal ranker.
    from app.services import retrieval_service as rs

    model = None
    if query_embedding:
        model = await pool.fetchval(
            "SELECT embedding_model FROM goal_search_index WHERE embedding IS NOT NULL GROUP BY 1 ORDER BY count(*) DESC LIMIT 1")
    return await rs.search_goal_candidates(
        pool, query_text=query_text, query_embedding=query_embedding, embedding_model=model,
        scope=scope or AccessScope.unrestricted(), status=status, limit=limit)


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
    client: Optional[Any] = None,
    adjudication_model: str = "gemma-4-31B-it",
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
        created_by=owner_id, embedder=embedder, client=client, adjudication_model=adjudication_model,
    )
    return {"outcome": "matched" if not result["created"] else "created", "goal": result}
