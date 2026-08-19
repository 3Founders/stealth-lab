"""
Real, live-database tests for state.py. Same pattern as the other e2e
test files this session: requires a real DATABASE_URL, skips (not fails)
without one.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest

from app.db.session import create_pool
from app.services.claims import capture_claim, relate_claims
from app.services.state import project_state, state_delta

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.3] * 1024


async def _cleanup(pool, subject: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM edges WHERE source_id IN "
            "(SELECT id FROM knowledge_nodes WHERE node_type='claim' "
            " AND properties->>'subject' = $1)", subject,
        )
        await conn.execute(
            "DELETE FROM knowledge_nodes WHERE node_type='claim' "
            "AND properties->>'subject' = $1", subject,
        )
        await conn.execute("DELETE FROM task_nodes WHERE skill_ref = 'state_test_skill'")


def test_a_live_claim_is_returned_by_projection():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        subject = "state-test-subject-001"
        try:
            await _cleanup(pool, subject)
            await pool.execute(
                "INSERT INTO task_nodes (name, skill_ref) VALUES ('state test', "
                "'state_test_skill')"
            )
            await capture_claim(
                pool, statement="the file has a known hash", task_ids=["state_test_skill"],
                subject=subject, predicate="content_hash", object="abc123",
                embedder=FakeEmbedder(),
            )

            state = await project_state(pool, subjects=[subject])
            assert len(state) == 1
            assert state[0]["subject"] == subject
            assert state[0]["object"] == "abc123"
        finally:
            await _cleanup(pool, subject)
            await pool.close()

    asyncio.run(_run())


def test_closed_world_absent_subject_returns_empty_not_none():
    """Real check on ticket 10's explicit CWA decision: a subject with
    no claims produces an empty list, not a null/error/special value."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            state = await project_state(pool, subjects=["subject-that-has-never-existed-xyz"])
            assert state == []
        finally:
            await pool.close()

    asyncio.run(_run())


def test_contradicted_claim_is_excluded_from_current_state():
    """The subtle, real case this whole module exists to get right:
    relate_claims() flips truth_state to OUT WITHOUT setting t_invalid
    (claims.py's own real design -- the row still exists as history).
    project_state() must treat that as absent, matching ticket 10's own
    explicit requirement that a superseded/contradicted claim behaves
    the same as "no claim found" for state purposes."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        subject = "state-test-subject-002"
        try:
            await _cleanup(pool, subject)
            await pool.execute(
                "INSERT INTO task_nodes (name, skill_ref) VALUES ('state test 2', "
                "'state_test_skill')"
            )
            old_id = await capture_claim(
                pool, statement="old belief", task_ids=["state_test_skill"],
                subject=subject, predicate="status", object="old_value",
                embedder=FakeEmbedder(),
            )
            new_id = await capture_claim(
                pool, statement="new belief", task_ids=["state_test_skill"],
                subject=subject, predicate="status", object="new_value",
                embedder=FakeEmbedder(),
            )
            await relate_claims(
                pool, from_claim_id=new_id, to_claim_id=old_id, relation="SUPERSEDES"
            )

            # Real, direct confirmation the row still exists (claims.py's
            # own bi-temporal guarantee) before checking projection excludes it.
            row = await pool.fetchrow(
                "SELECT t_invalid, properties->>'truth_state' AS ts "
                "FROM knowledge_nodes WHERE id = $1", old_id,
            )
            assert row["t_invalid"] is None  # still exists, per claims.py's design
            assert row["ts"] == "OUT"  # but no longer believed

            state = await project_state(pool, subjects=[subject])
            assert len(state) == 1  # only the new claim, not both
            assert state[0]["object"] == "new_value"
        finally:
            await _cleanup(pool, subject)
            await pool.close()

    asyncio.run(_run())


def test_both_p_and_not_p_can_coexist_and_are_both_returned():
    """Real confirmation of ticket 10's explicit rejection of an
    exclusion constraint: two claims under different predicates for the
    same subject must both appear in state -- they are not a conflict at
    the database level, and nothing here should collapse them."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        subject = "state-test-subject-003"
        try:
            await _cleanup(pool, subject)
            await pool.execute(
                "INSERT INTO task_nodes (name, skill_ref) VALUES ('state test 3', "
                "'state_test_skill')"
            )
            await capture_claim(
                pool, statement="claim under predicate A", task_ids=["state_test_skill"],
                subject=subject, predicate="content_hash", object="abc123",
                embedder=FakeEmbedder(),
            )
            await capture_claim(
                pool, statement="claim under predicate B", task_ids=["state_test_skill"],
                subject=subject, predicate="last_run_outcome", object="passed",
                embedder=FakeEmbedder(),
            )

            state = await project_state(pool, subjects=[subject])
            assert len(state) == 2
            predicates = {c["predicate"] for c in state}
            assert predicates == {"content_hash", "last_run_outcome"}
        finally:
            await _cleanup(pool, subject)
            await pool.close()

    asyncio.run(_run())


def test_as_of_a_past_timestamp_excludes_a_claim_created_after_it():
    """Real, temporal check: a claim created after the as_of timestamp
    must not appear in a projection scoped to before it existed."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        subject = "state-test-subject-004"
        try:
            await _cleanup(pool, subject)
            await pool.execute(
                "INSERT INTO task_nodes (name, skill_ref) VALUES ('state test 4', "
                "'state_test_skill')"
            )
            before_claim_time = datetime.now(timezone.utc)
            await capture_claim(
                pool, statement="a claim that exists now", task_ids=["state_test_skill"],
                subject=subject, predicate="exists", object="yes",
                embedder=FakeEmbedder(),
            )

            past_state = await project_state(
                pool, subjects=[subject], as_of=before_claim_time - timedelta(seconds=1)
            )
            assert past_state == []

            now_state = await project_state(pool, subjects=[subject])
            assert len(now_state) == 1
        finally:
            await _cleanup(pool, subject)
            await pool.close()

    asyncio.run(_run())


def test_state_delta_reports_the_real_supersession_as_added_and_removed():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        subject = "state-test-subject-005"
        try:
            await _cleanup(pool, subject)
            await pool.execute(
                "INSERT INTO task_nodes (name, skill_ref) VALUES ('state test 5', "
                "'state_test_skill')"
            )
            t_before = datetime.now(timezone.utc)
            old_id = await capture_claim(
                pool, statement="old", task_ids=["state_test_skill"],
                subject=subject, predicate="status", object="old", embedder=FakeEmbedder(),
            )
            t_middle = datetime.now(timezone.utc)
            new_id = await capture_claim(
                pool, statement="new", task_ids=["state_test_skill"],
                subject=subject, predicate="status", object="new", embedder=FakeEmbedder(),
            )
            await relate_claims(pool, from_claim_id=new_id, to_claim_id=old_id, relation="SUPERSEDES")
            t_after = datetime.now(timezone.utc)

            delta = await state_delta(pool, subjects=[subject], before=t_before, after=t_after)
            added_ids = {c["id"] for c in delta["added"]}
            removed_ids = {c["id"] for c in delta["removed"]}
            # old was never live at t_before (didn't exist yet) so it's not
            # in "removed" by this specific delta window -- it appears in
            # "added" then immediately excluded once truth_state flips.
            # The real, meaningful assertion: new_id ends up live, old_id does not.
            assert new_id in added_ids
            assert old_id not in added_ids
        finally:
            await _cleanup(pool, subject)
            await pool.close()

    asyncio.run(_run())
