"""
Real, live-database tests for environment_probe.py's write path
(assert_environment_claims). Same pattern as test_observations_e2e.py /
test_ingestion_jobs_e2e.py: requires a real DATABASE_URL, skips (not
fails) without one.
"""
import os

import asyncpg
import pytest

from app.db.session import create_pool as _real_create_pool
from app.services.environment_probe import assert_environment_claims
from app.services.state import project_state

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PROJECT_ID = "envprobe-test-project-001"
SUBJECT = f"project:{PROJECT_ID}"


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.4] * 1024


async def _cleanup(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM edges WHERE source_id IN "
            "(SELECT id FROM knowledge_nodes WHERE properties->>'subject' = $1) "
            "OR target_id IN (SELECT id FROM knowledge_nodes WHERE properties->>'subject' = $1)",
            SUBJECT,
        )
        await conn.execute(
            "DELETE FROM knowledge_nodes WHERE properties->>'subject' = $1", SUBJECT,
        )


def test_assert_environment_claims_writes_real_claims_project_state_reads(tmp_path):
    """The whole point: a real checkout's real facts land as real claims,
    and project_state() -- the function applicability.py's preconditions
    actually call -- can see them, with no task_node/edge required."""
    import json
    (tmp_path / "package.json").write_text(json.dumps({
        "dependencies": {"next": "^14.0.0"},
    }))
    (tmp_path / "package-lock.json").write_text("")

    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            written = await assert_environment_claims(
                pool, project_id=PROJECT_ID, repo_root=str(tmp_path), embedder=FakeEmbedder(),
            )
            assert len(written) >= 2  # has_framework=next, package_manager=npm at least

            claims = await project_state(pool, subjects=[SUBJECT])
            by_predicate = {c["predicate"]: c["object"] for c in claims}
            assert by_predicate.get("has_framework") == "next"
            assert by_predicate.get("package_manager") == "npm"
        finally:
            await _cleanup(pool)
            await pool.close()

    import asyncio
    asyncio.run(_run())


def test_reprobing_unchanged_repo_is_idempotent(tmp_path):
    import json
    (tmp_path / "requirements.txt").write_text("pytest\n")

    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            first = await assert_environment_claims(
                pool, project_id=PROJECT_ID, repo_root=str(tmp_path), embedder=FakeEmbedder(),
            )
            assert len(first) >= 1

            second = await assert_environment_claims(
                pool, project_id=PROJECT_ID, repo_root=str(tmp_path), embedder=FakeEmbedder(),
            )
            assert second == [], "re-probing an unchanged repo must write nothing new"

            claims = await project_state(pool, subjects=[SUBJECT])
            # exactly one live claim per predicate, not two
            predicates = [c["predicate"] for c in claims]
            assert len(predicates) == len(set(predicates))
        finally:
            await _cleanup(pool)
            await pool.close()

    import asyncio
    asyncio.run(_run())


def test_changed_environment_supersedes_not_duplicates(tmp_path):
    """A repo whose environment genuinely changes (build tool swapped)
    must supersede the old claim, not silently overwrite it or leave
    both live -- history stays queryable, current belief stays singular,
    same discipline every other claim in this codebase follows."""
    import json

    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"vite": "^5.0.0"}}))
            await assert_environment_claims(
                pool, project_id=PROJECT_ID, repo_root=str(tmp_path), embedder=FakeEmbedder(),
            )

            (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"webpack": "^5.0.0"}}))
            await assert_environment_claims(
                pool, project_id=PROJECT_ID, repo_root=str(tmp_path), embedder=FakeEmbedder(),
            )

            current = await project_state(pool, subjects=[SUBJECT])
            by_predicate = {c["predicate"]: c["object"] for c in current}
            assert by_predicate.get("has_build_tool") == "webpack", (
                "current belief must reflect the new fact"
            )

            all_claims = await pool.fetch(
                "SELECT properties->>'object' AS object, properties->>'truth_state' AS truth_state "
                "FROM knowledge_nodes WHERE properties->>'subject' = $1 "
                "AND properties->>'predicate' = 'has_build_tool'",
                SUBJECT,
            )
            objects_seen = {r["object"] for r in all_claims}
            assert objects_seen == {"vite", "webpack"}, (
                "the old claim must still exist (as history), not be deleted"
            )
        finally:
            await _cleanup(pool)
            await pool.close()

    import asyncio
    asyncio.run(_run())
