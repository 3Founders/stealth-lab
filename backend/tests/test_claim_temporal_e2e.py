"""
Live-database proving tests for app/services/claim_temporal.py.

Builds a real 2-version claim chain (`claims.supersede_claim`, same
pattern test_claim_versioning_e2e.py already establishes), each version
linked via a REAL `episode_links` row (`capture_claim`/`supersede_claim`'s
own `justification_episode_id` parameter) to a REAL `episodes` row, whose
`session_id` joins to a REAL `agent_traces` row carrying a distinct
`commit_hash` -- the exact real join `get_claim_commit_history` issues
(`episode_links -> episodes -> agent_traces` on `episodes.session_id =
agent_traces.session_id`).

Required-NOT-NULL columns for the hand-rolled trace/episode rows below
follow test_canonical_personal_memory_e2e.py's own real pattern
(`agent_traces` needs `trace_id`, `session_id`, `started_at`,
`schema_version`; `episodes` needs `episode_type`, `timestamp`) -- read
before writing this file.

Same pattern as every other `*_e2e.py` file: requires a real
DATABASE_URL, skips (not fails) without one, real Postgres, no mocks,
self-cleaning by name prefix.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest

from app.db.session import create_pool
from app.services.claim_temporal import (
    get_claim_commit_history,
    what_was_current_as_of_commit,
)
from app.services.claims import capture_claim, supersede_claim

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "claim-temporal-e2e"


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.0] * 1024


async def _cleanup(pool) -> None:
    await pool.execute(
        "DELETE FROM edges WHERE source_id IN "
        "(SELECT id FROM knowledge_nodes WHERE name LIKE $1)", f"{PREFIX}%",
    )
    await pool.execute(
        "DELETE FROM episode_links WHERE target_id IN "
        "(SELECT id FROM knowledge_nodes WHERE name LIKE $1)", f"{PREFIX}%",
    )
    await pool.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM task_nodes WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM agent_traces WHERE trace_id LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM episodes WHERE content_ref LIKE $1", f"{PREFIX}%")


async def _task_node(pool, name: str) -> str:
    await pool.fetchrow(
        "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2)",
        name, f"skill_{name}",
    )
    return name


async def _real_episode_with_commit(
    pool, *, suffix: str, session_id: str, commit_hash: str, repo: str, branch: str,
    when: datetime,
) -> str:
    """Build one real episode + one real agent_traces header, joined by
    `session_id` -- the exact real join `get_claim_commit_history` relies
    on. Returns the episode's real id (str), for `capture_claim`/
    `supersede_claim`'s `justification_episode_id`."""
    await pool.execute(
        "INSERT INTO agent_traces (trace_id, session_id, started_at, "
        "schema_version, repo, branch, commit_hash) "
        "VALUES ($1, $2, $3, 'v1', $4, $5, $6)",
        f"{PREFIX}-trace-{suffix}", session_id, when, repo, branch, commit_hash,
    )
    episode_id = await pool.fetchval(
        "INSERT INTO episodes (episode_type, content_ref, timestamp, "
        "session_id, metadata) "
        "VALUES ('trace', $1, $2, $3, $4::jsonb) RETURNING id",
        f"{PREFIX}#episode:{suffix}", when, session_id, {},
    )
    return str(episode_id)


