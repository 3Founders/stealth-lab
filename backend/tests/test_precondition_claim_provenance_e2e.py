"""
Live-database proving test for task 31 (author-time precondition
provenance): a precondition entry's optional `claim_id` (derive.py's new
`precondition_with_claim` helper shape) must NARROW applicability.py's
check to that SPECIFIC claim -- not just be an ignored hint -- while a
claim_id-less precondition must keep behaving exactly as it did before
this change (subject-based, "any live claim with the right predicate/
object satisfies it").

Real claims written via claims.py's capture_claim (subject/predicate/
object kwargs, the same real write path derive_preconditions' own
projection reads back through project_state()), checked through the
real check_hard_constraints() cascade -- app/services/applicability.py's
precondition loop -- against a real Postgres knowledge_nodes table, no
mocking of the query layer.

Same pattern as every other `*_e2e.py` file: requires a real
DATABASE_URL, skips (not fails) without one (see test_claim_relations_e2e.py).
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.db.session import create_pool
from app.services.applicability import check_hard_constraints
from app.services.claims import capture_claim

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "precond-claim-e2e"


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.0] * 1024


async def _cleanup(pool) -> None:
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


async def _claim(pool, task_name: str, *, subject: str, predicate: str, object: str) -> str:  # noqa: A002
    claim_id = await capture_claim(
        pool,
        statement=f"{PREFIX} {subject} {predicate} {object}",
        task_ids=[f"skill_{task_name}"],
        subject=subject, predicate=predicate, object=object,
        embedder=FakeEmbedder(),
    )
    assert claim_id
    return claim_id


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


def test_claim_id_less_precondition_grounds_via_subject_unchanged():
    """PART 2 backward-compatibility proof, against a real claim: a
    precondition with no claim_id is satisfied by the real live claim
    matching subject/predicate/object, and disqualified once the object
    it names no longer matches -- exactly the pre-existing behavior."""
    async def _body():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task1")
            subject = f"{PREFIX}:project:1"
            await _claim(pool, task, subject=subject, predicate="language", object="python")

            matching = _procedure(preconditions=[
                {"subject": subject, "predicate": "language", "object": "python"},
            ])
            result = await check_hard_constraints(pool, matching, as_of=datetime.now(timezone.utc))
            assert result.applicable, result.failed_constraints

            mismatched = _procedure(preconditions=[
                {"subject": subject, "predicate": "language", "object": "ruby"},
            ])
            result2 = await check_hard_constraints(pool, mismatched, as_of=datetime.now(timezone.utc))
            assert not result2.applicable
            assert result2.failed_constraints[0].startswith("precondition:")
        finally:
            await _cleanup(pool)
            await pool.close()

    _run(_body())


def test_claim_id_precondition_narrows_to_the_specific_real_claim():
    """PART 2 narrowing proof, against real claims: two live claims share
    the same subject; one (decoy) has the predicate/object the
    precondition wants, the other (the one actually named by claim_id)
    does not. A claim_id-less precondition would be satisfied by the
    decoy -- a claim_id-bearing one must NOT be, because it narrows to
    the specific claim, not "any claim with this subject"."""
    async def _body():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task2")
            subject = f"{PREFIX}:project:2"

            decoy_claim_id = await _claim(
                pool, task, subject=subject, predicate="language", object="python",
            )
            specific_claim_id = await _claim(
                pool, task, subject=subject, predicate="language", object="ruby",
            )

            # Sanity: the claim_id-LESS check is satisfied by the decoy.
            claim_id_less = _procedure(preconditions=[
                {"subject": subject, "predicate": "language", "object": "python"},
            ])
            baseline = await check_hard_constraints(pool, claim_id_less, as_of=datetime.now(timezone.utc))
            assert baseline.applicable, "sanity check: decoy claim should satisfy the old subject-only path"

            # The real proof: naming the SPECIFIC (mismatched) claim
            # disqualifies, even though the decoy would have satisfied it.
            narrowed = _procedure(preconditions=[
                {
                    "subject": subject, "predicate": "language", "object": "python",
                    "claim_id": specific_claim_id,
                },
            ])
            result = await check_hard_constraints(pool, narrowed, as_of=datetime.now(timezone.utc))
            assert not result.applicable, (
                "claim_id must narrow to the SPECIFIC claim, not fall back to any matching subject"
            )
            assert result.failed_constraints[0].startswith("precondition:")

            # And naming the decoy's OWN id (which really does match) is applicable.
            narrowed_match = _procedure(preconditions=[
                {
                    "subject": subject, "predicate": "language", "object": "python",
                    "claim_id": decoy_claim_id,
                },
            ])
            result2 = await check_hard_constraints(pool, narrowed_match, as_of=datetime.now(timezone.utc))
            assert result2.applicable, result2.failed_constraints
        finally:
            await _cleanup(pool)
            await pool.close()

    _run(_body())


def test_claim_id_precondition_fails_when_the_named_claim_does_not_exist():
    async def _body():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            fake_claim_id = str(uuid4())
            procedure = _procedure(preconditions=[
                {
                    "subject": f"{PREFIX}:project:none", "predicate": "language", "object": "python",
                    "claim_id": fake_claim_id,
                },
            ])
            result = await check_hard_constraints(pool, procedure, as_of=datetime.now(timezone.utc))
            assert not result.applicable
        finally:
            await _cleanup(pool)
            await pool.close()

    _run(_body())
