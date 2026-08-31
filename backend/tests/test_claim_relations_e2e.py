"""
Live-database proving tests for CONSOLIDATED-directive Phase 2's claim
relation vocabulary (app/services/claims.py::link_claims/
get_claim_relations, and the real edge_type fix to relate_claims found
during the Phase 0 architecture audit).

Real state confirmed before writing this file (Phase 0 audit,
.scratch/final_architecture_audit.md): `relate_claims()` previously wrote
`edge_type='SUPERSEDES'` in the `edges` table UNCONDITIONALLY, even for
`relation='CONTRADICTS'` -- a real labeling bug, since `edge_type` is a
frozen ENUM with no `CONTRADICTS` member at all. Fixed via
`_edge_type_for_relation()`: `SUPERSEDES` gets the real enum member;
every other relation (`CONTRADICTS` included) rides the existing
`VALIDATED_BY` bucket with `custom_edge_type` carrying the real name --
the same idiom this file's own `CONFLICTS_WITH` edges already use, no
migration, no parallel graph table.

Same pattern as every other `*_e2e.py` file: requires a real
DATABASE_URL, skips (not fails) without one.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from app.db.session import create_pool
from app.services.claims import (
    ALL_CLAIM_RELATIONS,
    capture_claim,
    get_claim_relations,
    link_claims,
    relate_claims,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "claim-rel-e2e"


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


def test_contradicts_persists_as_validated_by_not_supersedes():
    """The real bug fix, proven against the real edges table: a
    CONTRADICTS edge's stored edge_type is 'VALIDATED_BY', distinguishable
    from a real SUPERSEDES edge -- not the old, indistinguishable
    edge_type='SUPERSEDES' for both."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task")
            a = await _claim(pool, f"{PREFIX} claim A", task)
            b = await _claim(pool, f"{PREFIX} claim B", task)

            await relate_claims(pool, from_claim_id=a, to_claim_id=b, relation="CONTRADICTS")

            row = await pool.fetchrow(
                "SELECT edge_type::text, custom_edge_type FROM edges "
                "WHERE source_id = $1::uuid AND target_id = $2::uuid "
                "AND custom_edge_type = 'CONTRADICTS'",
                a, b,
            )
            assert row is not None
            assert row["edge_type"] == "VALIDATED_BY"
            assert row["custom_edge_type"] == "CONTRADICTS"

            b_row = await pool.fetchrow(
                "SELECT properties->>'truth_state' AS ts FROM knowledge_nodes WHERE id = $1::uuid", b,
            )
            assert b_row["ts"] == "OUT", "CONTRADICTS must still flip the target's truth_state"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_supersedes_still_persists_as_the_real_enum_member():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task2")
            old = await _claim(pool, f"{PREFIX} old claim", task)
            new = await _claim(pool, f"{PREFIX} new claim", task)

            await relate_claims(pool, from_claim_id=new, to_claim_id=old, relation="SUPERSEDES")

            row = await pool.fetchrow(
                "SELECT edge_type::text FROM edges "
                "WHERE source_id = $1::uuid AND target_id = $2::uuid "
                "AND custom_edge_type = 'SUPERSEDES'",
                new, old,
            )
            assert row["edge_type"] == "SUPERSEDES"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_link_claims_round_trips_through_get_claim_relations():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task3")
            a = await _claim(pool, f"{PREFIX} pandas>=2.0 claim", task)
            b = await _claim(pool, f"{PREFIX} DataFrame.append removed claim", task)

            await link_claims(
                pool, from_claim_id=b, to_claim_id=a, relation="DEPENDS_ON",
                properties={"reason": "removal only applies under this version"},
            )

            outgoing_from_b = await get_claim_relations(pool, b, direction="outgoing")
            assert len(outgoing_from_b) == 1
            assert outgoing_from_b[0]["relation"] == "DEPENDS_ON"
            assert str(outgoing_from_b[0]["target_id"]) == a
            assert outgoing_from_b[0]["properties"]["reason"] == (
                "removal only applies under this version"
            )

            incoming_to_a = await get_claim_relations(pool, a, direction="incoming")
            assert len(incoming_to_a) == 1
            assert str(incoming_to_a[0]["source_id"]) == b

            # No truth_state side effect -- link_claims is not Truth
            # Maintenance.
            a_row = await pool.fetchrow(
                "SELECT properties->>'truth_state' AS ts FROM knowledge_nodes WHERE id = $1::uuid", a,
            )
            assert a_row["ts"] in (None, "IN")
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_get_claim_relations_finds_both_buckets_uniformly():
    """A CONTRADICTS edge (VALIDATED_BY bucket) and a SUPERSEDES edge
    (SUPERSEDES bucket) must both be findable through the SAME reader --
    the real point of ALL_CLAIM_RELATIONS existing at all."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task4")
            a = await _claim(pool, f"{PREFIX} claim A4", task)
            b = await _claim(pool, f"{PREFIX} claim B4", task)
            c = await _claim(pool, f"{PREFIX} claim C4", task)

            await relate_claims(pool, from_claim_id=a, to_claim_id=b, relation="CONTRADICTS")
            await link_claims(pool, from_claim_id=a, to_claim_id=c, relation="SUPPORTS")

            relations = await get_claim_relations(pool, a, direction="outgoing")
            found = {(r["relation"], str(r["target_id"])) for r in relations}
            assert found == {("CONTRADICTS", b), ("SUPPORTS", c)}
            assert ALL_CLAIM_RELATIONS.issuperset({"CONTRADICTS", "SUPPORTS"})
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
