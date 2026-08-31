"""
Real, live-database proving test for the final wiring step: does calling
`claims.relate_claims()` itself -- not `claim_impact.propagate_claim_change()`
directly -- actually cause a dependent procedure to be marked stale?

This is the orchestrator's own integration commit connecting two
independently-built, independently-tested pieces (`claims.py`'s
relation-writing functions and `claim_impact.py`'s propagation
primitive) that were deliberately left unconnected by the parallel
agents that built them, to avoid a real merge conflict. This file is the
proof they are actually connected now, not just individually correct.

Same pattern as every other `*_e2e.py` file: requires a real
DATABASE_URL, skips (not fails) without one.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from app.db.session import create_pool
from app.services.claims import capture_claim, relate_claims
from app.services.procedure_extraction.derive import precondition_with_claim
from app.services.procedures import capture_procedure, get_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "claim-relate-impact-wiring-e2e"


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
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{PREFIX}%")


async def _task_node(pool, name: str) -> str:
    await pool.fetchrow(
        "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2)", name, f"skill_{name}",
    )
    return name


def test_contradicting_a_claim_marks_the_dependent_procedure_stale():
    """The real end-to-end proof: capture a claim, capture a procedure
    whose precondition names that exact claim, then CONTRADICT the claim
    via the ordinary relate_claims() call a caller would actually make --
    no direct call to claim_impact anywhere in this test. The dependent
    procedure's own staleness column must flip for real."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task")

            target_claim_id = await capture_claim(
                pool, statement=f"{PREFIX} pandas is at 2.0",
                task_ids=[f"skill_{task}"], subject="pandas", predicate="version",
                object="2.0", embedder=FakeEmbedder(),
            )
            assert target_claim_id

            procedure = await capture_procedure(
                pool, name=f"{PREFIX}-dependent", goal="do work that needs pandas 2.0",
                preconditions=[
                    precondition_with_claim(
                        "pandas", "version", "2.0", claim_id=target_claim_id,
                    ),
                ],
                provenance="system_pending_review", scope_type="global",
            )

            before = await get_procedure(pool, procedure["id"])
            assert before["staleness"] == "fresh"

            contradicting_claim_id = await capture_claim(
                pool, statement=f"{PREFIX} pandas is actually at 1.5",
                task_ids=[f"skill_{task}"], subject="pandas", predicate="version",
                object="1.5", embedder=FakeEmbedder(),
            )
            assert contradicting_claim_id

            marked = await relate_claims(
                pool, from_claim_id=contradicting_claim_id, to_claim_id=target_claim_id,
                relation="CONTRADICTS",
            )
            assert procedure["id"] in marked, (
                "relate_claims() itself must return the dependent procedure's "
                "row id as marked -- proving propagation actually ran, not "
                "just that it's callable in isolation"
            )

            after = await get_procedure(pool, procedure["id"])
            assert after["staleness"] == "stale", (
                "contradicting a claim a procedure's precondition specifically "
                "depends on must mark that procedure stale -- the real, "
                "end-to-end impact-propagation wiring this test proves"
            )
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_relating_claims_with_propagate_false_skips_impact():
    """The opt-out must be real: propagate=False must leave a genuinely
    dependent procedure untouched."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task-optout")

            target_claim_id = await capture_claim(
                pool, statement=f"{PREFIX} optout claim",
                task_ids=[f"skill_{task}"], subject="optout-subject", predicate="version",
                object="2.0", embedder=FakeEmbedder(),
            )
            procedure = await capture_procedure(
                pool, name=f"{PREFIX}-optout-dependent", goal="g",
                preconditions=[
                    precondition_with_claim(
                        "optout-subject", "version", "2.0", claim_id=target_claim_id,
                    ),
                ],
                provenance="system_pending_review", scope_type="global",
            )
            contradicting_claim_id = await capture_claim(
                pool, statement=f"{PREFIX} optout contradiction",
                task_ids=[f"skill_{task}"], subject="optout-subject", predicate="version",
                object="1.0", embedder=FakeEmbedder(),
            )

            marked = await relate_claims(
                pool, from_claim_id=contradicting_claim_id, to_claim_id=target_claim_id,
                relation="CONTRADICTS", propagate=False,
            )
            assert marked == []

            after = await get_procedure(pool, procedure["id"])
            assert after["staleness"] == "fresh", (
                "propagate=False must be a real opt-out, not decorative"
            )
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
