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
  tier 2.5 -- text SimHash near-duplicate match (always on, zero cost --
    pure Python, no embedder/LLM call): catches word-order changes, a
    word inserted/removed/swapped -- phrasings tier 1/2's EXACT match
    misses but that are not yet the "real paraphrase" job tier 3/4's
    embedding similarity is for. Founder decision (2026-09-16): explicitly
    text-only -- hashing the EMBEDDING vector (LSH) was considered and
    rejected as redundant with the exact HNSW+cosine-distance search tier
    3/4 already does at this table's scale (migration 84). See
    migration 86 and `compute_simhash`/`SIMHASH_MAX_HAMMING_DISTANCE`
    below.
  tier 3/4 -- embedding cosine-similarity match: OPT-IN via the
    `embedder` parameter (default None -- no behavior or cost change for
    a caller that doesn't pass one). Founder directive (2026-09-16):
    dedup should be AGGRESSIVE -- AUTO_DEDUP_MAX_COSINE_DISTANCE is set
    to auto-merge real paraphrases ("find callers" vs "find all callers
    of a function"), not just near-exact restatements.
  tier 5 -- LLM adjudication for the ambiguous middle band (embedding
    similarity present but between AUTO_DEDUP_MAX_COSINE_DISTANCE and
    AMBIGUOUS_DEDUP_MAX_COSINE_DISTANCE): OPT-IN via the `client`
    parameter (default None -- same zero-cost-unless-asked posture as
    `embedder`). One small, fail-closed LLM call decides "same goal or
    not"; a "same" verdict merges AND appends the candidate's own
    phrasing as a new alias on the surviving row (so the next caller with
    the SAME phrasing hits tier 2 for free, never needing another
    embedding call or LLM adjudication for it again). Any failure,
    malformed response, or explicit "different" verdict falls through to
    creating a new, distinct row -- conservative on uncertainty, only
    aggressive when the model is actually confident.
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

# Tier 3/4 auto-merge threshold (pgvector cosine DISTANCE -- smaller is
# more similar; 0.12 ~= cosine similarity >= 0.88). Founder directive
# (2026-09-16, "aggressive dedup for goals"): loosened from an earlier,
# more conservative 0.05 (~0.95 similarity, near-exact restatements only)
# -- this band now catches real paraphrases ("find callers" vs "find all
# callers of a function"), not just near-identical text. Configuration,
# not tuned against real production data yet -- a real, stated limitation.
AUTO_DEDUP_MAX_COSINE_DISTANCE = 0.12

# Tier 5's band: a candidate this close-but-not-auto-mergeable gets one
# LLM adjudication call (only if the caller passed `client`) before
# falling through to a new row. Wider than the auto-merge band on
# purpose -- this is exactly the "maybe the same, ask" zone the auto tier
# deliberately doesn't touch on similarity alone.
AMBIGUOUS_DEDUP_MAX_COSINE_DISTANCE = 0.30

_ADJUDICATION_SYSTEM_PROMPT = """You decide whether two short goal descriptions refer to \
the SAME reusable outcome, phrased differently, or two genuinely DIFFERENT outcomes.

Same goal, different phrasing (answer same=true): "find callers" / "find all callers of a \
function" / "locate every call site of a function". These describe the identical outcome.

Different goals (answer same=false): "find callers" vs "find callers and remove them" (the \
second has an extra, distinct outcome). "deploy a service" vs "deploy a service safely with \
a canary rollout" (materially different scope/rigor) -- when genuinely uncertain, answer false.

Reply with ONLY a JSON object, no other text: {"same": true} or {"same": false}
"""


async def _adjudicate_same_goal(
    client: Any, model: str, candidate_name: str, existing_name: str,
) -> bool:
    """Tier 5's one LLM call. Fail-closed to False on ANY problem (no
    client, API error, malformed/ambiguous response) -- adjudication only
    ever makes dedup MORE aggressive when the model is genuinely
    confident, never blocks a legitimate creation on an infrastructure
    hiccup."""
    if client is None:
        return False
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _ADJUDICATION_SYSTEM_PROMPT},
                {"role": "user", "content": f'Goal A: "{candidate_name}"\nGoal B: "{existing_name}"'},
            ],
            temperature=0.0,
            max_tokens=20,
        )
        text = (response.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001 -- any client/transport failure fails closed
        return False
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict) and parsed.get("same") is True

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


