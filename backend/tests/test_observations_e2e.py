"""
Real, live-database tests for observations.py's persistence and
claim-promotion functions. Same pattern as test_trace_ingestion_e2e.py:
requires a real DATABASE_URL, skips (not fails) without one.
"""
import asyncio
import os

import asyncpg

from app.db.session import create_pool as _real_create_pool
import pytest

from app.services.observations import persist_observation, promote_observation_to_claim

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.2] * 1024


async def _cleanup(pool: asyncpg.Pool, session_id: str) -> None:
    async with pool.acquire() as conn:
        # Observations don't carry session_id directly -- clean up via
        # the trace_events they cite, same real ordering discipline as
        # test_trace_ingestion_e2e.py's own cleanup (children before
        # parents, so nothing is orphaned mid-test-run).
        await conn.execute(
            "DELETE FROM observation_events WHERE event_id IN "
            "(SELECT id FROM trace_events WHERE session_id = $1)", session_id,
        )
        await conn.execute(
            "DELETE FROM observations WHERE id NOT IN "
            "(SELECT observation_id FROM observation_events)"
        )
        await conn.execute(
            "DELETE FROM edges WHERE source_id IN "
            "(SELECT id FROM knowledge_nodes WHERE created_by = 'claim_capture' "
            " AND name LIKE 'obs-test%')"
        )
        await conn.execute(
            "DELETE FROM knowledge_nodes WHERE created_by = 'claim_capture' "
            "AND name LIKE 'obs-test%'"
        )
        await conn.execute("DELETE FROM trace_events WHERE session_id = $1", session_id)
        await conn.execute("DELETE FROM agent_traces WHERE session_id = $1", session_id)
        await conn.execute("DELETE FROM task_nodes WHERE skill_ref = 'obs_test_skill'")


def test_persist_observation_writes_real_row_and_real_links():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_id = "obs-test-session-001"
        try:
            await _cleanup(pool, session_id)

            trace_id = await pool.fetchval(
                "INSERT INTO agent_traces (trace_id, session_id, started_at, schema_version) "
                "VALUES ($1, $2, now(), '1') RETURNING trace_id",
                "obs-test-trace-001", session_id,
            )
            event_id = await pool.fetchval(
                "INSERT INTO trace_events (trace_id, session_id, sequence, event_type, "
                "\"timestamp\", tool_name, dedup_key, schema_version) "
                "VALUES ($1,$2,0,'PostToolUse',now(),'Edit','obs-test-dedup-1','1') "
                "RETURNING id",
                trace_id, session_id,
            )

            obs_id = await persist_observation(
                pool, observation_type="file_touched", label="Modified obs-test-file.py",
                extractor_kind="deterministic", event_ids=[str(event_id)],
                properties={"file_path": "obs-test-file.py"},
            )
            assert obs_id is not None

            row = await pool.fetchrow("SELECT * FROM observations WHERE id = $1", obs_id)
            assert row["observation_type"] == "file_touched"
            assert row["extractor_kind"] == "deterministic"
            assert row["model_id"] is None  # deterministic -- no model involved

            link_count = await pool.fetchval(
                "SELECT count(*) FROM observation_events WHERE observation_id = $1", obs_id
            )
            assert link_count == 1

            # Real reverse-traversal check -- the whole point of the
            # join-table design over an array-on-observation approach.
            reverse = await pool.fetchval(
                "SELECT observation_id FROM observation_events WHERE event_id = $1", event_id
            )
            assert str(reverse) == obs_id
        finally:
            await _cleanup(pool, session_id)
            await pool.close()

    asyncio.run(_run())


def test_promote_deterministic_observation_sets_observed_epistemic_status():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_id = "obs-test-session-002"
        try:
            await _cleanup(pool, session_id)

            task_id = await pool.fetchval(
                "INSERT INTO task_nodes (name, skill_ref) VALUES ('obs-test task', "
                "'obs_test_skill') RETURNING id"
            )
            trace_id = await pool.fetchval(
                "INSERT INTO agent_traces (trace_id, session_id, started_at, schema_version) "
                "VALUES ($1, $2, now(), '1') RETURNING trace_id",
                "obs-test-trace-002", session_id,
            )
            event_id = await pool.fetchval(
                "INSERT INTO trace_events (trace_id, session_id, sequence, event_type, "
                "\"timestamp\", tool_name, dedup_key, schema_version) "
                "VALUES ($1,$2,0,'PostToolUse',now(),'Bash','obs-test-dedup-2','1') "
                "RETURNING id",
                trace_id, session_id,
            )
            obs_id = await persist_observation(
                pool, observation_type="test_run", label="obs-test claim from deterministic obs",
                extractor_kind="deterministic", event_ids=[str(event_id)],
            )

            claim_id = await promote_observation_to_claim(
                pool, observation_id=obs_id, task_ids=["obs_test_skill"],
                embedder=FakeEmbedder(),
            )
            assert claim_id is not None

            claim_row = await pool.fetchrow(
                "SELECT properties FROM knowledge_nodes WHERE id = $1", claim_id
            )
            props = dict(claim_row["properties"])
            assert props["epistemic_status"] == "observed"
            assert props["claim_type"] == "test_run"
            assert "deterministic_v1" in props["extraction_version"]
            assert props["statement"] == "obs-test claim from deterministic obs"
        finally:
            await _cleanup(pool, session_id)
            await pool.close()

    asyncio.run(_run())


