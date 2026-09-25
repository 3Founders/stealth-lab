"""
Semantic identity resolution for Goals (and the shared candidate machinery for
Claims/Procedures) -- docs/dedup_and_identity.md.

Rule: deterministic search GENERATES candidates; a MODEL decides identity.

    exact normalized name / alias      -> deterministic identity (string equality,
                                          not a semantic heuristic) -- handled by
                                          the caller before this module
    Postgres FTS  +  pgvector ANN      -> candidate generation
    Reciprocal Rank Fusion             -> ordering of the fused candidate list
    SemanticJudge.judge_identity (JEV -> Gemini -> Gemma, no heuristic provider)
                                       -> same / narrower / broader / related / distinct
    (no `cosine <= X` auto-merge, no SimHash merge -- both were removed)

Failure policy (fail closed where correctness matters):
  * judge chain exhausted while candidates exist -> ``SemanticJudgmentUnavailable``
    (the ingestion worker turns this into a retryable job failure), unless the
    caller passed ``on_unavailable="create"`` (dev / no provider configured), in
    which case a new object is created and the decision is recorded as
    ``judge_unavailable`` so it can be reviewed/merged later.
  * every judged resolution is written to ``identity_decisions`` (durable, vendor
    independent). A replayed job with the same ``idempotency_key`` reuses its
    earlier decision instead of re-judging.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, cast

import asyncpg

from app.services.embeddings import to_pgvector
from app.services.semantic.chain import SemanticJudge
from app.services.semantic.errors import SemanticJudgmentUnavailable
from app.services.semantic.prompts import IDENTITY_PROMPT_VERSION

log = logging.getLogger(__name__)

RRF_K = 60
DEFAULT_FTS_K = 20
DEFAULT_VECTOR_K = 20
DEFAULT_JUDGE_TOP_N = 5
# A model verdict of "same" below this self-reported confidence is treated as
# "related" (fail closed). This is the MODEL's confidence, not a similarity threshold.
SAME_MIN_CONFIDENCE = 0.75
GoalIdentityMode = Literal["model", "none"]
VALID_GOAL_IDENTITY_MODES = ("model", "none")
VALID_ON_UNAVAILABLE = ("raise", "create")
VALID_DECISIONS = frozenset({
    "exact_match", "same", "related", "broader", "narrower", "contradicts",
    "distinct", "no_candidates", "judge_unavailable", "new_version",
})
VALID_DECISIONS_BY_OBJECT = {
    "goal": frozenset({
        "exact_match", "same", "related", "broader", "narrower", "contradicts",
        "distinct", "no_candidates", "judge_unavailable",
    }),
    "claim": frozenset({
        "exact_match", "same", "related", "broader", "narrower", "contradicts",
        "distinct", "no_candidates", "judge_unavailable",
    }),
    "procedure": frozenset({"same", "new_version", "distinct", "no_candidates", "judge_unavailable"}),
}
_SAFE_GOAL_CREATE_DECISIONS = frozenset({
    "distinct", "related", "narrower", "broader",
    "judge_unavailable", "no_candidates",
})
GOAL_ABSTRACTION_PLACEMENT_JOB = "goal_abstraction_placement"
GOAL_ABSTRACTION_AUDIT_JOB = "goal_abstraction_audit"
GOAL_ABSTRACTION_PLACEMENT_VERSION = "goal_abstraction_placement_v1"
GOAL_ABSTRACTION_AUDIT_REASONS = frozenset({"orphan", "uncertain"})


def goal_abstraction_placement_key(goal_id: str) -> str:
    return f"goal-abstraction-placement:{goal_id}"


def goal_abstraction_audit_key(goal_id: str, reason: str) -> str:
    if reason not in GOAL_ABSTRACTION_AUDIT_REASONS:
        raise ValueError(f"unsupported Goal abstraction audit reason: {reason}")
    return f"goal-abstraction-audit:{goal_id}:{reason}"


async def _enqueue_goal_abstraction_job(
    pool: asyncpg.Pool,
    *,
    job_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    goal_id: str,
    scope_type: str,
    scope_entity_id: Optional[str],
    owner_id: Optional[str],
    visibility: str,
    max_attempts: int,
    config_version: str,
) -> tuple[Optional[int], bool]:
    from app.ingestion import queue as ingestion_queue

    ingestion_queue.validate_scope(job_type, scope_type, visibility, owner_id)
    if isinstance(pool, asyncpg.Pool):
        return await ingestion_queue.enqueue(
            pool,
            job_type,
            payload,
            idempotency_key=idempotency_key,
            source_id=goal_id,
            scope_type=scope_type,
            scope_entity_id=scope_entity_id,
            owner_id=owner_id,
            visibility=visibility,
            config_version=config_version,
            max_attempts=max_attempts,
            offload=False,
        )
    auth_scope = {
        "public": "global_public",
        "private": "user_private",
        "org": "tenant_private",
    }.get(visibility, "system_internal")
    result = await pool.execute(
        """
        INSERT INTO ingestion_jobs (
            job_type, payload, idempotency_key, source_id, scope_type, scope_entity_id,
            owner_id, visibility, config_version, max_attempts, submitted_by_user_id,
            auth_tenant_id, auth_scope, auth_visibility, publication_allowed
        ) VALUES (
            $1, $2::jsonb, $3, $4, $5, $6, $7, $8, $9, $10, $11,
            $12::uuid, $13, $14, false
        )
        ON CONFLICT (job_type, idempotency_key)
            WHERE idempotency_key IS NOT NULL
        DO NOTHING
        """,
        job_type,
        payload,
        idempotency_key,
        goal_id,
        scope_type,
        scope_entity_id,
        owner_id,
        visibility,
        config_version,
        max_attempts,
        owner_id if visibility == "private" else None,
        scope_entity_id if visibility == "org" else None,
        auth_scope,
        visibility,
    )
    return None, str(result).endswith(" 1")


async def enqueue_goal_abstraction_placement(
    pool: asyncpg.Pool,
    goal_id: str,
    *,
    identity_decision_id: Optional[str],
    judge_mode: str = "model",
    scope_type: str,
    scope_entity_id: Optional[str],
    owner_id: Optional[str],
    visibility: str,
    goal_version: int = 1,
) -> tuple[Optional[int], bool]:
    from app.ingestion.config import WorkerConfig

    judge_mode = validate_judge_mode(judge_mode)
    config = WorkerConfig.from_env()
    key = goal_abstraction_placement_key(str(goal_id))
    payload = {
        "goal_id": str(goal_id),
        "identity_decision_id": str(identity_decision_id) if identity_decision_id else None,
        "judge_mode": judge_mode,
        "goal_version": int(goal_version),
        "candidate_limit": config.goal_abstraction_candidate_limit,
        "neighbor_seed_limit": config.goal_abstraction_neighbor_seed_limit,
        "neighbor_limit": config.goal_abstraction_neighbor_limit,
        "minimum_confidence": config.goal_abstraction_minimum_confidence,
        "idempotency_key": key,
    }
    return await _enqueue_goal_abstraction_job(
        pool,
        job_type=GOAL_ABSTRACTION_PLACEMENT_JOB,
        payload=payload,
        idempotency_key=key,
        goal_id=str(goal_id),
        scope_type=scope_type,
        scope_entity_id=scope_entity_id,
        owner_id=owner_id,
        visibility=visibility,
        max_attempts=config.max_attempts,
        config_version=GOAL_ABSTRACTION_PLACEMENT_VERSION,
    )


PLACEMENT_REPAIR_WINDOW_MINUTES = 7 * 24 * 60
PLACEMENT_REPAIR_BATCH = 200


async def enqueue_missing_goal_placements(
    pool: asyncpg.Pool,
    *,
    window_minutes: int = PLACEMENT_REPAIR_WINDOW_MINUTES,
    limit: int = PLACEMENT_REPAIR_BATCH,
) -> int:
    """Repair sweep: a Goal whose placement enqueue failed at creation time
    (find_or_create_goal logs and continues -- placement is optional
    enrichment) is re-enqueued here. Reads the control-plane projection, so
    Goals on every shard are covered. Bounded to recently projected Goals so
    it never pushes the whole legacy corpus through the judge in one pass."""
    rows = await pool.fetch(
        "SELECT g.goal_id::text AS id, COALESCE(g.scope_type, 'global') AS scope_type, g.scope_entity_id, "
        "g.owner_id, g.visibility::text AS visibility, g.version "
        "FROM goal_search_index g "
        "WHERE g.status IN ('active', 'candidate') "
        "AND g.updated_at >= now() - make_interval(mins => $1) "
        "AND NOT EXISTS (SELECT 1 FROM ingestion_jobs j WHERE j.job_type = $3 "
        "                AND j.idempotency_key = 'goal-abstraction-placement:' || g.goal_id::text) "
        "ORDER BY g.updated_at DESC, g.goal_id LIMIT $2",
        int(window_minutes), int(limit), GOAL_ABSTRACTION_PLACEMENT_JOB,
    )
    enqueued = 0
    for row in rows:
        scope_type = str(row["scope_type"])
        try:
            _job_id, created = await enqueue_goal_abstraction_placement(
                pool,
                str(row["id"]),
                identity_decision_id=None,
                scope_type=scope_type,
                scope_entity_id=None if scope_type == "global" else row["scope_entity_id"],
                owner_id=row["owner_id"],
                visibility=str(row["visibility"]),
                goal_version=int(row["version"] or 1),
            )
        except Exception:  # noqa: BLE001 -- retried on the next sweep
            log.warning("goal %s placement re-enqueue failed", row["id"], exc_info=True)
            continue
        enqueued += int(bool(created))
    return enqueued


async def enqueue_goal_abstraction_audit(
    pool: asyncpg.Pool,
    goal_id: str,
    *,
    reason: str,
    scope_type: str,
    scope_entity_id: Optional[str],
    owner_id: Optional[str],
    visibility: str,
) -> tuple[Optional[int], bool]:
    key = goal_abstraction_audit_key(str(goal_id), reason)
    payload = {
        "goal_id": str(goal_id),
        "reason": reason,
        "idempotency_key": key,
    }
    return await _enqueue_goal_abstraction_job(
        pool,
        job_type=GOAL_ABSTRACTION_AUDIT_JOB,
        payload=payload,
        idempotency_key=key,
        goal_id=str(goal_id),
        scope_type=scope_type,
        scope_entity_id=scope_entity_id,
        owner_id=owner_id,
        visibility=visibility,
        max_attempts=3,
        config_version=GOAL_ABSTRACTION_PLACEMENT_VERSION,
    )


class PermanentIdentityConflict(ValueError):
    def __init__(self, object_type: str, idempotency_key: str, reason: str):
        self.object_type = object_type
        self.idempotency_key = idempotency_key
        self.reason = reason
        self.permanent = True
        super().__init__(
            f"permanent identity conflict for {object_type} key {idempotency_key}: {reason}"
        )


class IdentityReplayError(ValueError):
    pass


def validate_identity_job_id(job_id: Any) -> Optional[int]:
    """Validate a trusted ingestion job id without coercing untrusted input."""
    if job_id is None:
        return None
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id <= 0:
        raise ValueError("identity_job_id must be a positive integer")
    return job_id


def validate_on_unavailable(on_unavailable: Optional[str]) -> Optional[str]:
    if on_unavailable is None:
        return None
    if not isinstance(on_unavailable, str) or on_unavailable not in VALID_ON_UNAVAILABLE:
        raise ValueError(
            f"on_unavailable must be one of {VALID_ON_UNAVAILABLE!r}, got {on_unavailable!r}"
        )
    return on_unavailable


def canonical_identity_text(text: Any) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", str(text or "")).casefold().split()
    )


def _canonical_scope_type(scope_type: Any) -> str:
    return str(scope_type or "global").strip().lower()


def identity_idempotency_key(
    job_id: Optional[int] = None,
    source_hash: str = "",
    object_type: str = "",
    semantic_role: str = "",
    scope_type: str = "global",
    scope_entity_id: Optional[str] = None,
    text: str = "",
) -> Optional[str]:
    """Return the stable replay key for one job-owned identity operation."""
    job_id = validate_identity_job_id(job_id)
    if job_id is None:
        return None
    normalized_text = canonical_identity_text(text)
    fields = (
        str(job_id),
        str(source_hash or "").strip().lower(),
        str(object_type or "").strip().lower(),
        str(semantic_role or "").strip(),
        _canonical_scope_type(scope_type),
        "" if scope_entity_id is None else str(scope_entity_id),
        normalized_text,
    )
    encoded = "\x1f".join(f"{len(value)}:{value}" for value in fields)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_judge_mode(judge_mode: str) -> GoalIdentityMode:
    if judge_mode not in VALID_GOAL_IDENTITY_MODES:
        raise ValueError(
            f"judge_mode must be one of {VALID_GOAL_IDENTITY_MODES!r}, got {judge_mode!r}"
        )
    return cast(GoalIdentityMode, judge_mode)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an the of to for in on at by with and or from into is are be as it its this that".split())

_RELATION_TO_DECISION = {
    "same": "same", "specializes": "narrower", "generalizes": "broader", "related": "related",
    "contradicts": "contradicts", "distinct": "distinct", "refinement": "new_version",
}


def fts_or_query(text: str, *, max_terms: int = 24) -> Optional[str]:
    """OR-joined ``to_tsquery`` string from free text (recall-oriented: FTS only
    generates candidates; ``plainto_tsquery``'s AND would drop paraphrases)."""
    seen: list[str] = []
    for tok in _TOKEN_RE.findall(text.lower()):
        if tok in _STOP or tok in seen:
            continue
        seen.append(tok)
    if not seen:
        return None
    return " | ".join(seen[:max_terms])


def rrf_fuse(*ranked: list[str], k: int = RRF_K) -> dict[str, float]:
    """id -> fused score across ranked id lists (rank starts at 1)."""
    scores: dict[str, float] = {}
    for lst in ranked:
        for rank, oid in enumerate(lst, start=1):
            scores[oid] = scores.get(oid, 0.0) + 1.0 / (k + rank)
    return scores


@dataclass
class Candidate:
    id: str
    name: str
    text: str
    fts_rank: Optional[int] = None
    vec_rank: Optional[int] = None
    vec_distance: Optional[float] = None
    rrf: float = 0.0
    home_shard_id: Optional[str] = None
    relation: Optional[str] = None
    confidence: Optional[float] = None

    def as_json(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "text" and v is not None}


@dataclass
class IdentityOutcome:
    action: Literal["reuse", "create"]
    decision: str
    resolved_id: Optional[str] = None
    candidates: list[Candidate] = field(default_factory=list)
    relations: list[Candidate] = field(default_factory=list)  # narrower/broader/related candidates
    decision_id: Optional[str] = None
    fts_candidates: int = 0
    vector_candidates: int = 0
    judge_provider: Optional[str] = None
    judge_model: Optional[str] = None
    reused_decision: bool = False


_DEFAULT_JUDGE: Optional[SemanticJudge] = None


def default_judge() -> SemanticJudge:
    """Process-wide judge built from settings (JEV -> Gemini -> Gemma)."""
    global _DEFAULT_JUDGE
    if _DEFAULT_JUDGE is None:
        _DEFAULT_JUDGE = SemanticJudge.from_settings()
    return _DEFAULT_JUDGE


def reset_default_judge() -> None:
    global _DEFAULT_JUDGE
    _DEFAULT_JUDGE = None


# --------------------------------------------------------------- candidates

_SCOPE_GLOBAL = "(scope_type IS NULL OR scope_type = 'global')"


def _scope_clause(scope_type: str, n: int) -> tuple[str, list[Any]]:
    """WHERE fragment (params start at $n) restricting to one goal scope."""
    if scope_type == "global":
        return _SCOPE_GLOBAL, []
    return f"scope_type = ${n} AND scope_entity_id = ${n + 1}", [scope_type]


async def generate_goal_candidates(
    pool: asyncpg.Pool, text: str, *, scope_type: str, scope_entity_id: Optional[str],
    embedding: Optional[list[float]] = None, embedding_model: Optional[str] = None,
    fts_k: int = DEFAULT_FTS_K, vector_k: int = DEFAULT_VECTOR_K, top_n: int = DEFAULT_JUDGE_TOP_N,
    exclude_id: Optional[str] = None, created_within: Optional[tuple[Any, float]] = None,
) -> tuple[list[Candidate], int, int]:
    """FTS + ANN candidates over CANONICAL ``goals`` (never the projection: a
    goal committed a millisecond ago by a concurrent worker must be visible),
    fused with RRF. Returns (top_n fused, n_fts, n_vector).

    Uses migration 83's ``idx_goals_fts`` expression and migration 84's HNSW
    index on ``goals.embedding``. The vector leg only compares vectors from the
    SAME embedding model (never mixes vector spaces)."""
    scope_sql, extra = _scope_clause(scope_type, 1)
    params: list[Any] = list(extra)
    if scope_type != "global":
        params.append(scope_entity_id)
    base = f"t_invalid IS NULL AND status <> 'merged' AND {scope_sql}"
    if exclude_id:
        params.append(exclude_id)
        base += f" AND id <> ${len(params)}::uuid"
    if created_within:  # (anchor timestamp, +/- minutes): only goals created near the anchor
        params.extend([created_within[0], float(created_within[1])])
        base += (f" AND t_created BETWEEN ${len(params) - 1}::timestamptz - make_interval(mins => ${len(params)}) "
                 f"AND ${len(params) - 1}::timestamptz + make_interval(mins => ${len(params)})")

    by_id: dict[str, Candidate] = {}
    fts_ids: list[str] = []
    q = fts_or_query(text)
    if q:
        rows = await pool.fetch(
            f"SELECT id::text AS id, canonical_name, description, home_shard_id, "
            f"ts_rank_cd(to_tsvector('english', canonical_name || ' ' || COALESCE(description, '')), "
            f"to_tsquery('english', ${len(params) + 1})) AS r FROM goals "
            f"WHERE {base} AND to_tsvector('english', canonical_name || ' ' || COALESCE(description, '')) "
            f"@@ to_tsquery('english', ${len(params) + 1}) ORDER BY r DESC, id LIMIT {int(fts_k)}",
            *params, q)
        for rank, r in enumerate(rows, start=1):
            c = by_id.setdefault(r["id"], Candidate(r["id"], r["canonical_name"], _goal_text(r), home_shard_id=r["home_shard_id"]))
            c.fts_rank = rank
            fts_ids.append(r["id"])

    vec_ids: list[str] = []
    if embedding is not None and embedding_model:
        vparams = params + [to_pgvector(embedding), embedding_model]
        n_vec, n_model = len(params) + 1, len(params) + 2
        rows = await pool.fetch(
            f"SELECT id::text AS id, canonical_name, description, home_shard_id, "
            f"embedding <=> ${n_vec}::vector AS dist FROM goals "
            f"WHERE {base} AND embedding IS NOT NULL AND embedding_model_id = ${n_model} "
            f"ORDER BY dist ASC, id LIMIT {int(vector_k)}", *vparams)
        for rank, r in enumerate(rows, start=1):
            c = by_id.setdefault(r["id"], Candidate(r["id"], r["canonical_name"], _goal_text(r), home_shard_id=r["home_shard_id"]))
            c.vec_rank, c.vec_distance = rank, float(r["dist"])
            vec_ids.append(r["id"])

    # Goals homed on OTHER shards are not in this database's `goals`: candidates for them come
    # from the global goal projection (their canonical rows stay on their shard).
    from app.services.shards import HOME_SHARD, multi_shard

    if await multi_shard(pool):
        rparams: list[Any] = [HOME_SHARD, "global" if scope_type == "global" else scope_type, scope_entity_id or ""]
        rbase = ("home_shard_id <> $1 AND status <> 'merged' AND COALESCE(scope_type, 'global') = $2 "
                 "AND COALESCE(scope_entity_id, '') = $3")
        if exclude_id:
            rparams.append(exclude_id)
            rbase += f" AND goal_id <> ${len(rparams)}::uuid"
        if q:
            n = len(rparams) + 1
            for rank, r in enumerate(await pool.fetch(
                    f"SELECT goal_id::text AS id, canonical_name, short_description AS description, home_shard_id FROM goal_search_index "
                    f"WHERE {rbase} AND search_tsv @@ to_tsquery('english', ${n}) "
                    f"ORDER BY ts_rank_cd(search_tsv, to_tsquery('english', ${n})) DESC, goal_id LIMIT {int(fts_k)}", *rparams, q), 1):
                c = by_id.setdefault(r["id"], Candidate(r["id"], r["canonical_name"], _goal_text(r), home_shard_id=r["home_shard_id"]))
                c.fts_rank = c.fts_rank or rank
                fts_ids.append(r["id"])
        if embedding is not None and embedding_model:
            n = len(rparams) + 1
            for rank, r in enumerate(await pool.fetch(
                    f"SELECT goal_id::text AS id, canonical_name, short_description AS description, home_shard_id, "
                    f"embedding <=> ${n}::vector AS dist FROM goal_search_index WHERE {rbase} AND embedding IS NOT NULL "
                    f"AND embedding_model = ${n + 1} ORDER BY dist, goal_id LIMIT {int(vector_k)}",
                    *rparams, to_pgvector(embedding), embedding_model), 1):
                c = by_id.setdefault(r["id"], Candidate(r["id"], r["canonical_name"], _goal_text(r), home_shard_id=r["home_shard_id"]))
                c.vec_rank, c.vec_distance = rank, float(r["dist"])
                vec_ids.append(r["id"])

    scores = rrf_fuse(fts_ids, vec_ids)
    for oid, sc in scores.items():
        by_id[oid].rrf = sc
    fused = sorted(by_id.values(), key=lambda c: (-c.rrf, c.id))[:top_n]
    return fused, len(fts_ids), len(vec_ids)


def _goal_text(r) -> str:
    d = r["description"]
    return f"{r['canonical_name']}: {d}" if d else r["canonical_name"]


# ------------------------------------------------------------------ resolve


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, IndexError, TypeError, AttributeError):
        return getattr(row, key, default)


