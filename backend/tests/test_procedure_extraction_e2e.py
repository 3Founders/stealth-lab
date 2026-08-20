"""
Real, live-database tests for procedure_extraction/derive.py's two
DB-backed functions (derive_preconditions, derive_scope). Same pattern
as the other e2e test files: requires a real DATABASE_URL, skips (not
fails) without one.

THE CORE GROUNDING CLAIM this file tests directly: derive_preconditions
must produce EXACTLY the claims project_state() itself would return for
the same subject/timestamp -- not a paraphrase, not a subset, the same
predicates -- because that identity is what guarantees a derived
precondition can never fail V1 (groundedness).
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from app.db.session import create_pool as _real_create_pool
from app.services.environment_probe import assert_environment_claims
from app.services.procedure_extraction.derive import derive_preconditions, derive_scope
from app.services.procedure_extraction.evidence import ProcedureEvidence
from app.services.state import project_state

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PROJECT_ID = "procext-test-project-001"
SUBJECT = f"project:{PROJECT_ID}"


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.5] * 1024


async def _cleanup(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM edges WHERE source_id IN "
            "(SELECT id FROM knowledge_nodes WHERE properties->>'subject' = $1) "
            "OR target_id IN (SELECT id FROM knowledge_nodes WHERE properties->>'subject' = $1)",
            SUBJECT,
        )
        await conn.execute("DELETE FROM knowledge_nodes WHERE properties->>'subject' = $1", SUBJECT)


def test_derive_preconditions_matches_project_state_exactly(tmp_path):
    """The core grounding claim, tested directly: derive_preconditions'
    output is not an approximation of project_state()'s claims, it IS
    them -- same predicates, same objects."""
    import json
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"react": "^18.0.0"}}))
    (tmp_path / "package-lock.json").write_text("")

    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            now = datetime.now(timezone.utc)
            await assert_environment_claims(
                pool, project_id=PROJECT_ID, repo_root=str(tmp_path), embedder=FakeEmbedder(),
            )

            evidence = ProcedureEvidence(
                goal_text="g", outcome="success", project_id=PROJECT_ID,
                started_at=now + timedelta(seconds=5),  # after the claims were asserted
            )
            derived = await derive_preconditions(pool, evidence)
            direct = await project_state(pool, subjects=[SUBJECT], as_of=now + timedelta(seconds=5))

            derived_pairs = {(p.predicate, p.object) for p in derived}
            direct_pairs = {(c["predicate"], c["object"]) for c in direct}
            assert derived_pairs == direct_pairs
            assert derived_pairs, "fixture should have produced real claims to match against"
            for p in derived:
                assert p.subject == SUBJECT
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_derive_preconditions_drops_a_predicate_entirely_once_superseded(tmp_path):
    """
    REAL, VERIFIED LIMITATION -- and empirically the OPPOSITE of what
    was first assumed here (twice: neither "sees the old value" nor
    "sees the new value" turned out to be right). project_state()
    requires BOTH t_valid <= as_of (bi-temporal existence) AND
    truth_state='IN' (current epistemic belief) to hold together. Once a
    claim is superseded, its truth_state flips to 'OUT' globally (no
    time dimension of its own), so an as_of from BEFORE the supersession
    no longer sees the OLD value (truth_state fails) -- but the NEW
    claim's t_valid is AFTER that same as_of, so it doesn't see the NEW
    value either (t_valid <= as_of fails). Net result, confirmed against
    a real database: the predicate simply DISAPPEARS from the
    projection once superseded, for any as_of before the new claim's own
    t_valid. Consequence for extraction: re-extracting from an old
    episode after its project's environment has since changed can
    silently produce FEWER preconditions than were actually true when
    that episode ran -- not a wrong value, an absent one. Real, stated
    limitation -- not fixed in this pass, which only wires
    derive_preconditions to project_state() exactly as it is designed.
    """
    import json

    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"vite": "^5.0.0"}}))
            await assert_environment_claims(
                pool, project_id=PROJECT_ID, repo_root=str(tmp_path), embedder=FakeEmbedder(),
            )
            # Real margin after the first write's own now()-assigned
            # t_valid actually committed -- 1ms was tighter than real DB
            # round-trip latency and made this test itself flaky, not a
            # bug in derive_preconditions.
            episode_started_at = datetime.now(timezone.utc)
            await asyncio.sleep(0.05)

            # Environment changes AFTER the episode's own start time.
            (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"webpack": "^5.0.0"}}))
            await assert_environment_claims(
                pool, project_id=PROJECT_ID, repo_root=str(tmp_path), embedder=FakeEmbedder(),
            )

            evidence = ProcedureEvidence(
                goal_text="g", outcome="success", project_id=PROJECT_ID,
                started_at=episode_started_at,
            )
            derived = await derive_preconditions(pool, evidence)
            build_tool = next((p.object for p in derived if p.predicate == "has_build_tool"), None)
            assert build_tool is None, (
                "verified real behavior: the old value fails truth_state='IN' (superseded) and "
                "the new value fails t_valid<=as_of (didn't exist yet) -- the predicate is absent "
                "entirely, neither the pre- nor post-swap value survives this as_of"
            )
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_derive_preconditions_empty_without_project_id_or_started_at():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            evidence = ProcedureEvidence(goal_text="g", outcome="success")
            assert await derive_preconditions(pool, evidence) == []
        finally:
            await pool.close()

    asyncio.run(_run())


def test_derive_scope_extracts_language_only(tmp_path):
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            now = datetime.now(timezone.utc)
            (tmp_path / "requirements.txt").write_text("pytest\n")
            await assert_environment_claims(
                pool, project_id=PROJECT_ID, repo_root=str(tmp_path), embedder=FakeEmbedder(),
            )

            evidence = ProcedureEvidence(
                goal_text="g", outcome="success", project_id=PROJECT_ID,
                started_at=now + timedelta(seconds=5),
            )
            scope = await derive_scope(pool, evidence)
            assert scope == {"language": ["python"]}
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
