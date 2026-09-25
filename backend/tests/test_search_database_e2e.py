"""REAL two-project control plane: control database A + search/log database B.

Requires DATABASE_URL (A, migrated normally) and TEST_SEARCH_DATABASE_URL (B,
migrated with `scripts/migrate.py --target search`). Proves, end to end:
ingest -> the Procedure/Claim projections land on B (Goal projection stays on A)
-> find_best_way and find_ways retrieve the Procedure -> their retrieval_decisions
rows and the identity decisions are on B -> NOTHING about those five tables is
written to A. Only the judge's verdicts are scripted (no model provider is called)."""
import hashlib
import json
import os
import uuid

import pytest
import pytest_asyncio

from app.db.session import create_pool
from app.services import retrieval_service as rs
from app.services import search_projection as sp
from app.services import shards as sh
from app.services.access import AccessScope
from app.services.goals import find_or_create_goal
from app.services.procedures import capture_procedure
from tests.identity_fakes import CallbackProvider, ConceptEmbedder, make_judge

B_DSN = os.environ.get("TEST_SEARCH_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not (os.environ.get("DATABASE_URL") and B_DSN), reason="needs DATABASE_URL + TEST_SEARCH_DATABASE_URL")

T = "srch" + uuid.uuid4().hex[:6]
EMB = ConceptEmbedder()
U = AccessScope.unrestricted()


def _verdict(kind, a, b):
    ours = T in b.lower()
    if kind == "goal":
        return ("distinct", 0.9)
    if kind == "task_goal":
        return ("matches", 0.9) if ours and "export a docx file" in b.lower() else ("unrelated", 0.9)
    if kind == "task_procedure":
        return ("applies", 0.9) if ours else ("not_applicable", 0.9)
    return ("distinct", 0.9)


JUDGE = make_judge(CallbackProvider(_verdict, name="jev"))


class _RequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class _Context:
    def __init__(self, pool):
        self.request_context = _RequestContext(pool)


async def _counts(conn) -> dict[str, int]:
    return {table: int(await conn.fetchval(f"SELECT count(*) FROM {table}")) for table in sh.SEARCH_DB_TABLES}


@pytest_asyncio.fixture
async def dbs(monkeypatch):
    monkeypatch.setenv("SEARCH_DATABASE_URL", B_DSN)
    monkeypatch.setattr(rs, "default_judge", lambda: JUDGE)       # find_ways' Goal choice uses the default judge
    a = await create_pool()
    b = await create_pool(B_DSN)
    yield a, b
    await sh.close_search_pools()
    await b.close()
    await a.close()


@pytest.mark.asyncio
async def test_ingest_projects_to_b_and_both_retrieval_doors_read_it_without_writing_a(dbs):
    a, b = dbs
    a_before = await _counts(a)
    b_before = await _counts(b)

    # --- ingest: Goal + Procedure (canonical on A), then a near-duplicate Goal so a
    # judged identity decision is recorded
    proc = await capture_procedure(
        a, name=f"{T} docx writer", goal=f"{T} export a docx file", steps=[{"description": "use docx.js"}],
        provenance="prior_library", scope_type="global", goal_embedder=EMB, goal_judge=JUDGE)
    await sp.drain_outbox(a, pools=sh.pools_for(a))
    await find_or_create_goal(
        a, canonical_name=f"{T} export a docx document file", scope_type="global", provenance="prior_library",
        embedder=EMB, judge=JUDGE, idempotency_key=f"{T}-identity")
    await sp.drain_outbox(a, pools=sh.pools_for(a))

    # --- projections: Procedure on B only; Goal projection on A (with the hierarchy)
    assert await b.fetchval("SELECT count(*) FROM procedure_search_index WHERE procedure_id = $1::uuid",
                            proc["procedure_id"]) == 1
    assert await a.fetchval("SELECT count(*) FROM procedure_search_index WHERE procedure_id = $1::uuid",
                            proc["procedure_id"]) == 0
    assert await a.fetchval("SELECT count(*) FROM goal_search_index WHERE canonical_name = $1",
                            f"{T} export a docx file") == 1
    assert await b.fetchval("SELECT count(*) FROM identity_decisions WHERE idempotency_key = $1", f"{T}-identity") == 1

    # --- REST door: find_best_way
    query = f"{T} export a docx file"
    res = await rs.find_best_way(a, query, scope=U, embedder=EMB, judge=JUDGE, require_verified=False)
    assert [p["name"] for p in res["procedures"]] == [f"{T} docx writer"]
    sha = hashlib.sha256(query.encode()).hexdigest()
    assert await b.fetchval("SELECT count(*) FROM retrieval_decisions WHERE query_sha256 = $1", sha) == 1

    # --- MCP door: find_ways
    import app.mcp_server.server as srv

    reply = json.loads(await srv.find_ways(query, _Context(a), semantic=False, use_llm=False))
    assert reply["outcome"] == "resolved"
    assert f"{T} docx writer" in json.dumps(reply)
    assert await b.fetchval(
        "SELECT count(*) FROM retrieval_decisions WHERE query_sha256 = $1 AND mode = 'find_ways'", sha) == 1

    # --- nothing about the five search/log tables was written to A
    assert await _counts(a) == a_before
    b_after = await _counts(b)
    assert b_after["procedure_search_index"] > b_before["procedure_search_index"]
    assert b_after["retrieval_decisions"] >= b_before["retrieval_decisions"] + 2

    # --- verify/reindex run against the split without cross-database joins
    report = await sp.verify_projection(a)
    assert report["types"]["procedure"]["database"] == "search"
    assert report["types"]["procedure"]["missing"] == 0