def _scope_matches(
    stored_type: Any,
    stored_entity_id: Any,
    expected_type: Any,
    expected_entity_id: Any,
) -> bool:
    return (
        _canonical_scope_type(stored_type) == _canonical_scope_type(expected_type)
        and stored_entity_id == expected_entity_id
    )


def _decision_metadata_mismatch_reason(
    row: Any,
    *,
    candidate_text: str,
    scope_type: str,
    scope_entity_id: Optional[str],
    job_id: Optional[int] = None,
) -> Optional[str]:
    stored_text = _row_value(row, "candidate_text")
    if (
        not isinstance(stored_text, str)
        or canonical_identity_text(stored_text) != canonical_identity_text(candidate_text)
    ):
        return "candidate_text"
    if _row_value(row, "job_id") != job_id:
        return "job_id"
    if not _scope_matches(
        _row_value(row, "scope_type"),
        _row_value(row, "scope_entity_id"),
        scope_type,
        scope_entity_id,
    ):
        return "scope"
    return None


def _decision_metadata_matches(
    row: Any,
    *,
    candidate_text: str,
    scope_type: str,
    scope_entity_id: Optional[str],
    job_id: Optional[int] = None,
) -> bool:
    return _decision_metadata_mismatch_reason(
        row,
        candidate_text=candidate_text,
        scope_type=scope_type,
        scope_entity_id=scope_entity_id,
        job_id=job_id,
    ) is None


