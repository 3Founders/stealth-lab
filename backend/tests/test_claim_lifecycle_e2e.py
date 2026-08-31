"""
Live-database proving tests for `get_claim_lifecycle_state`
(app/services/claims.py) -- CONSOLIDATED directive §5's 7-state claim
lifecycle (proposed/supported/current/disputed/contradicted/stale/
retired), computed at read time from real signals only, extending the
existing `_DISPUTED_CLAIM_SQL`/`has_open_conflict_trigger` computed-status
pattern (.scratch/final_architecture_audit.md §5) rather than adding a new
stored `status` column.

`proposed` is exempt: there is no real, honest signal to compute it from
today (no claim approval workflow exists), and the function's own
docstring documents that -- no live proof for it here on purpose.

Same pattern as every other `*_e2e.py` file: requires a real DATABASE_URL,
skips (not fails) without one.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest

from app.db.session import create_pool
from app.services.claims import (
    CLAIM_STALE_THRESHOLD_DAYS,
    capture_claim,
    get_claim_lifecycle_state,
    link_claims,
    relate_claims,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "claim-lifecycle-e2e"


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.0] * 1024


async def _cleanup(pool) -> None:
    await pool.execute(
        "DELETE FROM debates WHERE trigger_id IN "
        "(SELECT id FROM triggers WHERE task_node_id IN "
        "(SELECT id FROM task_nodes WHERE name LIKE $1))", f"{PREFIX}%",
    )
    await pool.execute(
        "DELETE FROM triggers WHERE task_node_id IN "
        "(SELECT id FROM task_nodes WHERE name LIKE $1)", f"{PREFIX}%",
    )
    await pool.execute(
        "DELETE FROM edges WHERE source_id IN "
        "(SELECT id FROM knowledge_nodes WHERE name LIKE $1) "
        "OR target_id IN (SELECT id FROM knowledge_nodes WHERE name LIKE $1)", f"{PREFIX}%",
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


def test_default_fresh_claim_is_current():
    """No dispute, no reaffirmation, fresh t_valid -- the overwhelmingly
    common real case, proven against the real DB default (t_valid set by
    the schema's own DEFAULT now(), no test override needed)."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-current-task")
            claim_id = await _claim(pool, f"{PREFIX} current claim", task)

            state = await get_claim_lifecycle_state(pool, claim_id)
            assert state == "current"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_open_conflict_trigger_makes_it_disputed():
    """Real trigger + no debate against a real, live, truth_state=IN
    claim -- has_open_conflict_trigger's own live-DB signal, reused
    verbatim by get_claim_lifecycle_state, precedence position 1."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-disputed-task")
            claim_id = await _claim(pool, f"{PREFIX} disputed claim", task)

            task_row = await pool.fetchrow(
                "SELECT id FROM task_nodes WHERE name = $1", f"{PREFIX}-disputed-task",
            )
            await pool.execute(
                "INSERT INTO edges (edge_type, custom_edge_type, "
                " source_id, source_table, target_id, target_table, "
                " properties, created_by, provenance) "
                "VALUES ('VALIDATED_BY'::edge_type, 'CONFLICTS_WITH', $1::uuid, 'task_nodes', "
                " $2::uuid, 'knowledge_nodes', $3, $4, 'company_ingested')",
                task_row["id"], claim_id, {}, "test",
            )
            await pool.execute(
                "INSERT INTO triggers (task_node_id, rule_name, metric_name, "
                " observed_value, threshold, sample_size) "
                "VALUES ($1::uuid, 'claim_conflict', 'error_rate', 1, 0, 1)",
                task_row["id"],
            )

            state = await get_claim_lifecycle_state(pool, claim_id)
            assert state == "disputed"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_superseded_claim_is_retired():
    """Real relate_claims(SUPERSEDES) call -- truth_state flips OUT and a
    live SUPERSEDES edge targets the old claim, precedence position 2."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-retired-task")
            old_id = await _claim(pool, f"{PREFIX} old claim", task)
            new_id = await _claim(pool, f"{PREFIX} new claim", task)

            await relate_claims(pool, from_claim_id=new_id, to_claim_id=old_id, relation="SUPERSEDES")

            state = await get_claim_lifecycle_state(pool, old_id)
            assert state == "retired"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_contradicted_claim_is_contradicted():
    """Real relate_claims(CONTRADICTS) call -- truth_state flips OUT via a
    live CONTRADICTS edge, no SUPERSEDES edge present, precedence
    position 3."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-contradicted-task")
            a = await _claim(pool, f"{PREFIX} claim A", task)
            b = await _claim(pool, f"{PREFIX} claim B", task)

            await relate_claims(pool, from_claim_id=a, to_claim_id=b, relation="CONTRADICTS")

            state = await get_claim_lifecycle_state(pool, b)
            assert state == "contradicted"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_old_unreaffirmed_claim_is_stale():
    """Real row with `t_valid` pushed back past CLAIM_STALE_THRESHOLD_
    DAYS (direct UPDATE, since capture_claim always writes t_valid = now())
    and no incoming reaffirming relation -- precedence position 4. Uses a
    controlled `as_of` rather than waiting 180 real days."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-stale-task")
            claim_id = await _claim(pool, f"{PREFIX} stale claim", task)

            as_of = datetime.now(timezone.utc)
            old_t_valid = as_of - timedelta(days=CLAIM_STALE_THRESHOLD_DAYS + 1)
            await pool.execute(
                "UPDATE knowledge_nodes SET t_valid = $1 WHERE id = $2::uuid",
                old_t_valid, claim_id,
            )

            state = await get_claim_lifecycle_state(pool, claim_id, as_of=as_of)
            assert state == "stale"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_old_but_reaffirmed_claim_is_supported_not_stale():
    """Same aged t_valid as the stale fixture, but with a real
    link_claims(SUPPORTS) edge reaffirming it -- reaffirmation beats
    staleness, precedence position 5 wins over position 4."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-supported-task")
            old_claim = await _claim(pool, f"{PREFIX} old reaffirmed claim", task)
            supporter = await _claim(pool, f"{PREFIX} supporting claim", task)

            as_of = datetime.now(timezone.utc)
            old_t_valid = as_of - timedelta(days=CLAIM_STALE_THRESHOLD_DAYS + 1)
            await pool.execute(
                "UPDATE knowledge_nodes SET t_valid = $1 WHERE id = $2::uuid",
                old_t_valid, old_claim,
            )
            await link_claims(pool, from_claim_id=supporter, to_claim_id=old_claim, relation="SUPPORTS")

            state = await get_claim_lifecycle_state(pool, old_claim, as_of=as_of)
            assert state == "supported"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_missing_claim_raises():
    """A claim id that does not resolve to a live claim row has no
    honest lifecycle state -- real ValueError, not a guessed default."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            with pytest.raises(ValueError):
                await get_claim_lifecycle_state(pool, "00000000-0000-0000-0000-000000000000")
        finally:
            await pool.close()

    asyncio.run(_run())
