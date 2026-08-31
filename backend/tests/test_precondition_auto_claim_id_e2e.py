"""
Live-database proving test for task 37 (auto-populated precondition
provenance): derive_preconditions() must actually WRITE a real claim_id
onto a derived precondition when exactly one live claim justifies it,
and must NOT guess one when multiple live claims share the exact same
(subject, predicate, object) triple -- the producing side of the exact
feature test_precondition_claim_provenance_e2e.py already proves the
consuming side of (applicability.py honoring precondition.claim_id).

Real claims written via claims.py's capture_claim, a real
ProcedureEvidence built the same way derive_preconditions' own offline
tests build one, and a real project_state() round trip against a live
Postgres knowledge_nodes table -- no mocking of the query layer.

Same pattern as every other `*_e2e.py` file: requires a real
DATABASE_URL, skips (not fails) without one.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest

from app.db.session import create_pool
from app.services.claims import capture_claim
from app.services.procedure_extraction.derive import derive_preconditions
from app.services.procedure_extraction.evidence import ProcedureEvidence

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "precond-auto-claim-e2e"


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


def _evidence(*, project_id: str, started_at: datetime) -> ProcedureEvidence:
    # A test_run observation makes has_test_runner load-bearing (see
    # derive.py's load_bearing_predicates) -- the exact same evidence
    # shape derive.py's own offline tests build.
    return ProcedureEvidence(
        goal_text="run the suite", outcome="success",
        project_id=project_id, started_at=started_at,
        observations=[{"observation_type": "test_run", "properties": {"passed": True}}],
    )


def _run(coro):
    return asyncio.run(coro)


def test_derive_preconditions_writes_a_real_claim_id_on_a_single_match():
    """PART 1: exactly one live claim carries has_test_runner=pytest for
    this project -- the derived precondition must carry that claim's
    real, live id."""
    async def _body():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            project_id = f"{PREFIX}-proj1"
            subject = f"project:{project_id}"
            task = await _task_node(pool, f"{PREFIX}-task1")
            expected_claim_id = await _claim(
                pool, task, subject=subject, predicate="has_test_runner", object="pytest",
            )

            evidence = _evidence(project_id=project_id, started_at=datetime.now(timezone.utc))
            preconditions = await derive_preconditions(pool, evidence)

            assert len(preconditions) == 1
            assert preconditions[0].subject == subject
            assert preconditions[0].predicate == "has_test_runner"
            assert preconditions[0].object == "pytest"
            assert preconditions[0].claim_id == expected_claim_id
            assert preconditions[0].model_dump() == {
                "subject": subject, "predicate": "has_test_runner", "object": "pytest",
                "claim_id": expected_claim_id,
            }
        finally:
            await _cleanup(pool)
            await pool.close()

    _run(_body())


def test_derive_preconditions_does_not_guess_among_real_ambiguous_claims():
    """PART 2: TWO independently-captured live claims assert the exact
    same (subject, predicate, object) triple for this project -- a real,
    possible case. derive_preconditions() must not silently pick one;
    the precondition falls back to the claim_id-less shape, exactly as
    it did before task 37."""
    async def _body():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            project_id = f"{PREFIX}-proj2"
            subject = f"project:{project_id}"
            task = await _task_node(pool, f"{PREFIX}-task2")
            # Two distinct real claim rows, same real triple.
            await _claim(pool, task, subject=subject, predicate="has_test_runner", object="pytest")
            await _claim(pool, task, subject=subject, predicate="has_test_runner", object="pytest")

            evidence = _evidence(project_id=project_id, started_at=datetime.now(timezone.utc))
            preconditions = await derive_preconditions(pool, evidence)

            assert len(preconditions) == 1
            assert preconditions[0].subject == subject
            assert preconditions[0].predicate == "has_test_runner"
            assert preconditions[0].object == "pytest"
            # Ambiguous fallback is a plain Predicate -- no claim_id
            # attribute at all (not claim_id=None) -- the exact
            # pre-existing type.
            assert getattr(preconditions[0], "claim_id", None) is None
            assert preconditions[0].model_dump() == {
                "subject": subject, "predicate": "has_test_runner", "object": "pytest",
            }
            assert "claim_id" not in preconditions[0].model_dump()
        finally:
            await _cleanup(pool)
            await pool.close()

    _run(_body())