def _candidate_number(value: Any, field_name: str, *, minimum: Optional[float] = None,
                      maximum: Optional[float] = None) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IdentityReplayError(f"candidate {field_name} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise IdentityReplayError(f"candidate {field_name} must be finite")
    if minimum is not None and number < minimum:
        raise IdentityReplayError(f"candidate {field_name} is below its minimum")
    if maximum is not None and number > maximum:
        raise IdentityReplayError(f"candidate {field_name} is above its maximum")
    return number


def _candidate_rank(value: Any, field_name: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise IdentityReplayError(f"candidate {field_name} must be a positive integer")
    return value


def _candidate_from_raw(raw: Any) -> Candidate:
    if not isinstance(raw, dict):
        raise IdentityReplayError("stored candidate must be an object")
    identifier = raw.get("id")
    if not isinstance(identifier, str) or not identifier.strip():
        raise IdentityReplayError("stored candidate id must be a non-empty string")
    name = raw.get("name", "")
    text = raw.get("text", "")
    if name is None:
        name = ""
    if text is None:
        text = ""
    if not isinstance(name, str) or not isinstance(text, str):
        raise IdentityReplayError("stored candidate name and text must be strings")
    relation = raw.get("relation")
    if relation is not None and not isinstance(relation, str):
        raise IdentityReplayError("stored candidate relation must be a string")
    if relation in ("narrower", "broader"):
        relation = "specializes" if relation == "narrower" else "generalizes"
    if relation is not None and relation not in {
        "same", "distinct", "related", "contradicts", "specializes",
        "generalizes", "refinement",
    }:
        raise IdentityReplayError("stored candidate relation is not allowed")
    home_shard_id = raw.get("home_shard_id")
    if home_shard_id is not None and not isinstance(home_shard_id, str):
        raise IdentityReplayError("stored candidate home_shard_id must be a string")
    return Candidate(
        id=identifier,
        name=name,
        text=text,
        fts_rank=_candidate_rank(raw.get("fts_rank"), "fts_rank"),
        vec_rank=_candidate_rank(raw.get("vec_rank"), "vec_rank"),
        vec_distance=_candidate_number(raw.get("vec_distance"), "vec_distance", minimum=0.0),
        rrf=_candidate_number(raw.get("rrf"), "rrf", minimum=0.0) or 0.0,
        home_shard_id=home_shard_id,
        relation=relation,
        confidence=_candidate_number(raw.get("confidence"), "confidence", minimum=0.0, maximum=1.0),
    )


def _validate_candidate_object(candidate: Any) -> Candidate:
    if not isinstance(candidate, Candidate):
        raise IdentityReplayError("decision candidates must contain Candidate objects")
    if not isinstance(candidate.id, str) or not candidate.id.strip():
        raise IdentityReplayError("candidate id must be a non-empty string")
    if not isinstance(candidate.name, str) or not isinstance(candidate.text, str):
        raise IdentityReplayError("candidate name and text must be strings")
    return _candidate_from_raw({
        "id": candidate.id,
        "name": candidate.name,
        "text": candidate.text,
        "fts_rank": candidate.fts_rank,
        "vec_rank": candidate.vec_rank,
        "vec_distance": candidate.vec_distance,
        "rrf": candidate.rrf,
        "home_shard_id": candidate.home_shard_id,
        "relation": candidate.relation,
        "confidence": candidate.confidence,
    })


def _validate_candidate_objects(candidates: Any) -> list[Candidate]:
    if not isinstance(candidates, list):
        raise IdentityReplayError("decision candidates must be a list")
    return [_validate_candidate_object(candidate) for candidate in candidates]


def _validate_decision(object_type: str, decision: Any, resolved_id: Any,
                       candidates: list[Candidate]) -> Optional[str]:
    allowed = VALID_DECISIONS_BY_OBJECT.get(object_type)
    if (
        allowed is None
        or not isinstance(decision, str)
        or decision not in VALID_DECISIONS
        or decision not in allowed
    ):
        raise IdentityReplayError(f"decision is not allowed for {object_type}: {decision!r}")
    if resolved_id is not None and not isinstance(resolved_id, str):
        raise IdentityReplayError("resolved_id must be a string")
    resolved = None if resolved_id is None else resolved_id
    if decision in ("exact_match", "same", "new_version"):
        if not resolved or resolved not in {candidate.id for candidate in candidates}:
            raise IdentityReplayError(
                f"{decision} resolved_id must be a member of stored candidates"
            )
    elif resolved is not None:
        raise IdentityReplayError(f"{decision} must not have resolved_id")
    if decision == "no_candidates" and candidates:
        raise IdentityReplayError("no_candidates must store an empty candidate list")
    return resolved


def _stored_candidates(value: Any) -> list[Candidate]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise IdentityReplayError("stored candidates JSON is invalid") from exc
    if not isinstance(value, list):
        raise IdentityReplayError("stored candidates must be a JSON array")
    candidates = [_candidate_from_raw(raw) for raw in value]
    if len({candidate.id for candidate in candidates}) != len(candidates):
        raise IdentityReplayError("stored candidate ids must be unique")
    return candidates


def _validate_stored_decision_row(row: Any, object_type: str) -> tuple[str, list[Candidate], Optional[str]]:
    if row is None:
        raise IdentityReplayError("identity decision row is missing")
    decision_id = _row_value(row, "id")
    if decision_id is None or not str(decision_id).strip():
        raise IdentityReplayError("identity decision id is missing")
    candidate_text = _row_value(row, "candidate_text")
    if not isinstance(candidate_text, str) or not canonical_identity_text(candidate_text):
        raise IdentityReplayError("identity decision candidate_text is invalid")
    decision = _row_value(row, "decision")
    candidates = _stored_candidates(_row_value(row, "candidates"))
    resolved = _validate_decision(object_type, decision, _row_value(row, "resolved_id"), candidates)
    return str(decision), candidates, resolved


async def _load_prior_decision(pool, object_type: str, key: str) -> Optional[asyncpg.Record]:
    from app.services.shards import search_pool

    return await (await search_pool(pool)).fetchrow(
        "SELECT id::text AS id, candidate_text, scope_type, scope_entity_id, decision, "
        "resolved_id::text AS resolved_id, candidates, judge_provider, judge_model, "
        "fts_candidates, vector_candidates, job_id, detail, idempotency_key "
        "FROM identity_decisions WHERE object_type = $1 AND idempotency_key = $2",
        object_type,
        key,
    )


async def _live_goal(
    pool: Any,
    goal_id: str,
    *,
    scope_type: str,
    scope_entity_id: Optional[str],
) -> bool:
    from app.services.shards import home_pool

    owner = await home_pool(pool, "goal", str(goal_id))
    query = (
        "SELECT 1 FROM goals WHERE id = $1::uuid AND t_invalid IS NULL "
        "AND status <> 'merged' AND (($2 = 'global' AND "
        "(scope_type IS NULL OR scope_type = 'global') AND scope_entity_id IS NULL) "
        "OR ($2 <> 'global' AND scope_type = $2 AND scope_entity_id = $3))"
    )
    args = (str(goal_id), scope_type or "global", scope_entity_id)
    if hasattr(owner, "fetchval"):
        return bool(await owner.fetchval(query, *args))
    return bool(await owner.fetchrow(query, *args))


def _stored_count(row: Any, field_name: str) -> int:
    value = _row_value(row, field_name, 0)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise IdentityReplayError(f"stored {field_name} must be a non-negative integer")
    return value


async def _replay_goal_decision(
    pool: Any,
    prior: Any,
    *,
    scope_type: str,
    scope_entity_id: Optional[str],
    on_unavailable: str,
    reused_decision: bool = True,
) -> IdentityOutcome:
    decision, candidates, resolved_id = _validate_stored_decision_row(prior, "goal")
    if decision == "judge_unavailable" and on_unavailable != "create":
        raise IdentityReplayError("stored judge_unavailable decision conflicts with on_unavailable='raise'")
    relations = [
        candidate for candidate in candidates
        if candidate.relation in ("specializes", "generalizes", "related")
    ]
    common = {
        "candidates": candidates,
        "relations": relations,
        "decision_id": _row_value(prior, "id"),
        "fts_candidates": _stored_count(prior, "fts_candidates"),
        "vector_candidates": _stored_count(prior, "vector_candidates"),
        "judge_provider": _row_value(prior, "judge_provider"),
        "judge_model": _row_value(prior, "judge_model"),
        "reused_decision": reused_decision,
    }
    if decision in _SAFE_GOAL_CREATE_DECISIONS:
        return IdentityOutcome(
            action="create", decision=decision, resolved_id=None, **common
        )
    if decision not in ("same", "exact_match"):
        raise IdentityReplayError(f"unsupported goal replay decision: {decision!r}")
    if not resolved_id or not await _live_goal(
        pool, resolved_id, scope_type=scope_type, scope_entity_id=scope_entity_id
    ):
        raise IdentityReplayError("stored goal decision targets a dead goal")
    return IdentityOutcome(
        action="reuse", decision=decision, resolved_id=resolved_id, **common
    )


async def record_decision(
    pool: asyncpg.Pool, *, object_type: str, candidate_text: str, scope_type: Optional[str],
    scope_entity_id: Optional[str], decision: str, resolved_id: Optional[str], candidates: list[Candidate],
    judge: Optional[SemanticJudge], provider: Optional[str], model: Optional[str], fts_n: int, vec_n: int,
    job_id: Optional[int], idempotency_key: Optional[str], detail: Optional[dict] = None,
    return_row: bool = True,
) -> Any:
    job_id = validate_identity_job_id(job_id)
    canonical_text = canonical_identity_text(candidate_text)
    validated_candidates = _validate_candidate_objects(candidates)
    validated_resolved = _validate_decision(object_type, decision, resolved_id, validated_candidates)
    if detail is not None and not isinstance(detail, dict):
        raise IdentityReplayError("identity decision detail must be an object")
    scope_value = None if scope_type is None else _canonical_scope_type(scope_type)
    if isinstance(fts_n, bool) or not isinstance(fts_n, int) or fts_n < 0:
        raise IdentityReplayError("fts_n must be a non-negative integer")
    if isinstance(vec_n, bool) or not isinstance(vec_n, int) or vec_n < 0:
        raise IdentityReplayError("vec_n must be a non-negative integer")
    from app.services.shards import search_pool

    try:
        row = await (await search_pool(pool)).fetchrow(
            """
            INSERT INTO identity_decisions (object_type, candidate_text, scope_type, scope_entity_id, decision,
                resolved_id, candidates, judge_chain, judge_provider, judge_model, prompt_version,
                fts_candidates, vector_candidates, job_id, idempotency_key, detail)
            VALUES ($1, $2, $3, $4, $5, $6::uuid, $7::jsonb, $8, $9, $10, $11, $12, $13, $14, $15, $16::jsonb)
            ON CONFLICT (object_type, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
            RETURNING id::text AS id, candidate_text, scope_type, scope_entity_id, decision,
                resolved_id::text AS resolved_id, candidates, judge_provider, judge_model,
                fts_candidates, vector_candidates, job_id, detail, idempotency_key
            """,
            object_type, candidate_text, scope_value, scope_entity_id, decision, validated_resolved,
            [candidate.as_json() for candidate in validated_candidates],
            (judge.chain_id if judge else None), provider, model, IDENTITY_PROMPT_VERSION,
            fts_n, vec_n, job_id, idempotency_key, detail or {})
    except asyncpg.UniqueViolationError:
        if idempotency_key is None:
            raise
        row = None
    if row:
        return row if return_row else _row_value(row, "id")
    if not idempotency_key:
        return None
    prior = await _load_prior_decision(pool, object_type, idempotency_key)
    if prior is None:
        raise IdentityReplayError("identity decision conflict has no readable winner")
    mismatch = _decision_metadata_mismatch_reason(
        prior,
        candidate_text=canonical_text,
        scope_type=scope_value or "global",
        scope_entity_id=scope_entity_id,
        job_id=job_id,
    )
    if mismatch:
        raise PermanentIdentityConflict(
            object_type, idempotency_key, f"{mismatch} differs from the stored decision"
        )
    _validate_stored_decision_row(prior, object_type)
    return prior if return_row else _row_value(prior, "id")


async def load_goal_identity_decision(
    pool: asyncpg.Pool, decision_id: str
) -> Optional[dict[str, Any]]:
    from uuid import UUID

    try:
        normalized = str(UUID(str(decision_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("decision_id must be a UUID") from exc
    from app.services.shards import search_pool

    row = await (await search_pool(pool)).fetchrow(
        """
        SELECT id::text AS id, candidate_text, scope_type, scope_entity_id, decision,
               resolved_id::text AS resolved_id, candidates, judge_provider, judge_model,
               fts_candidates, vector_candidates, job_id, detail, idempotency_key
        FROM identity_decisions
        WHERE object_type = 'goal' AND id = $1::uuid
        """,
        normalized,
    )
    if row is None:
        return None
    result = dict(row)
    result["candidates"] = _stored_candidates(result.get("candidates"))
    return result


async def resolve_goal_identity(
    pool: asyncpg.Pool, *, name: str, description: Optional[str], scope_type: str,
    scope_entity_id: Optional[str], embedding: Optional[list[float]], embedding_model: Optional[str],
    judge: Optional[SemanticJudge] = None, judge_mode: GoalIdentityMode = "model",
    on_unavailable: Optional[str] = None,
    job_id: Optional[int] = None, idempotency_key: Optional[str] = None,
    fts_k: int = DEFAULT_FTS_K, vector_k: int = DEFAULT_VECTOR_K, top_n: int = DEFAULT_JUDGE_TOP_N,
    same_min_confidence: float = SAME_MIN_CONFIDENCE,
) -> IdentityOutcome:
    """Decide whether the candidate Goal already exists. Never mutates ``goals``;
    the caller creates the row on ``action == 'create'``. ``judge_mode="model"``
    runs semantic candidate generation and model judging; ``judge_mode="none"``
    skips both and returns an explicit unpersisted create outcome."""
    on_unavailable = validate_on_unavailable(on_unavailable)
    judge_mode = validate_judge_mode(judge_mode)
    job_id = validate_identity_job_id(job_id)
    if judge_mode == "none":
        return IdentityOutcome(
            action="create", decision="judge_mode_none", candidates=[], relations=[],
            decision_id=None, fts_candidates=0, vector_candidates=0,
        )
    judge = judge if judge is not None else default_judge()
    if on_unavailable is None:
        on_unavailable = "raise" if judge.providers else "create"
    cand_text = f"{name}: {description}" if description else name

    if idempotency_key:
        prior = await _load_prior_decision(pool, "goal", idempotency_key)
        if prior is not None:
            mismatch = _decision_metadata_mismatch_reason(
                prior,
                candidate_text=cand_text,
                scope_type=scope_type,
                scope_entity_id=scope_entity_id,
                job_id=job_id,
            )
            if mismatch:
                raise PermanentIdentityConflict(
                    "goal", idempotency_key, f"{mismatch} differs from the stored decision"
                )
            return await _replay_goal_decision(
                pool, prior, scope_type=scope_type, scope_entity_id=scope_entity_id,
                on_unavailable=on_unavailable,
            )

    t0 = time.monotonic()
    candidates, n_fts, n_vec = await generate_goal_candidates(
        pool, cand_text, scope_type=scope_type, scope_entity_id=scope_entity_id, embedding=embedding,
        embedding_model=embedding_model, fts_k=fts_k, vector_k=vector_k, top_n=top_n)
    candidates = _validate_candidate_objects(candidates)
    cand_ms = (time.monotonic() - t0) * 1000

    async def _finish(decision: str, resolved: Optional[str], *, provider=None, model=None, relations=None,
                      action: str = "create", detail: Optional[dict] = None) -> IdentityOutcome:
        detail = {**(detail or {}), "candidate_ms": round(cand_ms, 1)}
        if decision == "no_candidates" and idempotency_key is None:
            return IdentityOutcome(
                action=action,
                decision=decision,
                resolved_id=resolved,
                candidates=candidates,
                relations=relations or [],
                decision_id=None,
                fts_candidates=n_fts,
                vector_candidates=n_vec,
                judge_provider=provider,
                judge_model=model,
            )
        written = await record_decision(
            pool, object_type="goal", candidate_text=cand_text, scope_type=scope_type,
            scope_entity_id=scope_entity_id, decision=decision, resolved_id=resolved, candidates=candidates,
            judge=judge, provider=provider, model=model, fts_n=n_fts, vec_n=n_vec, job_id=job_id,
            idempotency_key=idempotency_key, detail=detail)
        if written is None:
            if idempotency_key is not None:
                raise IdentityReplayError("identity decision was not persisted")
            return IdentityOutcome(
                action=action,
                decision=decision,
                resolved_id=resolved,
                candidates=candidates,
                relations=relations or [],
                decision_id=None,
                fts_candidates=n_fts,
                vector_candidates=n_vec,
                judge_provider=provider,
                judge_model=model,
            )
        if _row_value(written, "decision", None) is not None:
            return await _replay_goal_decision(
                pool, written, scope_type=scope_type, scope_entity_id=scope_entity_id,
                on_unavailable=on_unavailable, reused_decision=False,
            )
        return IdentityOutcome(
            action=action,
            decision=decision,
            resolved_id=resolved,
            candidates=candidates,
            relations=relations or [],
            decision_id=_row_value(written, "id"),
            fts_candidates=n_fts,
            vector_candidates=n_vec,
            judge_provider=provider,
            judge_model=model,
        )


    if not candidates:
        return await _finish("no_candidates", None)

    relations: list[Candidate] = []
    batch_result = await judge.judge_identity_batch(
        "goal", cand_text, [cand.text for cand in candidates])
    if not batch_result.ok:
        if on_unavailable == "raise":
            raise SemanticJudgmentUnavailable(
                f"goal identity judgment unavailable ({batch_result.reason}); {len(candidates)} candidate(s) unresolved",
                attempts=batch_result.attempts)
        return await _finish("judge_unavailable", None, relations=relations,
                             detail={"reason": batch_result.reason,
                                     "unjudged": [c.id for c in candidates]})
    verdicts = batch_result.value
    if not isinstance(verdicts, list) or len(verdicts) != len(candidates):
        reason = "identity batch returned an invalid verdict count"
        if on_unavailable == "raise":
            raise SemanticJudgmentUnavailable(
                f"goal identity judgment unavailable ({reason}); {len(candidates)} candidate(s) unresolved",
                attempts=batch_result.attempts)
        return await _finish("judge_unavailable", None, relations=relations,
                             detail={"reason": reason, "unjudged": [c.id for c in candidates]})
    for cand, verdict in zip(candidates, verdicts):
        cand.relation, cand.confidence = verdict["relation"], verdict["confidence"]
        if verdict["relation"] == "same":
            if verdict["confidence"] >= same_min_confidence:
                return await _finish("same", cand.id, provider=batch_result.provider, model=batch_result.model,
                                     action="reuse", relations=relations)
            cand.relation = "related"
        if cand.relation in ("specializes", "generalizes", "related"):
            relations.append(cand)
    if not relations:
        final = "distinct"
    else:
        first = relations[0].relation
        final = {"specializes": "narrower", "generalizes": "broader"}.get(first, "related")
    return await _finish(final, None, provider=batch_result.provider, model=batch_result.model,
                         relations=relations, detail={"judged": len(candidates)})


async def propose_goal_relations(
    pool: asyncpg.Pool, new_goal_id: str, relations: list[Candidate], *, decision_id: Optional[str],
    provenance: str = "identity_resolution",
) -> int:
    """Store hierarchy edges implied by judged relations. Best-effort and
    OPTIONAL: hierarchy is never required for ingestion or retrieval, so a
    failure here is logged, not raised. Status stays 'proposed'."""
    n = 0
    for c in relations:
        if c.relation not in ("specializes", "generalizes"):
            continue
        specific, abstract = (new_goal_id, c.id) if c.relation == "specializes" else (c.id, new_goal_id)
        try:
            await pool.execute(
                "INSERT INTO goal_relations (specific_goal_id, abstract_goal_id, relation_type, status, confidence, "
                "provenance, decision_id) VALUES ($1::uuid, $2::uuid, 'SPECIALIZES', 'proposed', $3, $4, $5::uuid) "
                "ON CONFLICT DO NOTHING", specific, abstract, c.confidence, provenance, decision_id)
            n += 1
        except Exception:  # noqa: BLE001 -- hierarchy is optional; never blocks ingestion
            log.warning("goal_relation %s -> %s not stored", specific, abstract, exc_info=True)
    return n


# ---------------------------------------------------------- reconciliation

RECONCILE_LOCK = "reconcile_goals"


async def relink_procedures_of_merged_goals(pool: asyncpg.Pool) -> int:
    """Repair Procedures that a concurrent writer linked to a goal just before it was
    merged (the write-time trigger cannot see an uncommitted merge). Idempotent."""
    res = await pool.execute(
        "UPDATE procedures p SET achieves_goal_id = g.merged_into_id FROM goals g "
        "WHERE p.achieves_goal_id = g.id AND g.status = 'merged' AND g.merged_into_id IS NOT NULL")
    return int(res.split()[-1])


_RELATION_COLUMNS = (
    "relation_type, status, confidence, provenance, decision_id, decision_metadata, "
    "decided_by, decided_at, scope_type, scope_entity_id, tenant_id"
)


async def _move_goal_relations(conn: asyncpg.Connection, loser_id: str, survivor_id: str) -> None:
    """Re-point the loser's hierarchy edges at the survivor inside the caller's
    transaction. Decision and scope columns travel with each edge (migration
    113 requires them on accepted/rejected rows). An accepted edge that would
    close a cycle once re-pointed is kept as a non-routing 'proposed' edge
    for review instead of aborting the whole merge."""
    rows = await conn.fetch(
        f"SELECT specific_goal_id::text AS s, abstract_goal_id::text AS a, {_RELATION_COLUMNS} "
        "FROM goal_relations WHERE specific_goal_id = $1::uuid OR abstract_goal_id = $1::uuid",
        loser_id,
    )
    for row in rows:
        specific = survivor_id if row["s"] == loser_id else row["s"]
        abstract = survivor_id if row["a"] == loser_id else row["a"]
        if specific == abstract:
            continue
        values = [row[c.strip()] for c in _RELATION_COLUMNS.split(",")]
        insert = (
            f"INSERT INTO goal_relations (specific_goal_id, abstract_goal_id, {_RELATION_COLUMNS}) "
            "VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7::uuid, $8::jsonb, $9, $10, $11, $12, $13::uuid) "
            "ON CONFLICT (specific_goal_id, abstract_goal_id, relation_type) DO NOTHING"
        )
        try:
            async with conn.transaction():
                await conn.execute(insert, specific, abstract, *values)
        except asyncpg.CheckViolationError:
            if row["status"] != "accepted":
                raise
            metadata = dict(row["decision_metadata"] or {})
            metadata["demoted_on_merge"] = {"loser_goal_id": loser_id, "reason": "would create an accepted cycle"}
            values[1] = "proposed"
            values[5] = metadata
            await conn.execute(insert, specific, abstract, *values)
    await conn.execute(
        "DELETE FROM goal_relations WHERE specific_goal_id = $1::uuid OR abstract_goal_id = $1::uuid", loser_id)


async def merge_goal(pool: asyncpg.Pool, loser_id: str, survivor_id: str, *, decision_id: Optional[str] = None) -> dict[str, int]:
    """Merge ``loser`` into ``survivor`` atomically: every Procedure
    that pointed at the loser now points at the survivor, hierarchy edges move,
    the loser's names become aliases, and the loser row becomes status='merged'
    (kept for audit; never deleted). Idempotent."""
    from app.services.shards import HOME_SHARD, list_shards, multi_shard, pools_for

    if await multi_shard(pool):
        return await _merge_goal_sharded(pool, loser_id, survivor_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"goal-merge:{loser_id}")
            state = await conn.fetchval("SELECT status FROM goals WHERE id = $1::uuid FOR UPDATE", loser_id)
            if state is None or state == "merged":
                return {"procedures": 0, "already_merged": 1}
            procs = int((await conn.execute(
                "UPDATE procedures SET achieves_goal_id = $2::uuid WHERE achieves_goal_id = $1::uuid", loser_id, survivor_id)).split()[-1])
            await _move_goal_relations(conn, loser_id, survivor_id)
            await conn.execute(
                "UPDATE goals g SET aliases = (SELECT ARRAY(SELECT DISTINCT a FROM unnest(g.aliases || l.aliases || ARRAY[l.canonical_name]) a "
                "WHERE a <> g.canonical_name)) FROM goals l WHERE g.id = $2::uuid AND l.id = $1::uuid", loser_id, survivor_id)
            await conn.execute(
                "UPDATE goals SET status = 'merged', merged_into_id = $2::uuid, reconciled_at = now() WHERE id = $1::uuid",
                loser_id, survivor_id)
            return {"procedures": procs, "already_merged": 0}


async def reconcile_goals(
    pool: asyncpg.Pool, *, embedder: Any = None, judge: Optional[SemanticJudge] = None, window_minutes: Optional[float] = 30.0,
    batch: int = 100, same_min_confidence: float = SAME_MIN_CONFIDENCE, lock_timeout_s: float = 120.0,
) -> dict[str, Any]:
    """Second identity pass for goals created concurrently (see migration 95).

    For every unreconciled goal G: judge G against goals created within
    ``window_minutes`` of G (``None`` = the whole corpus, for a legacy sweep).
    If the model says *same*, the goal with the LARGER id (newer uuid7) is merged
    into the smaller one -- a deterministic tie-break, so concurrent sweeps and
    both directions of a race converge on the same survivor. A judge outage
    leaves G unreconciled (retried next sweep); nothing is guessed. Single-flight
    across workers via a session advisory lock."""
    judge = judge if judge is not None else default_judge()
    out = {"checked": 0, "merged": 0, "deferred": 0, "skipped": False, "relinked": 0}
    async with pool.acquire() as lock:
        # BLOCK (bounded) instead of skipping: a sweep that started earlier works from an
        # older snapshot, so goals created after it must be picked up by the next sweep,
        # not silently left unreconciled because two workers finished at the same moment.
        try:
            await asyncio.wait_for(lock.execute("SELECT pg_advisory_lock(hashtext($1))", RECONCILE_LOCK), timeout=lock_timeout_s)
        except asyncio.TimeoutError:
            out["skipped"] = True
            return out
        try:
            from app.services.shards import HOME_SHARD, list_shards, multi_shard, pools_for

            sources: list[tuple[Any, Any]] = []      # (row, pool that owns it)
            shard_list = [s.shard_id for s in await list_shards(pool)] if await multi_shard(pool) else [HOME_SHARD]
            for sid in shard_list:
                try:
                    spool = pool if sid == HOME_SHARD else await pools_for(pool).get(sid)
                    for r in await spool.fetch(
                            "SELECT id::text AS id, canonical_name, description, scope_type, scope_entity_id, t_created, "
                            "embedding::text AS emb, embedding_model_id FROM goals "
                            "WHERE reconciled_at IS NULL AND status <> 'merged' AND t_invalid IS NULL ORDER BY id LIMIT $1", batch):
                        sources.append((r, spool))
                except Exception:  # noqa: BLE001 -- unreachable shard: its goals stay unreconciled until it is back
                    out["deferred"] += 1
            sources.sort(key=lambda t: t[0]["id"])
            for g, gpool in sources[:batch]:
                out["checked"] += 1
                if await gpool.fetchval("SELECT status FROM goals WHERE id = $1::uuid", g["id"]) == "merged":
                    continue
                emb = [float(x) for x in g["emb"].strip("[]").split(",")] if g["emb"] else None
                text = _goal_text({"canonical_name": g["canonical_name"], "description": g["description"]})
                cands, n_fts, n_vec = await generate_goal_candidates(
                    pool, text, scope_type=g["scope_type"] or "global", scope_entity_id=g["scope_entity_id"], embedding=emb,
                    embedding_model=g["embedding_model_id"], exclude_id=g["id"],
                    created_within=(g["t_created"], window_minutes) if window_minutes else None)
                unavailable = False
                if cands:
                    res = await judge.judge_identity_batch(
                        "goal", text, [cand.text for cand in cands])
                    if not res.ok:
                        unavailable = True
                    elif not isinstance(res.value, list) or len(res.value) != len(cands):
                        unavailable = True
                    else:
                        for cand, verdict in zip(cands, res.value):
                            cand.relation, cand.confidence = verdict["relation"], verdict["confidence"]
                            if cand.relation == "same":
                                if cand.confidence >= same_min_confidence:
                                    survivor, loser = (cand.id, g["id"]) if cand.id < g["id"] else (g["id"], cand.id)
                                    decision_row = await record_decision(
                                        pool, object_type="goal", candidate_text=text, scope_type=g["scope_type"],
                                        scope_entity_id=g["scope_entity_id"], decision="same", resolved_id=survivor,
                                        candidates=cands, judge=judge, provider=res.provider, model=res.model,
                                        fts_n=n_fts, vec_n=n_vec, job_id=None,
                                        idempotency_key=f"reconcile:{loser}:{survivor}",
                                        detail={"reconcile": True, "merged_loser": loser})
                                    decision_id = _row_value(decision_row, "id", decision_row)
                                    await merge_goal(pool, loser, survivor, decision_id=decision_id)
                                    out["merged"] += 1
                                    break
                                cand.relation = "related"
                            if cand.relation in ("specializes", "generalizes"):
                                out["relations"] = out.get("relations", 0) + await propose_goal_relations(
                                    pool, g["id"], [cand], decision_id=None, provenance="goal_reconciliation")
                if unavailable:
                    out["deferred"] += 1
                    continue
                await gpool.execute("UPDATE goals SET reconciled_at = now() WHERE id = $1::uuid AND reconciled_at IS NULL", g["id"])
        finally:
            await lock.execute("SELECT pg_advisory_unlock(hashtext($1))", RECONCILE_LOCK)
    out["relinked"] = await relink_procedures_of_merged_goals(pool)
    return out


async def _merge_goal_sharded(pool: asyncpg.Pool, loser_id: str, survivor_id: str) -> dict[str, int]:
    """merge_goal when more than one shard exists: procedures that achieve the loser may live on
    ANY shard, the loser row lives on its home shard, control tables (relations, names) are here.
    Each step is idempotent, so a crash mid-way is repaired by simply running the merge again."""
    from app.services.shards import HOME_SHARD, list_shards, pools_for

    sp = pools_for(pool)
    loser_shard = await pool.fetchval("SELECT home_shard_id FROM object_routes WHERE object_type='goal' AND object_id=$1::uuid", loser_id) or HOME_SHARD
    lpool = pool if loser_shard == HOME_SHARD else await sp.get(loser_shard)
    state = await lpool.fetchval("SELECT status FROM goals WHERE id = $1::uuid", loser_id)
    if state is None or state == "merged":
        return {"procedures": 0, "already_merged": 1}
    moved = 0
    for shard in await list_shards(pool):
        try:
            spool = pool if shard.shard_id == HOME_SHARD else await sp.get(shard.shard_id)
        except Exception:  # noqa: BLE001 -- an unreachable shard: procedures there are repaired by the relink sweep
            continue
        moved += int((await spool.execute(
            "UPDATE procedures SET achieves_goal_id = $2::uuid WHERE achieves_goal_id = $1::uuid", loser_id, survivor_id)).split()[-1])
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _move_goal_relations(conn, loser_id, survivor_id)
    lrow = await lpool.fetchrow("SELECT canonical_name, aliases FROM goals WHERE id = $1::uuid", loser_id)
    survivor_shard = await pool.fetchval("SELECT home_shard_id FROM object_routes WHERE object_type='goal' AND object_id=$1::uuid", survivor_id) or HOME_SHARD
    surv_pool = pool if survivor_shard == HOME_SHARD else await sp.get(survivor_shard)
    await surv_pool.execute(
        "UPDATE goals g SET aliases = (SELECT ARRAY(SELECT DISTINCT a FROM unnest(g.aliases || $2::text[]) a WHERE a <> g.canonical_name)) "
        "WHERE g.id = $1::uuid", survivor_id, list(lrow["aliases"] or []) + [lrow["canonical_name"]])
    await lpool.execute("UPDATE goals SET status = 'merged', merged_into_id = $2::uuid, reconciled_at = now() WHERE id = $1::uuid", loser_id, survivor_id)
    await pool.execute("DELETE FROM goal_names WHERE goal_id = $1::uuid", loser_id)
    from app.services.search_projection import enqueue
    for oid in (loser_id, survivor_id):
        await enqueue(pool, "goal", oid)
    return {"procedures": moved, "already_merged": 0}
