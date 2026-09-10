"""
MCP hardening B30: `get_relevant_claims` -- bounded, compact Claim
references for a goal, reusing HybridRetriever rather than a new
retrieval mechanism, and the MCP tool wrapper.

Same pattern as every other `*_e2e.py` file: skips without a real
DATABASE_URL, self-cleaning by name prefix.
"""
import asyncio
import json
import os
from uuid import uuid4

import pytest

import app.mcp_server.server as srv
from app.db.session import create_pool
from app.services.claims import capture_claim
from app.services.embeddings import Embedder
from app.services.relevant_claims import MAX_TOP_K, get_relevant_claims

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


class _FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class _FakeContext:
    def __init__(self, pool):
        self.request_context = _FakeRequestContext(pool)


async def _cleanup(pool, subject: str) -> None:
    await pool.execute(
        "DELETE FROM knowledge_nodes WHERE node_type = 'claim' AND properties->>'subject' = $1",
        subject,
    )
    await pool.execute("DELETE FROM task_nodes WHERE skill_ref = $1", subject)


async def _capture_standalone_claim(pool, *, statement: str, subject: str, predicate: str, object: str, embedder) -> str:
    """capture_claim() requires >=1 live task_node to link to (task_ids
    resolve against task_nodes.skill_ref) -- a claim supporting nothing
    is dropped, not written orphaned (its own documented contract). This
    helper creates the minimal real task_node this test's claim needs."""
    await pool.execute("INSERT INTO task_nodes (name, skill_ref) VALUES ('t', $1)", subject)
    claim_id = await capture_claim(
        pool, statement=statement, task_ids=[subject], subject=subject,
        predicate=predicate, object=object, claim_type="fact", epistemic_status="observed",
        created_by="tester", scope_type="global", embedder=embedder,
    )
    assert claim_id is not None, "capture_claim silently dropped the claim -- task_node link failed"
    return claim_id


def test_get_relevant_claims_returns_bounded_compact_refs():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        subject = f"project:relevant-claims-probe-{run_id}"
        try:
            embedder = Embedder()
            statement = f"the canary cluster quota is available for probe {run_id}"
            await _capture_standalone_claim(
                pool, statement=statement, subject=subject,
                predicate="quota", object="available", embedder=embedder,
            )

            refs = await get_relevant_claims(pool, goal=statement, top_k=5)
            assert refs, "must find the claim it was just given"
            ref = refs[0]
            for key in ("claim_id", "version", "scope", "status", "belief",
                        "statement", "reason_for_relevance", "applicability", "evidence_summary"):
                assert key in ref
            assert ref["statement"] == statement
            assert ref["applicability"] is None  # general-purpose tool, not tied to one procedure

            # Bounded even if a caller asks for more than MAX_TOP_K.
            capped = await get_relevant_claims(pool, goal=statement, top_k=999)
            assert len(capped) <= MAX_TOP_K
        finally:
            await _cleanup(pool, subject)
            await pool.close()

    asyncio.run(_run())


def test_get_relevant_claims_mcp_tool_end_to_end():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        subject = f"project:relevant-claims-mcp-{run_id}"
        try:
            embedder = Embedder()
            statement = f"the deployment freeze window ends for probe {run_id}"
            await _capture_standalone_claim(
                pool, statement=statement, subject=subject,
                predicate="freeze_window", object="ended", embedder=embedder,
            )

            ctx = _FakeContext(pool)
            result = await srv.get_relevant_claims(goal=statement, ctx=ctx)
            refs = json.loads(result)
            assert any(r["statement"] == statement for r in refs)
        finally:
            await _cleanup(pool, subject)
            await pool.close()

    asyncio.run(_run())
