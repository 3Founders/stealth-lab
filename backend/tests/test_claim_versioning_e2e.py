"""
Live-database proving tests for claim version chains
(app/services/claims.py::supersede_claim/get_claim_version_chain).

Claims had no version-history concept before this: `relate_claims(
relation="SUPERSEDES")` flipped the OLD claim's truth_state to OUT but
the "new" claim was a totally separate, unrelated row, connected only by
the SUPERSEDES edge -- no structural link showing it's part of the same
version chain, unlike `procedures` (`family_id` + `version`,
`supersede_procedure()`).

`supersede_claim()`/`get_claim_version_chain()` give claims the same
concept, living inside `properties` JSONB (`claim_family_id`/
`claim_version`) rather than real columns -- no migration, claims share
`knowledge_nodes` with 6 other virtual node types.

Same pattern as every other `*_e2e.py` file (test_claim_relations_e2e.py
in particular): requires a real DATABASE_URL, skips (not fails) without
one, real Postgres, no mocks, self-cleaning by name prefix.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from app.db.session import create_pool
from app.services.claims import (
    capture_claim,
    get_claim_version_chain,
    relate_claims,
    supersede_claim,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "claim-ver-e2e"


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


async def _claim(pool, statement: str, task_name: str) -> str:
    claim_id = await capture_claim(
        pool, statement=statement, task_ids=[f"skill_{task_name}"],
        embedder=FakeEmbedder(),
    )
    assert claim_id
    return claim_id


def test_supersede_claim_writes_a_real_family_chain_against_postgres():
    """First supersession: family root is the prior claim's own id, new
    claim's claim_version=2, and relate_claims' real SUPERSEDES edge +
    truth_state flip both still fire (reused, not reinvented)."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task")
            old = await _claim(pool, f"{PREFIX} old claim", task)

            new = await supersede_claim(
                pool, prior_claim_id=old, statement=f"{PREFIX} new claim",
                task_ids=[f"skill_{task}"], embedder=FakeEmbedder(),
                reason="real supersession reason",
            )
            assert new is not None
            assert new != old

            new_row = await pool.fetchrow(
                "SELECT properties FROM knowledge_nodes WHERE id = $1::uuid", new,
            )
            assert new_row["properties"]["claim_family_id"] == old
            assert new_row["properties"]["claim_version"] == 2
            assert new_row["properties"]["supersession_reason"] == "real supersession reason"

            old_row = await pool.fetchrow(
                "SELECT properties FROM knowledge_nodes WHERE id = $1::uuid", old,
            )
            assert old_row["properties"]["truth_state"] == "OUT"

            edge = await pool.fetchrow(
                "SELECT edge_type::text, custom_edge_type FROM edges "
                "WHERE source_id = $1::uuid AND target_id = $2::uuid "
                "AND custom_edge_type = 'SUPERSEDES'",
                new, old,
            )
            assert edge is not None
            assert edge["edge_type"] == "SUPERSEDES"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_get_claim_version_chain_walks_a_real_three_version_family():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task2")
            v1 = await _claim(pool, f"{PREFIX} v1", task)
            v2 = await supersede_claim(
                pool, prior_claim_id=v1, statement=f"{PREFIX} v2",
                task_ids=[f"skill_{task}"], embedder=FakeEmbedder(),
            )
            v3 = await supersede_claim(
                pool, prior_claim_id=v2, statement=f"{PREFIX} v3",
                task_ids=[f"skill_{task}"], embedder=FakeEmbedder(),
            )

            for anchor in (v1, v2, v3):
                chain = await get_claim_version_chain(pool, anchor)
                assert [str(r["id"]) for r in chain] == [v1, v2, v3], (
                    f"chain from anchor {anchor} must be the same full ordered chain"
                )
                assert [r["properties"]["statement"] for r in chain] == [
                    f"{PREFIX} v1", f"{PREFIX} v2", f"{PREFIX} v3",
                ]
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_missing_prior_claim_raises_against_real_postgres():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task3")
            with pytest.raises(ValueError):
                await supersede_claim(
                    pool, prior_claim_id="00000000-0000-0000-0000-000000000000",
                    statement=f"{PREFIX} orphan", task_ids=[f"skill_{task}"],
                    embedder=FakeEmbedder(),
                )
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_single_version_claim_chain_is_just_itself_against_real_postgres():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task4")
            only = await _claim(pool, f"{PREFIX} only version", task)

            chain = await get_claim_version_chain(pool, only)
            assert [str(r["id"]) for r in chain] == [only]
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
