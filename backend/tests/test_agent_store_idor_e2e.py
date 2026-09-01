"""Regression test for a real IDOR gap in `GET /v1/agent-store/{agent_id}`
(app/api/agent_store.py::get_agent).

`agents` (db/07_agents.sql) carries the same `visibility`/`owner_id`
columns every other Wave-1 object (`procedures`, `knowledge_nodes`,
`implementations`) is scoped by on read -- confirmed by grep, and every
sibling single-row-by-id reader in this codebase applies
`visibility_predicate`/`scope_predicates` (`app/api/implementations.py::
get_implementation`, `app/services/claim_graph_api.py::get_claim`).
`get_agent` was the one outlier: a raw `SELECT * FROM agents WHERE id =
$1` with no scope argument at all -- User A's private row, once created,
was readable by anyone who knew or guessed its UUID, regardless of who
they are.

Proven here against a REAL Postgres round-trip (a raw INSERT sets
visibility='private'/owner_id, since no current write path exercises
that combination for `agents` yet -- the gap is in the READ, and this is
the most direct way to prove the read-side gate holds for a row that
schema-legally exists):
  - the owner (`AccessScope.for_user(owner)`) CAN read their own private row.
  - a different identified user CANNOT (404, not the row).
  - an anonymous caller CANNOT (404).
  - a public agent remains readable by everyone, unaffected by the fix.

Skips (not fails) without a real DATABASE_URL, same convention as every
other `*_e2e.py` file.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.api.agent_store import get_agent
from app.db.session import create_pool
from app.services.access import AccessScope

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

RUN = uuid4().hex[:8]
NAME_PREFIX = f"idor-agent-{RUN}"


async def _cleanup(pool) -> None:
    await pool.execute("DELETE FROM agents WHERE name LIKE $1", f"{NAME_PREFIX}%")


def test_private_agent_not_readable_by_other_user_or_anonymous():
    import asyncio

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)

            owner = f"{NAME_PREFIX}-owner"
            other_user = f"{NAME_PREFIX}-other-user"

            private_row = await pool.fetchrow(
                "INSERT INTO agents (name, description, source, execution_mode, "
                "skill_ref, visibility, owner_id, created_by) "
                "VALUES ($1, 'd', 'user_submitted', 'local_skill', 'sk', "
                "'private', $2, $2) RETURNING id",
                f"{NAME_PREFIX}-private", owner,
            )
            private_id = private_row["id"]

            public_row = await pool.fetchrow(
                "INSERT INTO agents (name, description, source, execution_mode, "
                "skill_ref, visibility, created_by) "
                "VALUES ($1, 'd', 'user_submitted', 'local_skill', 'sk', "
                "'public', $2) RETURNING id",
                f"{NAME_PREFIX}-public", owner,
            )
            public_id = public_row["id"]

            # Owner reads their own private row: succeeds.
            row = await get_agent(private_id, pool=pool, scope=AccessScope.for_user(owner))
            assert row["id"] == private_id

            # A different identified user cannot read it: 404, not the row.
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                await get_agent(private_id, pool=pool, scope=AccessScope.for_user(other_user))
            assert exc_info.value.status_code == 404

            # Anonymous cannot read it either.
            with pytest.raises(HTTPException) as exc_info:
                await get_agent(private_id, pool=pool, scope=AccessScope.anonymous())
            assert exc_info.value.status_code == 404

            # Public row is unaffected by the fix -- everyone still reads it.
            row = await get_agent(public_id, pool=pool, scope=AccessScope.for_user(other_user))
            assert row["id"] == public_id
            row = await get_agent(public_id, pool=pool, scope=AccessScope.anonymous())
            assert row["id"] == public_id
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
