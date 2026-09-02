"""Live-DB half of spec section 19's SQL-injection coverage (see
test_injection_adversarial_offline.py's docstring item 2 for why this one
test needs a real connection rather than a mock: claims.capture_claim uses
pool.acquire()/conn.transaction(), which a shallow fake pool can't honestly
exercise).

Same convention as every other *_e2e.py file: requires a real DATABASE_URL,
skips (not fails) without one. Pattern copied from test_claim_evidence_e2e.py.
"""
from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from app.db.session import create_pool
from app.services.claims import capture_claim

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "sec-injection-e2e"


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


def _run(coro):
    return asyncio.run(coro)


def test_sql_injection_shaped_claim_content_is_stored_inert_not_executed():
    """A SQL-injection-shaped value in a claim's statement/object must (a)
    be accepted and stored VERBATIM as inert claim content, and (b) never
    actually execute as SQL -- proven strongly here, not just by inspecting
    query text: after writing the adversarial claim, knowledge_nodes and
    every other core table must still exist and be queryable. If the
    DROP TABLE payload had ever executed, this test's own cleanup and the
    table-existence check below would fail outright."""
    async def _body():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task_name = f"{PREFIX}-task-{uuid4()}"
            await pool.execute(
                "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2)",
                task_name, f"skill_{task_name}",
            )

            injection = f"{PREFIX}-python'; DROP TABLE knowledge_nodes; --"
            claim_id = await capture_claim(
                pool,
                statement=f"{PREFIX} language is {injection}",
                task_ids=[f"skill_{task_name}"],
                subject=f"{PREFIX}:project:1", predicate="language", object=injection,
                embedder=FakeEmbedder(),
            )
            assert claim_id, "capture_claim must succeed with adversarial content, not choke"

            # Strong proof: if DROP TABLE had executed, this SELECT itself
            # would fail with "relation knowledge_nodes does not exist".
            row = await pool.fetchrow(
                "SELECT properties FROM knowledge_nodes WHERE id = $1::uuid", claim_id
            )
            assert row is not None, "the claim row itself must exist -- table was not dropped"
            assert row["properties"]["object"] == injection, (
                "adversarial value must be stored VERBATIM as inert data, "
                "confirming it was never interpolated into executed SQL"
            )

            # Table still fully functional for unrelated content afterward.
            other_task = f"{PREFIX}-task2-{uuid4()}"
            await pool.execute(
                "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2)",
                other_task, f"skill_{other_task}",
            )
            control_claim_id = await capture_claim(
                pool,
                statement=f"{PREFIX} control claim, unrelated",
                task_ids=[f"skill_{other_task}"],
                subject=f"{PREFIX}:project:2", predicate="language", object="rust",
                embedder=FakeEmbedder(),
            )
            assert control_claim_id, "knowledge_nodes remains fully writable after the adversarial insert"
        finally:
            await _cleanup(pool)
            await pool.close()

    _run(_body())
