"""Spec section 14: a single connected test walking the FULL provenance
chain -- precondition -> claim -> evidence -> source -- rather than the
two pairwise links the pre-existing suite already proves separately:

  - test_precondition_claim_provenance_e2e.py: precondition.claim_id
    narrows applicability.py's check to one SPECIFIC claim, not just
    "any live claim with the right subject/predicate/object."
  - test_claim_evidence_e2e.py: record_claim_evidence -> get_claim_evidence
    round-trips a real evidence row keyed by claim_id.

Neither test on its own would catch provenance silently dropping ACROSS
the join -- e.g. a precondition whose claim_id resolves to the wrong
claim, or a claim whose evidence exists but doesn't actually trace back
to the context (context_key/created_by -- this schema's source pointer;
evidence has no separate episode/observation foreign key, see
claim_evidence.py's docstring) it claims to. This test builds one real
procedure with a claim-backed precondition, that claim with real
evidence recorded against it (including a context_key standing in for
the originating episode/observation -- the actual "source" pointer this
schema carries), then walks the chain end-to-end through the REAL
production functions at every hop:

  precondition.claim_id
    -> check_hard_constraints() (applicability.py) resolves it against
       the real claim, not a re-derived guess
    -> get_claim_evidence() (claim_evidence.py) resolves the claim's
       real evidence rows
    -> the evidence row's own context_key/created_by resolve to the
       real source that was recorded

If any hop silently drops or mismatches, this test fails -- it does not
independently re-derive the expected answer and compare shapes, it asks
the real query path for the answer and checks the ANSWER is the one
this test itself planted.

Same convention as every other *_e2e.py file: requires a real
DATABASE_URL, skips (not fails) without one.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.db.session import create_pool
from app.services.applicability import check_hard_constraints
from app.services.claim_evidence import get_claim_evidence, record_claim_evidence
from app.services.claims import capture_claim

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "provenance-chain-e2e"


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.0] * 1024


async def _cleanup(pool) -> None:
    await pool.execute(
        "UPDATE evidence SET t_invalid = now() WHERE target_type = 'claim' "
        "AND t_invalid IS NULL AND target_id IN "
        "(SELECT id FROM knowledge_nodes WHERE name LIKE $1)", f"{PREFIX}%",
    )
    await pool.execute(
        "DELETE FROM edges WHERE source_id IN "
        "(SELECT id FROM knowledge_nodes WHERE name LIKE $1)", f"{PREFIX}%",
    )
    await pool.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM task_nodes WHERE name LIKE $1", f"{PREFIX}%")


async def _task_node(pool, name: str) -> str:
    await pool.fetchrow(
        "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2)",
        name, f"skill_{name}",
    )
    return name


def _procedure(*, preconditions: list) -> dict:
    return {
        "id": str(uuid4()),
        "t_invalid": None,
        "staleness": "fresh",
        "availability": "active",
        "verification_state": "verified",
        "approval_status": "approved",
        "scope": {},
        "exclusions": [],
        "preconditions": preconditions,
        "invariants": [],
    }


def _run(coro):
    return asyncio.run(coro)


def test_precondition_to_claim_to_evidence_to_source_survives_intact():
    async def _body():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task")
            subject = f"{PREFIX}:project:1"

            # Hop 1: a real claim, written the real way.
            claim_id = await capture_claim(
                pool,
                statement=f"{PREFIX} {subject} language python",
                task_ids=[f"skill_{PREFIX}-task"],
                subject=subject, predicate="language", object="python",
                embedder=FakeEmbedder(),
            )
            assert claim_id

            # Hop 2: real evidence recorded against that claim, carrying a
            # context_key that stands in for the originating episode/
            # observation -- this schema's actual source pointer (see
            # claim_evidence.py's docstring: no separate episode/observation
            # foreign key exists on the evidence table).
            source_context_key = f"{PREFIX}-episode-{uuid4()}"
            evidence_id = await record_claim_evidence(
                pool,
                claim_id=claim_id,
                evidence_type="execution_result",
                outcome_status="success",
                success_criteria={"predicate": "python 3.x observed in the real environment"},
                context_key=source_context_key,
                created_by=f"{PREFIX}-extractor",
            )
            assert evidence_id

            # Hop 0 (the top of the chain): a real procedure whose
            # precondition NAMES this specific claim via claim_id -- not
            # just a subject-based guess.
            procedure = _procedure(preconditions=[
                {
                    "subject": subject, "predicate": "language", "object": "python",
                    "claim_id": claim_id,
                },
            ])

            # Walk the chain forward through the REAL production functions,
            # never re-deriving the expected answer independently.
            applicability_result = await check_hard_constraints(
                pool, procedure, as_of=datetime.now(timezone.utc)
            )
            assert applicability_result.applicable, (
                f"precondition->claim hop broke: {applicability_result.failed_constraints}"
            )

            evidence_rows = await get_claim_evidence(pool, claim_id)
            assert len(evidence_rows) == 1, "claim->evidence hop lost or duplicated the recorded row"
            evidence_row = evidence_rows[0]
            assert str(evidence_row["id"]) == evidence_id
            assert str(evidence_row["target_id"]) == claim_id, (
                "evidence row exists but no longer points back at the claim it was recorded against"
            )

            # evidence->source hop: the context_key/created_by planted at
            # write time must still be the ones read back -- this is the
            # concrete assertion that would fail if provenance silently
            # dropped during persistence or retrieval.
            assert evidence_row["context_key"] == source_context_key, (
                "evidence's source pointer (context_key) does not match what was recorded -- "
                "provenance dropped somewhere between write and read"
            )
            assert evidence_row["created_by"] == f"{PREFIX}-extractor"
            assert evidence_row["outcome_status"] == "success"

            # Negative control: breaking any single hop must break the
            # chain, not just this one assertion -- proves the test is
            # actually sensitive to the thing it claims to test. Here: a
            # claim_id pointing at a real but WRONG claim (mismatched
            # object) must disqualify applicability even though evidence
            # for the correct claim still exists untouched.
            wrong_claim_id = await capture_claim(
                pool,
                statement=f"{PREFIX} {subject} language ruby (decoy)",
                task_ids=[f"skill_{PREFIX}-task"],
                subject=subject, predicate="language", object="ruby",
                embedder=FakeEmbedder(),
            )
            broken_procedure = _procedure(preconditions=[
                {
                    "subject": subject, "predicate": "language", "object": "python",
                    "claim_id": wrong_claim_id,
                },
            ])
            broken_result = await check_hard_constraints(
                pool, broken_procedure, as_of=datetime.now(timezone.utc)
            )
            assert not broken_result.applicable, (
                "negative control failed: a claim_id pointing at a claim with the WRONG "
                "object was still treated as satisfied -- the chain is not actually being checked"
            )
        finally:
            await _cleanup(pool)
            await pool.close()

    _run(_body())