# Tier 2.5's auto-merge radius: two 64-bit SimHashes this close (out of 64
# possible differing bits) are treated as the same goal, text-only,
# no embedder/LLM call. Empirically calibrated (not just guessed) against
# short goal-name-length text: a single word inserted/dropped/pluralized
# lands around Hamming distance 10-13 (few tokens means each one carries
# a lot of the bit-vote weight -- SimHash on short bags-of-words is
# inherently coarser than on long documents), while a genuine
# different-wording paraphrase ("find all callers of a function" vs
# "locate every caller of a function") lands >= 22. 12 sits in the gap:
# catches the former, leaves the latter to tier 3/4's real semantic
# comparison. Still a real, stated limitation -- not tuned against real
# production data yet (same honesty as AUTO_DEDUP_MAX_COSINE_DISTANCE's
# own docstring), and a stemmed variant right at the boundary
# ("reconcile schema drift" vs "...drifts", distance ~17) will miss this
# tier and fall through to tier 3/4 (if opted in) instead -- not a bug,
# just this tier's coarseness.
SIMHASH_BITS = 64
SIMHASH_MAX_HAMMING_DISTANCE = 12


def compute_simhash(text: str, *, bits: int = SIMHASH_BITS) -> int:
    """64-bit SimHash of `text`'s normalized token set (Charikar's
    algorithm). Deliberately hashes the SAME `normalize_goal_name(text)`
    tier 1 already computes -- one normalization step feeds both an exact
    key and a fuzzy one, rather than two independently-drifting text
    transforms.

    Uses `hashlib.sha1` (or higher) per token, NEVER Python's built-in
    `hash()` -- that is salted per-process (`PYTHONHASHSEED`) and would
    make a stored SimHash uncomparable across two different process runs,
    silently breaking every future lookup against it.

    No token weighting (each distinct token votes once, via a `set`, not
    a multiset) -- goal names are short (a few words), so raw term
    frequency carries little extra signal here; this stays a coarse,
    honest bag-of-tokens fingerprint, not a claim of real semantic
    similarity (that is tier 3/4's job).
    """
    tokens = normalize_goal_name(text).split(" ")
    tokens = [t for t in tokens if t]
    if not tokens:
        return 0
    bit_votes = [0] * bits
    for token in set(tokens):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        token_hash = int.from_bytes(digest[:8], "big")
        for i in range(bits):
            bit_votes[i] += 1 if (token_hash >> i) & 1 else -1
    fingerprint = 0
    for i, vote in enumerate(bit_votes):
        if vote > 0:
            fingerprint |= 1 << i
    return fingerprint


def hamming_distance(a: int, b: int) -> int:
    """Number of differing bits between two SimHash fingerprints.

    Masked to 64 bits before counting: `goals.simhash` is a signed
    BIGINT (Postgres has no unsigned integer type), so a fingerprint with
    its top bit set round-trips through the DB as a NEGATIVE Python int
    (two's complement) -- masking recovers the correct unsigned bit
    pattern either way, so this is safe whether `a`/`b` came straight
    from `compute_simhash` (always non-negative) or back from a DB row
    (real, live-tested bug: an un-masked XOR of a negative value produces
    an infinite-precision Python int whose bit count is meaningless)."""
    return bin((a ^ b) & 0xFFFFFFFFFFFFFFFF).count("1")