def test_get_claim_commit_history_joins_real_episode_links_to_real_agent_traces():
    """Two-version chain, each version justified by its own real episode,
    each episode's session joined to a real agent_traces row with a
    distinct commit_hash. Proves the real
    episode_links -> episodes -> agent_traces join end to end."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task")
            t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
            t2 = datetime(2026, 2, 1, tzinfo=timezone.utc)

            ep1 = await _real_episode_with_commit(
                pool, suffix="v1", session_id=f"{PREFIX}-sess-1",
                commit_hash=f"{PREFIX}-commitA", repo="stealthlab", branch="main",
                when=t1,
            )
            v1 = await capture_claim(
                pool, statement=f"{PREFIX} v1 statement",
                task_ids=[f"skill_{task}"],
                justification_episode_id=ep1,
                embedder=FakeEmbedder(),
            )
            assert v1

            ep2 = await _real_episode_with_commit(
                pool, suffix="v2", session_id=f"{PREFIX}-sess-2",
                commit_hash=f"{PREFIX}-commitB", repo="stealthlab", branch="main",
                when=t2,
            )
            v2 = await supersede_claim(
                pool, prior_claim_id=v1, statement=f"{PREFIX} v2 statement",
                task_ids=[f"skill_{task}"],
                justification_episode_id=ep2,
                embedder=FakeEmbedder(),
            )
            assert v2

            history = await get_claim_commit_history(pool, v1)
            assert [h["claim_id"] for h in history] == [v1, v2]
            assert [h["version"] for h in history] == [1, 2]
            assert history[0]["statement"] == f"{PREFIX} v1 statement"
            assert history[1]["statement"] == f"{PREFIX} v2 statement"

            assert history[0]["commits"] == [
                {"commit_hash": f"{PREFIX}-commitA", "repo": "stealthlab", "branch": "main"}
            ]
            assert history[1]["commits"] == [
                {"commit_hash": f"{PREFIX}-commitB", "repo": "stealthlab", "branch": "main"}
            ]

            # anchoring from the newest version must walk the same full chain
            history_from_v2 = await get_claim_commit_history(pool, v2)
            assert [h["claim_id"] for h in history_from_v2] == [v1, v2]
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_get_claim_commit_history_honest_empty_commits_when_no_episode_link():
    """A version captured with no justification_episode_id at all must
    come back with commits == [] -- never fabricated, never an error."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task-noep")
            v1 = await capture_claim(
                pool, statement=f"{PREFIX} no episode statement",
                task_ids=[f"skill_{task}"],
                embedder=FakeEmbedder(),
            )
            assert v1

            history = await get_claim_commit_history(pool, v1)
            assert len(history) == 1
            assert history[0]["claim_id"] == v1
            assert history[0]["version"] == 1
            assert history[0]["commits"] == []
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_get_claim_commit_history_empty_list_for_unresolvable_claim():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            history = await get_claim_commit_history(
                pool, "00000000-0000-0000-0000-000000000000",
            )
            assert history == []
        finally:
            await pool.close()

    asyncio.run(_run())


def test_what_was_current_as_of_commit_real_two_version_chain():
    """The real product-spec proof: given commit A, the answer is v1;
    given commit B, the answer is v2; given an unrelated real commit
    never linked to this family, the answer is honestly None."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task-asof")
            t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
            t2 = datetime(2026, 2, 1, tzinfo=timezone.utc)

            ep1 = await _real_episode_with_commit(
                pool, suffix="asof-v1", session_id=f"{PREFIX}-sess-asof-1",
                commit_hash=f"{PREFIX}-asofA", repo="stealthlab", branch="main",
                when=t1,
            )
            v1 = await capture_claim(
                pool, statement=f"{PREFIX} asof v1",
                task_ids=[f"skill_{task}"],
                justification_episode_id=ep1,
                embedder=FakeEmbedder(),
            )
            assert v1

            ep2 = await _real_episode_with_commit(
                pool, suffix="asof-v2", session_id=f"{PREFIX}-sess-asof-2",
                commit_hash=f"{PREFIX}-asofB", repo="stealthlab", branch="main",
                when=t2,
            )
            v2 = await supersede_claim(
                pool, prior_claim_id=v1, statement=f"{PREFIX} asof v2",
                task_ids=[f"skill_{task}"],
                justification_episode_id=ep2,
                embedder=FakeEmbedder(),
            )
            assert v2

            result_a = await what_was_current_as_of_commit(
                pool, v1, commit_hash=f"{PREFIX}-asofA",
            )
            assert result_a is not None
            assert result_a["claim_id"] == v1
            assert result_a["version"] == 1

            result_b = await what_was_current_as_of_commit(
                pool, v1, commit_hash=f"{PREFIX}-asofB",
            )
            assert result_b is not None
            assert result_b["claim_id"] == v2
            assert result_b["version"] == 2

            result_unknown = await what_was_current_as_of_commit(
                pool, v1, commit_hash=f"{PREFIX}-never-linked-commit",
            )
            assert result_unknown is None
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_what_was_current_as_of_commit_none_for_unresolvable_claim():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            result = await what_was_current_as_of_commit(
                pool, "00000000-0000-0000-0000-000000000000",
                commit_hash="whatever",
            )
            assert result is None
        finally:
            await pool.close()

    asyncio.run(_run())
