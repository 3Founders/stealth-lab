"""
Real, live-database tests for app/services/domain_search.py +
app/api/search.py. Same pattern as the other e2e test files: requires a
real DATABASE_URL, skips (not fails) without one. FakeEmbedder (same
idiom as test_retrieval_e2e.py / test_applicability_e2e.py) supplies
deterministic fixed vectors so vector search finds real, predictable
neighbours without spending real embedding-provider quota.

Scope: this file proves domain_search's OWN composition actually works
against real Postgres -- a real procedure/claim/task each findable
through search_global, the V0 scope_type/repository_id filter actually
filtering real rows, and find_best_way surfacing a real recommendation
with real evidence/capability from a real verified+approved procedure.
It does not re-prove applicability.py's cascade or retrieval.py's RRF
fusion themselves (already covered live by test_applicability_e2e.py /
test_retrieval_e2e.py) -- only that this module wires them correctly.
"""
from __future__ import annotations

import asyncio
import os

import pytest
from fastapi import FastAPI

from app.api.search import router as search_router
from app.db.session import create_pool
from app.services.access import AccessScope
from app.services.claims import capture_claim
from app.services.domain_search import find_best_way, search_global
from app.services.embeddings import to_pgvector
from app.services.procedures import (
    MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
    MIN_SUCCESSES_FOR_VERIFIED,
    capture_procedure,
    record_execution_outcome,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

def _vec(seed: float) -> list[float]:
    """A distinct constant vector per test/fixture. find_applicable_
    procedures' candidate pool is CORPUS-WIDE (not scoped by this file's
    name prefix) -- two fixtures sharing one embedding vector would be
    indistinguishable by cosine similarity and could rank either one on
    top, non-deterministically. Every test below gets its own seed so
    "the vector for THIS test's fixture" is unambiguous."""
    return [seed] * 1024


class FakeEmbedder:
    """Deterministic per-call vector, keyed on the input text -- lets one
    embedder instance serve the procedure/claim/task/query legs of a
    single search_global call with distinguishable, predictable vectors,
    same discipline test_retrieval_e2e.py's own FakeEmbedder documents."""

    def __init__(self, by_text: dict[str, list[float]], default=None):
        self._by_text = by_text
        self._default = default or [0.5] * 1024

    async def embed_one(self, text, input_type="query"):
        for key, vec in self._by_text.items():
            if key in text:
                return vec
        return self._default


def _run(coro):
    return asyncio.run(coro)


async def _cleanup(pool, prefix: str) -> None:
    await pool.execute(
        "DELETE FROM edges WHERE source_id IN (SELECT id FROM knowledge_nodes WHERE name LIKE $1) "
        "OR source_id IN (SELECT id FROM task_nodes WHERE name LIKE $1)",
        f"{prefix}%",
    )
    # evidence is append-only (db/24's own invariant #19: DELETE is
    # rejected by a real trigger) -- tombstone via t_invalid instead of
    # deleting, same retraction idiom the schema itself mandates.
    await pool.execute(
        "UPDATE evidence SET t_invalid = now() WHERE t_invalid IS NULL "
        "AND target_id IN (SELECT id FROM procedures WHERE name LIKE $1)",
        f"{prefix}%",
    )
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM task_nodes WHERE name LIKE $1", f"{prefix}%")


async def _make_verified_procedure(pool, name: str, **kwargs) -> dict:
    """Real lifecycle path, same helper shape test_applicability_e2e.py
    uses: drives a fresh procedure to verified+approved via real recorded
    outcomes, not by writing the enum value directly."""
    result = await capture_procedure(
        pool, name=name, goal=f"{name} goal", provenance="system_pending_review",
        scope_type=kwargs.pop("scope_type", "global"),
        scope_entity_id=kwargs.pop("scope_entity_id", None),
        embedding=kwargs.pop("embedding", None),
        **kwargs,
    )
    row_id = result["id"]
    for i in range(MIN_SUCCESSES_FOR_VERIFIED):
        await record_execution_outcome(
            pool, procedure_row_id=row_id, success=True,
            context_key=f"ctx-{i % (MIN_DISTINCT_CONTEXTS_FOR_VERIFIED + 1)}",
        )
    await pool.execute(
        "UPDATE procedures SET approval_status = 'approved' WHERE id = $1::uuid", row_id,
    )
    return result


def test_search_global_finds_a_real_procedure_task_and_claim():
    async def _run_test():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = "dsrch-test-basic"
        try:
            await _cleanup(pool, prefix)
            proc_vec, claim_vec, task_vec = _vec(0.6101), _vec(0.6102), _vec(0.6103)

            proc_name = f"{prefix}-procedure deploy the release safely"
            await _make_verified_procedure(pool, proc_name, embedding=proc_vec)

            task_row = await pool.fetchrow(
                "INSERT INTO task_nodes (name, description, skill_ref, embedding) "
                "VALUES ($1, 'a real task', $2, $3::vector) RETURNING id",
                f"{prefix}-task run the release checklist", f"{prefix}-skill", to_pgvector(task_vec),
            )
            task_id = task_row["id"]

            claim_subject = f"{prefix}-subject"
            claim_id = await capture_claim(
                pool, statement=f"{prefix}-claim the release checklist passed",
                task_ids=[f"{prefix}-skill"], subject=claim_subject, predicate="status", object="passed",
                embedder=FakeEmbedder({"release checklist passed": claim_vec}),
            )
            assert claim_id is not None  # real precondition: the task_node edge resolved

            embedder = FakeEmbedder({
                "deploy the release": proc_vec,
                "release checklist passed": claim_vec,
                "release checklist": task_vec,
            })

            result = await search_global(
                pool, f"{prefix} deploy the release checklist passed",
                scope=AccessScope.unrestricted(), embedder=embedder, limit=10,
            )

            assert set(result["object_types"]) == {"procedure", "task", "claim"}
            proc_names = [p["name"] for p in result["results"]["procedure"]]
            task_names = [t["name"] for t in result["results"]["task"]]
            claim_ids = [c["id"] for c in result["results"]["claim"]]
            assert proc_name in proc_names
            assert f"{prefix}-task run the release checklist" in task_names
            assert claim_id in claim_ids
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    _run(_run_test())


def test_search_global_object_types_filters_to_requested_subset():
    async def _run_test():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = "dsrch-test-subset"
        try:
            await _cleanup(pool, prefix)
            proc_vec = _vec(0.6201)
            proc_name = f"{prefix}-procedure narrow search target"
            await _make_verified_procedure(pool, proc_name, embedding=proc_vec)

            embedder = FakeEmbedder({"narrow search target": proc_vec})
            result = await search_global(
                pool, f"{prefix} narrow search target",
                object_types=["procedure"], scope=AccessScope.unrestricted(), embedder=embedder,
            )
            assert result["object_types"] == ["procedure"]
            assert set(result["results"].keys()) == {"procedure"}
            assert any(p["name"] == proc_name for p in result["results"]["procedure"])
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    _run(_run_test())


def test_search_global_repository_id_filters_by_real_scope_entity():
    async def _run_test():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = "dsrch-test-repofilter"
        try:
            await _cleanup(pool, prefix)
            proc_vec = _vec(0.6301)
            in_repo = await _make_verified_procedure(
                pool, f"{prefix}-in-repo procedure", embedding=proc_vec,
                scope_type="repository", scope_entity_id="dsrch-repo-A",
            )
            await _make_verified_procedure(
                pool, f"{prefix}-other-repo procedure", embedding=proc_vec,
                scope_type="repository", scope_entity_id="dsrch-repo-B",
            )

            embedder = FakeEmbedder({"procedure": proc_vec})
            result = await search_global(
                pool, f"{prefix} procedure", object_types=["procedure"],
                repository_id="dsrch-repo-A", scope=AccessScope.unrestricted(),
                embedder=embedder, limit=10,
            )
            ids = [p["id"] for p in result["results"]["procedure"]]
            assert in_repo["id"] in ids
            names = [p["name"] for p in result["results"]["procedure"]]
            assert f"{prefix}-other-repo procedure" not in names
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    _run(_run_test())


def test_search_global_rejects_solution_object_type_live():
    async def _run_test():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            with pytest.raises(ValueError, match="not a distinct searchable entity"):
                await search_global(
                    pool, "anything", object_types=["solution"],
                    scope=AccessScope.unrestricted(), embedder=FakeEmbedder({}),
                )
        finally:
            await pool.close()

    _run(_run_test())


def test_find_best_way_recommends_a_real_verified_procedure_with_evidence():
    async def _run_test():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = "dsrch-test-recommend"
        try:
            await _cleanup(pool, prefix)
            proc_vec = _vec(0.6401)
            proc_name = f"{prefix}-procedure roll back the deployment"
            made = await _make_verified_procedure(pool, proc_name, embedding=proc_vec)

            embedder = FakeEmbedder({"roll back the deployment": proc_vec})
            result = await find_best_way(
                pool, f"{prefix} roll back the deployment",
                scope=AccessScope.unrestricted(), embedder=embedder,
            )

            assert result["recommendation"] is not None, f"expected a real recommendation, got: {result}"
            rec = result["recommendation"]
            assert rec["procedure_id"] == made["procedure_id"]
            assert rec["verdict"] == "ALLOW"
            assert "successes" in rec["capability_note"]
            assert rec["evidence"], "evidence must be real citations, not an empty list"
            assert result["confidence"] == "high"
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    _run(_run_test())


def test_find_best_way_honest_empty_when_precondition_unsatisfied():
    """Real CWA fail-closed proof through this module: a procedure whose
    precondition names a subject with no live matching claim must yield
    an honest empty recommendation, not a fabricated one.

    Paired with a `scope_constraint` naming a repository id nothing else
    in this shared dev database uses: this repo's own corpus has other
    real, unrelated verified procedures with no preconditions of their
    own, which would otherwise satisfy find_applicable_procedures'
    cascade and get recommended instead -- a real result, but not proof
    of what THIS test means to prove (that a disqualified candidate does
    not get papered over). The scope_constraint filters every OTHER
    corpus procedure out, so the only way this assertion passes is if the
    procedure this test built (and only it) was correctly disqualified.
    """
    async def _run_test():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = "dsrch-test-emptyreco"
        try:
            await _cleanup(pool, prefix)
            proc_vec = _vec(0.6501)
            subject = f"{prefix}-subject-with-no-claim"
            repo_id = f"{prefix}-repo"
            await _make_verified_procedure(
                pool, f"{prefix}-procedure gated on missing claim", embedding=proc_vec,
                preconditions=[{"subject": subject, "predicate": "status", "object": "ready"}],
                scope_type="repository", scope_entity_id=repo_id,
            )

            embedder = FakeEmbedder({"gated on missing claim": proc_vec})
            result = await find_best_way(
                pool, f"{prefix} gated on missing claim",
                scope=AccessScope.unrestricted(), embedder=embedder,
                scope_constraint={"repository_id": repo_id},
            )
            assert result["recommendation"] is None, f"expected an honest empty result, got: {result}"
            assert result["confidence"] == "none"
            assert "honest empty result" in result["reason"]
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    _run(_run_test())


def test_rest_routes_end_to_end_against_real_db(monkeypatch):
    """One full-stack proof (real pool, real router, real domain_search
    functions underneath) that the REST surface itself is wired correctly
    -- request parsing through to a real DB-backed response.

    Real ASGI app + real pool, both driven from ONE event loop
    (asyncio.run() below), same discipline test_repository_knowledge_e2e.py's
    own `test_repository_router_returns_live_composed_knowledge` documents:
    an asyncpg pool's connections are bound to the loop that created them,
    so a sync TestClient (its own thread/loop) would cross-loop and fail.
    httpx.AsyncClient(transport=ASGITransport(...)) inside the same async
    function that owns the pool avoids that.

    Only the embedder is swapped (functools.partial over the real
    search_global/find_best_way, via pytest's own monkeypatch fixture so
    it is restored automatically) -- the route handlers, dependency
    wiring, and Pydantic request/response shaping are all exercised for
    real."""
    import functools

    import httpx

    import app.api.search as search_api

    async def _run_test():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = "dsrch-test-restapi"
        try:
            await _cleanup(pool, prefix)
            proc_vec = _vec(0.6601)
            proc_name = f"{prefix}-procedure restart the worker pool"
            made = await _make_verified_procedure(pool, proc_name, embedding=proc_vec)

            fake = FakeEmbedder({"restart the worker pool": proc_vec})
            monkeypatch.setattr(search_api, "search_global", functools.partial(search_global, embedder=fake))
            monkeypatch.setattr(search_api, "find_best_way", functools.partial(find_best_way, embedder=fake))

            app = FastAPI()
            app.state.pool = pool
            app.include_router(search_router)

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/v1/search", params={
                    "q": f"{prefix} restart the worker pool", "object_types": "procedure",
                })
                assert resp.status_code == 200
                body = resp.json()
                names = [p["name"] for p in body["results"]["procedure"]]
                assert proc_name in names

                resp2 = await client.post("/v1/search/recommend", json={
                    "goal": f"{prefix} restart the worker pool",
                })
                assert resp2.status_code == 200
                body2 = resp2.json()
                assert body2["recommendation"] is not None
                assert body2["recommendation"]["procedure_id"] == made["procedure_id"]
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    _run(_run_test())
