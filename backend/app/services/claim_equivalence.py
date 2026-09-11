"""
Detected (never auto-applied) Claim relations -- mechanism-9 / A6
"EquivalentClaims", per the research writeup at
`.scratch/research/semantic-dedup-procedures-implementations-claims.md`.

THE PROBLEM THIS CLOSES
    `claims.py::relate_claims` (SUPERSEDES/CONTRADICTS) and `link_claims`
    (the richer GENERAL_RELATIONS vocabulary) only ever write a relation
    a caller ALREADY knows and asserts explicitly. Nothing detects, from
    text alone, that two differently-worded claims assert the same fact
    (so their evidence/belief keeps splitting across two rows instead of
    accumulating on one) or that two claims conflict.

FOUNDER DIRECTIVE (2026-09-11) -- THE POSTURE THIS MODULE FOLLOWS
    A detected relation must NOT auto-resolve. Store it somewhere durable
    and reviewable (migration 73's `claim_relation_candidates` table);
    the resolution mechanism (who reviews it, how it becomes a real
    `relate_claims`/`link_claims` call) is decided later, deliberately
    out of scope here. This module NEVER calls relate_claims/link_claims
    itself -- it only ever writes a *candidate* row. Promoting a resolved
    candidate into a real claim-graph edge is a separate, future function
    that composes the existing (unmodified) relate_claims/link_claims,
    once the review mechanism exists.

    `claim_belief.py` is not touched, not imported, not read by anything
    here -- this is upstream of it, same as the research doc's own rule.

DETECTION -- HONEST ABSTENTION, NOT A FABRICATED VERDICT
    `classify_claim_relation` mirrors `skill_ingestion.py::_abstract_capability`'s
    own discipline exactly: no `client` (or any call failure) returns
    `{"relation": "unknown", "confidence": 0.0}` -- an honest "did not
    classify," never a guessed relation. This repo has no NLI model
    integration today (confirmed: no transformers/NLI dependency
    anywhere in requirements.txt); the classifier is LLM-as-judge over
    the SAME OpenAI-compatible `client` interface `_abstract_capability`
    already uses, not a new external dependency. Swapping in a real NLI
    model later only needs a new `classify_claim_relation`-shaped
    function -- the storage/candidate-finding halves below don't change.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import asyncpg

from app.services.access import TenantScope, tenant_transaction
from app.utils.ids import uuid7

log = logging.getLogger(__name__)

RELATIONS = ("equivalent", "contradicts", "related", "unrelated")
STATUSES = ("pending", "resolved", "dismissed")
DETECTOR = "claim_equivalence.classify_claim_relation"
DETECTOR_VERSION = "claim_equivalence@v1"

# Same embedding-space "confidently close" threshold this repo already
# treats as meaningful elsewhere (skill_ingestion.py's NOVELTY_THRESHOLD,
# reused rather than inventing a second number) as the pre-filter floor --
# below this, two claims aren't even worth a classification call.
CANDIDATE_SIMILARITY_FLOOR = 0.75


async def find_candidate_claim_pairs(
    pool: asyncpg.Pool, claim_id: str, *, top_k: int = 5,
    min_similarity: float = CANDIDATE_SIMILARITY_FLOOR,
) -> list[dict]:
    """Pure read: the `top_k` OTHER live claims closest to `claim_id` in
    embedding space, above `min_similarity` cosine. This is the cheap
    pre-filter every technique in the research writeup agrees on
    (SemDeDup, SBERT retrieve-then-rerank, Fellegi-Sunter "blocking") --
    it shortlists candidates; it never itself decides a relation.
    `knowledge_nodes.embedding` is already populated for every claim
    `capture_claim` writes (confirmed: no migration needed)."""
    rows = await pool.fetch(
        """
        SELECT b.id, b.name AS statement,
               1 - (a.embedding <=> b.embedding) AS similarity
        FROM knowledge_nodes a, knowledge_nodes b
        WHERE a.id = $1::uuid AND a.node_type = 'claim' AND a.t_invalid IS NULL
          AND b.node_type = 'claim' AND b.t_invalid IS NULL AND b.id != a.id
          AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
        ORDER BY a.embedding <=> b.embedding ASC
        LIMIT $2
        """,
        claim_id, top_k,
    )
    return [dict(r) for r in rows if r["similarity"] >= min_similarity]


_CLASSIFY_SYSTEM_PROMPT = (
    "You compare two independent factual claims and classify their "
    "relationship. Respond with EXACTLY one JSON object on one line: "
    '{"relation": "equivalent"|"contradicts"|"related"|"unrelated", '
    '"confidence": <0.0-1.0>}. '
    "equivalent = both claims assert the SAME fact, just worded "
    "differently (bidirectional paraphrase). contradicts = the claims "
    "assert INCOMPATIBLE facts (both cannot be true at once). related = "
    "on a similar topic but neither equivalent nor contradictory "
    "(e.g. one generalizes/specializes the other). unrelated = no "
    "meaningful relationship. Never invent facts not present in either "
    "claim; classify only the relationship between the two texts given."
)


def classify_claim_relation(
    statement_a: str, statement_b: str, *, client: Any = None,
    model: str = "gemma-4-31B-it", temperature: float = 0.0,
) -> dict:
    """One focused model call classifying the relation, or an honest
    abstention. Returns {"relation": "unknown", "confidence": 0.0} --
    NEVER a fabricated relation -- on any of: no client, an API error, a
    response that isn't valid JSON, or a `relation` value outside
    `RELATIONS`. Pure otherwise (no DB)."""
    abstain = {"relation": "unknown", "confidence": 0.0}
    if client is None:
        return abstain
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _CLASSIFY_SYSTEM_PROMPT},
                {"role": "user", "content": f'Claim A: "{statement_a}"\nClaim B: "{statement_b}"'},
            ],
            temperature=temperature,
            max_tokens=60,
        )
        text = (response.choices[0].message.content or "").strip()
        parsed = json.loads(text)
        relation = parsed.get("relation")
        confidence = float(parsed.get("confidence", 0.0))
        if relation not in RELATIONS:
            return abstain
        return {"relation": relation, "confidence": max(0.0, min(1.0, confidence))}
    except Exception:  # noqa: BLE001 -- a model call's own failure degrades to abstention, never a fabricated relation
        log.warning("claim_equivalence: classification call failed, abstaining", exc_info=True)
        return abstain


async def record_claim_relation_candidate(
    pool: asyncpg.Pool, *, claim_a_id: str, claim_b_id: str, relation: str,
    confidence: Optional[float] = None, detector: str = DETECTOR,
    detector_version: str = DETECTOR_VERSION, created_by: str,
) -> str:
    """Write ONE candidate row. Idempotent: the same (normalized) pair +
    detector re-detected is a no-op (ON CONFLICT DO NOTHING), returns the
    existing row's id. Pair order is normalized (smaller uuid first) so
    (a,b) and (b,a) are always the same candidate -- matches the table's
    own `claim_a_id < claim_b_id` CHECK. NEVER calls relate_claims/
    link_claims -- see this module's own docstring."""
    if relation not in RELATIONS:
        raise ValueError(f"relation must be one of {RELATIONS}, got {relation!r}")
    a, b = sorted((str(claim_a_id), str(claim_b_id)))
    if a == b:
        raise ValueError("claim_a_id and claim_b_id must be different claims")

    candidate_id = uuid7()
    scope = TenantScope.commons()
    async with tenant_transaction(pool, scope) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO claim_relation_candidates (
                id, claim_a_id, claim_b_id, relation, confidence,
                detector, detector_version, created_by
            ) VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8)
            ON CONFLICT (claim_a_id, claim_b_id, detector) DO NOTHING
            RETURNING id
            """,
            candidate_id, a, b, relation, confidence, detector, detector_version, created_by,
        )
        if row is not None:
            return str(row["id"])

    existing = await pool.fetchval(
        "SELECT id FROM claim_relation_candidates WHERE claim_a_id = $1::uuid "
        "AND claim_b_id = $2::uuid AND detector = $3",
        a, b, detector,
    )
    return str(existing)


async def get_pending_claim_relation_candidates(pool: asyncpg.Pool, *, limit: int = 100) -> list[dict]:
    """The review queue: every `status='pending'` candidate, oldest first,
    joined to each claim's current statement (a claim's text can change
    across bitemporal versions; this always shows the LIVE statement, not
    a stale copy)."""
    rows = await pool.fetch(
        """
        SELECT c.id, c.claim_a_id, c.claim_b_id, c.relation, c.confidence,
               c.detector, c.detector_version, c.created_by, c.t_created,
               a.name AS claim_a_statement, b.name AS claim_b_statement
        FROM claim_relation_candidates c
        JOIN knowledge_nodes a ON a.id = c.claim_a_id
        JOIN knowledge_nodes b ON b.id = c.claim_b_id
        WHERE c.status = 'pending'
        ORDER BY c.t_created ASC
        LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]


async def resolve_claim_relation_candidate(
    pool: asyncpg.Pool, *, candidate_id: str, resolution: str, resolved_by: str,
    status: str = "resolved",
) -> None:
    """Record that a human/review mechanism looked at this candidate.
    `resolution` is a free-text note (e.g. "confirmed_contradicts",
    "false_positive: different scope"), not a controlled vocabulary --
    the actual resolution MECHANISM is explicitly deferred (founder
    directive), so this function only marks the candidate reviewed. It
    does NOT call relate_claims/link_claims -- promoting a resolved
    candidate into a real claim-graph edge is a separate, future step
    once that mechanism is decided."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    scope = TenantScope.commons()
    async with tenant_transaction(pool, scope) as conn:
        await conn.execute(
            "UPDATE claim_relation_candidates SET status = $2, resolution = $3, "
            "resolved_by = $4, resolved_at = now() WHERE id = $1::uuid",
            candidate_id, status, resolution, resolved_by,
        )