def test_promote_model_observation_sets_inferred_epistemic_status():
    """Real, important case: the deterministic/model split must actually
    flow through to the claim's epistemic_status correctly -- this is
    the one decision ticket 04 was explicitly given ownership of."""
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_id = "obs-test-session-003"
        try:
            await _cleanup(pool, session_id)

            task_id = await pool.fetchval(
                "INSERT INTO task_nodes (name, skill_ref) VALUES ('obs-test task 2', "
                "'obs_test_skill') RETURNING id"
            )
            trace_id = await pool.fetchval(
                "INSERT INTO agent_traces (trace_id, session_id, started_at, schema_version) "
                "VALUES ($1, $2, now(), '1') RETURNING trace_id",
                "obs-test-trace-003", session_id,
            )
            event_id = await pool.fetchval(
                "INSERT INTO trace_events (trace_id, session_id, sequence, event_type, "
                "\"timestamp\", tool_name, dedup_key, schema_version) "
                "VALUES ($1,$2,0,'PostToolUse',now(),'Edit','obs-test-dedup-3','1') "
                "RETURNING id",
                trace_id, session_id,
            )
            obs_id = await persist_observation(
                pool, observation_type="semantic_label",
                label="obs-test claim from model-derived obs",
                extractor_kind="model", event_ids=[str(event_id)],
                model_id="gemma-4-31B-it", prompt_hash="abc123", decoding_params_hash="def456",
            )

            claim_id = await promote_observation_to_claim(
                pool, observation_id=obs_id, task_ids=["obs_test_skill"],
                embedder=FakeEmbedder(),
            )
            claim_row = await pool.fetchrow(
                "SELECT properties FROM knowledge_nodes WHERE id = $1", claim_id
            )
            props = dict(claim_row["properties"])
            assert props["epistemic_status"] == "inferred"
            assert "gemma-4-31B-it" in props["extraction_version"]
        finally:
            await _cleanup(pool, session_id)
            await pool.close()

    asyncio.run(_run())


def test_promote_nonexistent_observation_returns_none():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            result = await promote_observation_to_claim(
                pool, observation_id="00000000-0000-0000-0000-000000000099",
                task_ids=["whatever"], embedder=FakeEmbedder(),
            )
            assert result is None
        finally:
            await pool.close()

    asyncio.run(_run())


def test_promoting_a_private_observation_is_blocked_for_a_different_viewer():
    """Real, live confirmation of the fix: promote_observation_to_claim()
    previously ran an unscoped fetch, so any caller could promote (and
    thereby read the content of) any observation regardless of
    visibility. A private observation owned by 'alice' must not be
    promotable under bob's scope (returns None, same contract as
    'observation not found'), but must succeed under alice's own scope
    and under unrestricted()."""
    async def _run():
        from app.services.access import AccessScope

        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_id = "obs-test-session-004-private"
        try:
            await _cleanup(pool, session_id)

            task_id = await pool.fetchval(
                "INSERT INTO task_nodes (name, skill_ref) VALUES ('obs-test task 3', "
                "'obs_test_skill') RETURNING id"
            )
            trace_id = await pool.fetchval(
                "INSERT INTO agent_traces (trace_id, session_id, started_at, schema_version) "
                "VALUES ($1, $2, now(), '1') RETURNING trace_id",
                "obs-test-trace-004", session_id,
            )
            event_id = await pool.fetchval(
                "INSERT INTO trace_events (trace_id, session_id, sequence, event_type, "
                "\"timestamp\", tool_name, dedup_key, schema_version) "
                "VALUES ($1,$2,0,'PostToolUse',now(),'Edit','obs-test-dedup-4','1') "
                "RETURNING id",
                trace_id, session_id,
            )
            obs_id = await persist_observation(
                pool, observation_type="file_touched",
                label="obs-test private observation",
                extractor_kind="deterministic", event_ids=[str(event_id)],
                owner_id="alice", visibility="private",
            )

            # Confirm the write actually landed as private -- otherwise
            # this test would pass for the wrong reason.
            row = await pool.fetchrow(
                "SELECT owner_id, visibility::text AS visibility FROM observations WHERE id = $1",
                obs_id,
            )
            assert row["owner_id"] == "alice"
            assert row["visibility"] == "private"

            blocked = await promote_observation_to_claim(
                pool, observation_id=obs_id, task_ids=["obs_test_skill"],
                embedder=FakeEmbedder(), scope=AccessScope.for_user("bob"),
            )
            assert blocked is None

            blocked_anon = await promote_observation_to_claim(
                pool, observation_id=obs_id, task_ids=["obs_test_skill"],
                embedder=FakeEmbedder(), scope=AccessScope.anonymous(),
            )
            assert blocked_anon is None

            allowed = await promote_observation_to_claim(
                pool, observation_id=obs_id, task_ids=["obs_test_skill"],
                embedder=FakeEmbedder(), scope=AccessScope.for_user("alice"),
            )
            assert allowed is not None

            # The resulting claim must inherit the observation's own
            # ownership, not silently revert to capture_claim()'s public
            # default -- otherwise a private observation leaks into the
            # commons the moment it's promoted.
            claim_row = await pool.fetchrow(
                "SELECT owner_id, visibility::text AS visibility FROM knowledge_nodes "
                "WHERE id = $1", allowed,
            )
            assert claim_row["owner_id"] == "alice"
            assert claim_row["visibility"] == "private"
        finally:
            await _cleanup(pool, session_id)
            await pool.close()

    asyncio.run(_run())
