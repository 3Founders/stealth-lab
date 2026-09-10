"""
B19 (private execution/ingestion scope enforcement) proving test:
`handle_extract_procedure_from_episode` (app/services/ingestion_jobs.py)
must extract every episode-derived procedure as `visibility='private'`
with the episode's own real `owner_id` -- never the `extract_procedure()`
default of `visibility="public"` -- so an individual's own episode never
silently becomes a globally-visible procedure just because it happened
to clear extraction's other gates.

This is a genuine live-DB integration test through the real production
entrypoint (the same one process_pending_jobs() dispatches a real
'extract_procedure_from_episode' job to), not a FakePool/offline
assertion on call args -- it reads the persisted `procedures` row back
and checks the real `visibility`/`owner_id` columns.

Same pattern as every other `*_e2e.py` file in this suite: skips without
a real DATABASE_URL, self-cleaning by name/session prefix.
"""
import asyncio
import os

import asyncpg
import pytest

from app.db.session import create_pool as _real_create_pool
from app.services.ingestion_jobs import handle_extract_procedure_from_episode
from app.services.observations import persist_observation

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

SESSION_ID = "b19-privacy-test-session"
PROJECT_ID = "b19-privacy-test-project"
OWNER_ID = "b19-privacy-test-owner"


async def _cleanup(pool: asyncpg.Pool, episode_ids: list[str]) -> None:
    async with pool.acquire() as conn:
        if episode_ids:
            await conn.execute(
                "DELETE FROM procedures WHERE source_episode_ids && $1::uuid[]", episode_ids,
            )
        await conn.execute(
            "DELETE FROM observation_events WHERE event_id IN "
            "(SELECT id FROM trace_events WHERE session_id = $1)", SESSION_ID,
        )
        await conn.execute(
            "DELETE FROM observations WHERE id NOT IN "
            "(SELECT observation_id FROM observation_events)"
        )
        await conn.execute("DELETE FROM episodes WHERE session_id = $1", SESSION_ID)
        await conn.execute("DELETE FROM trace_events WHERE session_id = $1", SESSION_ID)
        await conn.execute("DELETE FROM agent_traces WHERE session_id = $1", SESSION_ID)


async def _insert_event(pool, *, sequence, tool_name, dedup_key):
    return await pool.fetchval(
        "INSERT INTO trace_events (trace_id, session_id, sequence, event_type, "
        "\"timestamp\", tool_name, dedup_key, schema_version) "
        "VALUES ($1,$2,$3,'PostToolUse',now(),$4,$5,'1') RETURNING id",
        SESSION_ID, SESSION_ID, sequence, tool_name, dedup_key,
    )


async def _build_owned_episode(pool) -> str:
    """One real, gate-clearing episode (Edit -> Bash test-run -> Read ->
    Bash commit, same shape test_synthesis_auto_discovery_e2e.py's own
    `_build_compatible_episode` uses) whose `episodes` row carries a
    real, non-NULL `owner_id` -- the fact this test exists to prove gets
    propagated as `visibility='private'` on the resulting procedure."""
    await pool.execute(
        "INSERT INTO agent_traces (trace_id, session_id, started_at, schema_version, project_id) "
        "VALUES ($1, $2, now(), '1', $3) ON CONFLICT (trace_id) DO NOTHING",
        SESSION_ID, SESSION_ID, PROJECT_ID,
    )
    e1 = await _insert_event(pool, sequence=0, tool_name="Edit", dedup_key="b19-dedup-1")
    await persist_observation(
        pool, observation_type="file_touched", label="Modified b19/privacy.py",
        extractor_kind="deterministic", event_ids=[str(e1)],
        properties={"file_path": "b19/privacy.py", "tool_name": "Edit"},
    )
    e2 = await _insert_event(pool, sequence=1, tool_name="Bash", dedup_key="b19-dedup-2")
    await persist_observation(
        pool, observation_type="test_run", label="Ran tests (passing)",
        extractor_kind="deterministic", event_ids=[str(e2)],
        properties={"command": "pytest tests/test_b19_privacy.py", "passed": True},
    )
    e3 = await _insert_event(pool, sequence=2, tool_name="Read", dedup_key="b19-dedup-3")
    await persist_observation(
        pool, observation_type="semantic_label", label="Checked test output",
        extractor_kind="deterministic", event_ids=[str(e3)], properties={},
    )
    e4 = await _insert_event(pool, sequence=3, tool_name="Bash", dedup_key="b19-dedup-4")
    await persist_observation(
        pool, observation_type="commit_made", label="Committed the fix",
        extractor_kind="deterministic", event_ids=[str(e4)],
        properties={"command": "git commit -m 'b19 fix'"},
    )
    episode_id = await pool.fetchval(
        "INSERT INTO episodes (episode_type, content_ref, timestamp, metadata, "
        "session_id, project_id, owner_id, start_ts, end_ts) "
        "VALUES ('trace', $1, now(), '{}'::jsonb, $2, $3, $4, now() - interval '1 hour', NULL) "
        "RETURNING id",
        "b19-privacy#0:4", SESSION_ID, PROJECT_ID, OWNER_ID,
    )
    return str(episode_id)


def test_episode_extraction_is_private_by_default_with_the_real_owner_id():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        episode_ids: list[str] = []
        try:
            await _cleanup(pool, [])
            episode_id = await _build_owned_episode(pool)
            episode_ids = [episode_id]

            await handle_extract_procedure_from_episode(
                pool, {
                    "episode_id": episode_id, "session_id": SESSION_ID,
                    "goal_text": "Resolve the B19 privacy fixture issue through implementation and tests",
                    "outcome": "success",
                },
            )

            row = await pool.fetchrow(
                "SELECT visibility, owner_id FROM procedures "
                "WHERE t_invalid IS NULL AND source_episode_ids = ARRAY[$1::uuid]",
                episode_id,
            )
            assert row is not None, (
                "expected the real extraction path to have persisted a procedure "
                "sourced from this episode"
            )
            # The literal B19 requirement: episode-derived extraction must be
            # private by construction, never the extract_procedure() default
            # of visibility='public' -- and must carry the episode's real
            # owner_id, not a placeholder or NULL.
            assert row["visibility"] == "private", (
                f"expected episode-derived procedure to be private, got {row['visibility']!r}"
            )
            assert row["owner_id"] == OWNER_ID, (
                f"expected the episode's real owner_id to propagate, got {row['owner_id']!r}"
            )
        finally:
            await _cleanup(pool, episode_ids)
            await pool.close()

    asyncio.run(_run())