def _simhash_to_int64(value: int) -> int:
    """Two's-complement fold into asyncpg/Postgres BIGINT's signed range
    -- `compute_simhash` produces an unsigned 0..2**64-1 value, but
    Postgres has no unsigned 64-bit type. Confirmed necessary by a real
    live-DB rehearsal failure (`asyncpg.exceptions.DataError: invalid
    input for query argument: value out of int64 range`) before this
    existed."""
    return value - (1 << 64) if value >= (1 << 63) else value


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
    client: Optional[Any] = None,
    adjudication_model: str = "gemma-4-31B-it",
) -> dict[str, Any]:
    """Tier 1 (exact name) + tier 2 (alias) dedup, then tier 3/4
    (embedding similarity) and tier 5 (LLM adjudication on an ambiguous
    near-match) before inserting. Returns {"id": str, "canonical_name":
    str, "created": bool}.

    `scope_type`/`provenance` are REQUIRED and V0-gated (Band 1.3
    discipline, same as `capture_procedure` -- "nothing enters without
    scope + provenance"), not defaulted to a silent 'global' -- callers
    must make an explicit choice, same as every other write path in this
    codebase.

    `embedder`: an app.services.embeddings.Embedder (or anything with an
    async `embed_one_with_metadata(text, input_type=...)` matching its
    signature). Passing one makes this function ALSO compute and store
    the new/matched goal's embedding, and adds the tier-3/4/5 semantic
    dedup passes -- a real latency/cost addition (one embedding API
    call), so it is opt-in, never silently applied to an existing caller.

    `client`: an OpenAI-compatible chat-completions client. Only used
    (one more real LLM call) when `embedder` found a near-match whose
    distance falls in the tier-5 ambiguous band -- see
    AMBIGUOUS_DEDUP_MAX_COSINE_DISTANCE. Also opt-in; omitting it just
    means an ambiguous near-match falls through to a new row, same as
    today's behavior without adjudication.
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

    # Tier 2.5: always-on, zero-cost text SimHash near-duplicate match --
    # no embedder/client needed, so this runs for every caller, not just
    # ones that opted into tier 3/4/5. A candidate list, not a single
    # nearest-neighbor SQL query -- BIGINT has no native Hamming-distance
    # operator in stock Postgres (migration 86), so the shortlist is
    # fetched by scope and compared in Python. Fine at this table's
    # current size (thousands of rows); a real, stated limitation if the
    # corpus grows large enough to matter -- not optimized preemptively.
    candidate_simhash = compute_simhash(canonical_name)
    if resolved_scope_type == "global":
        simhash_rows = await pool.fetch(
            "SELECT id, canonical_name, simhash FROM goals "
            "WHERE t_invalid IS NULL AND status <> 'merged' AND simhash IS NOT NULL "
            "AND (scope_type IS NULL OR scope_type = 'global')",
        )
    else:
        simhash_rows = await pool.fetch(
            "SELECT id, canonical_name, simhash FROM goals "
            "WHERE t_invalid IS NULL AND status <> 'merged' AND simhash IS NOT NULL "
            "AND scope_type = $1 AND scope_entity_id = $2",
            resolved_scope_type, resolved_scope_entity_id,
        )
    simhash_match = min(
        simhash_rows,
        key=lambda r: hamming_distance(candidate_simhash, r["simhash"]),
        default=None,
    )
    if (
        simhash_match is not None
        and hamming_distance(candidate_simhash, simhash_match["simhash"]) <= SIMHASH_MAX_HAMMING_DISTANCE
    ):
        await pool.execute(
            "UPDATE goals SET aliases = array_append(aliases, $2) "
            "WHERE id = $1::uuid AND NOT ($2 = ANY(aliases))",
            simhash_match["id"], canonical_name,
        )
        return {
            "id": str(simhash_match["id"]),
            "canonical_name": simhash_match["canonical_name"],
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
        # Tier 5: close but not auto-merge-close -- ask, don't guess, and
        # only if the caller opted into it via `client`. A "same" verdict
        # merges AND records the candidate's own phrasing as a new alias
        # on the surviving row, so this exact phrasing hits tier 2 (free,
        # no embedding/LLM call) on every future call.
        if (
            semantic_match and client is not None
            and semantic_match["dist"] <= AMBIGUOUS_DEDUP_MAX_COSINE_DISTANCE
        ):
            same = await _adjudicate_same_goal(
                client, adjudication_model, canonical_name, semantic_match["canonical_name"],
            )
            if same:
                await pool.execute(
                    "UPDATE goals SET aliases = array_append(aliases, $2) "
                    "WHERE id = $1::uuid AND NOT ($2 = ANY(aliases))",
                    semantic_match["id"], canonical_name,
                )
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
                embedding, embedding_model_id, embedding_provider, embedding_text_hash,
                simhash
            ) VALUES (
                $1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, $9, $10,
                $11::visibility_level, $12, $13, $14, $15,
                $16::vector, $17, $18, $19, $20
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
            _simhash_to_int64(candidate_simhash),
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
            embedder=embedder, client=client, adjudication_model=adjudication_model,
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
