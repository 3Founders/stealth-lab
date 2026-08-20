"""
Real, live-database CAPSTONE e2e test for procedure_extraction's public
API (extract_procedure). Same pattern as the other e2e test files:
requires a real DATABASE_URL, skips (not fails) without one.

THE CLAIM THIS FILE PROVES: real session observations produce a real
`procedures` row, correctly tagged (extracted_by, approval_status=
'proposed'), and -- critically -- applicability.py's OWN, UNTOUCHED
find_applicable_procedures() does NOT return it while unapproved. This
is the approval gate proven end to end, before anything downstream ever
depends on it.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from app.db.session import create_pool as _real_create_pool
from app.services.applicability import find_applicable_procedures
from app.services.environment_probe import assert_environment_claims
from app.services.observations import persist_observation
from app.services.procedure_extraction import extract_procedure
from app.services.procedure_extraction.evidence import SessionEvidenceSource
from app.services.procedure_extraction.registry import approve_extractor

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

SESSION_ID = "procext-init-test-session-001"
PROJECT_ID = "procext-init-test-project-001"
SUBJECT = f"project:{PROJECT_ID}"


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.7] * 1024


async def _cleanup(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM procedures WHERE name LIKE 'procext-init-test%'"
        )
        await conn.execute(
            "DELETE FROM observation_events WHERE event_id IN "
            "(SELECT id FROM trace_events WHERE session_id = $1)", SESSION_ID,
        )
        await conn.execute(
            "DELETE FROM observations WHERE id NOT IN "
            "(SELECT observation_id FROM observation_events)"
        )
        await conn.execute("DELETE FROM trace_events WHERE session_id = $1", SESSION_ID)
        await conn.execute("DELETE FROM agent_traces WHERE session_id = $1", SESSION_ID)
        await conn.execute(
            "DELETE FROM edges WHERE source_id IN "
            "(SELECT id FROM knowledge_nodes WHERE properties->>'subject' = $1) "
            "OR target_id IN (SELECT id FROM knowledge_nodes WHERE properties->>'subject' = $1)",
            SUBJECT,
        )
        await conn.execute("DELETE FROM knowledge_nodes WHERE properties->>'subject' = $1", SUBJECT)


async def _seed_real_session(pool: asyncpg.Pool) -> None:
    trace_id = SESSION_ID
    await pool.execute(
        "INSERT INTO agent_traces (trace_id, session_id, started_at, schema_version) "
        "VALUES ($1, $2, now(), '1') ON CONFLICT (trace_id) DO NOTHING",
        trace_id, SESSION_ID,
    )
    for i, (tool, path) in enumerate([("Read", "auth/login.py"), ("Edit", "auth/login.py")]):
        event_id = await pool.fetchval(
            "INSERT INTO trace_events (trace_id, session_id, sequence, event_type, "
            "\"timestamp\", tool_name, dedup_key, schema_version) "
            "VALUES ($1,$2,$3,'PostToolUse',now(),$4,$5,'1') RETURNING id",
            trace_id, SESSION_ID, i, tool, f"procext-init-dedup-{i}",
        )
        if tool == "Edit":
            await persist_observation(
                pool, observation_type="file_touched", label=f"Modified {path}",
                extractor_kind="deterministic", event_ids=[str(event_id)],
                properties={"file_path": path, "tool_name": tool},
            )


def test_real_session_produces_an_unapproved_procedure_the_approval_gate_blocks(tmp_path):
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            now = datetime.now(timezone.utc)

            # Real environment claims, so preconditions have something
            # grounded to derive.
            import json
            (tmp_path / "requirements.txt").write_text("pytest\n")
            await assert_environment_claims(
                pool, project_id=PROJECT_ID, repo_root=str(tmp_path), embedder=FakeEmbedder(),
            )

            # Real session with real observations (the exact ingestion
            # path wired earlier this session).
            await _seed_real_session(pool)

            source = SessionEvidenceSource(
                pool, session_id=SESSION_ID,
                goal_text="procext-init-test: fix the failing login test",
                outcome="success", project_id=PROJECT_ID,
            )

            result = await extract_procedure(pool, source, client=None)  # no client -> deterministic
            assert result.procedure_id is not None, f"extraction failed: {result.validation_failures}"
            assert result.extracted_by == "deterministic_v1@1"

            row = await pool.fetchrow(
                "SELECT approval_status, extracted_by, capability_statement "
                "FROM procedures WHERE id = $1::uuid", result.version_row_id,
            )
            assert row["approval_status"] == "proposed"
            assert row["extracted_by"] == "deterministic_v1@1"
            assert row["capability_statement"]

            # THE GATE: applicability.py's own, untouched
            # find_applicable_procedures() must not surface an unapproved
            # procedure, no matter how well it matches.
            applicable = await find_applicable_procedures(
                pool, current_scope={}, require_verified=False,
            )
            matched_ids = {p["id"] for p in applicable}
            assert result.version_row_id not in {str(i) for i in matched_ids}, (
                "an unapproved procedure must never be returned by applicability, "
                "even with require_verified=False (explicit-invocation mode)"
            )
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_dry_run_persists_nothing():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            await _seed_real_session(pool)
            source = SessionEvidenceSource(
                pool, session_id=SESSION_ID, goal_text="procext-init-test: dry run",
                outcome="success",
            )
            result = await extract_procedure(pool, source, client=None, dry_run=True)
            assert result.procedure_id is None
            assert result.extracted is not None

            count = await pool.fetchval(
                "SELECT count(*) FROM procedures WHERE name LIKE 'procext-init-test%'"
            )
            assert count == 0
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_failed_episode_is_refused_before_any_strategy_runs():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            await _seed_real_session(pool)
            source = SessionEvidenceSource(
                pool, session_id=SESSION_ID, goal_text="procext-init-test: failed",
                outcome="failure",
            )
            result = await extract_procedure(pool, source, client=None)
            assert result.procedure_id is None
            assert any("V5" in f for f in result.validation_failures)
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
