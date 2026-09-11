"""
Real, live-database tests for app/services/solution_search.py +
app/api/solutions.py's `/v1/solutions/search` route. Same pattern as
test_domain_search_e2e.py: requires a real DATABASE_URL, skips (not
fails) without one.

Scope: proves solution_search's OWN composition works against real
Postgres -- a real procedure AND a real task both findable through ONE
call to search_solutions, landing in the blended list with the right
`type` tag each, and the REST route wiring returning the same shape.
Does not re-prove domain_search's/applicability's/retrieval's own
correctness (already covered live by their own e2e files).
"""
from __future__ import annotations

import asyncio
import os

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


def _vec(seed: float) -> list[float]:
    return [seed] * 1024


class FakeEmbedder:
    """Deterministic per-call vector, keyed on the input text -- same
    idiom test_domain_search_e2e.py's own FakeEmbedder uses."""

    def __init__(self, by_text: dict[str, list[float]], default=None):
        self._by_text = by_text
        self._default = default or [0.5] * 1024

    async def embed_one(self, text, input_type="query"):
        for key, vec in self._by_text.items():
            if key in text:
                return vec
        return self._default

    def embedding_model_id(self) -> str:
        return "fake:test_solution_search_e2e"


def _run(coro):
    return asyncio.run(coro)


async def _cleanup(pool, prefix: str) -> None:
    await pool.execute(
        "DELETE FROM edges WHERE source_id IN (SELECT id FROM knowledge_nodes WHERE name LIKE $1) "
        "OR source_id IN (SELECT id FROM task_nodes WHERE name LIKE $1)",
        f"{prefix}%",
    )
    await pool.execute(
        "UPDATE evidence SET t_invalid = now() WHERE t_invalid IS NULL "
        "AND target_id IN (SELECT id FROM procedures WHERE name LIKE $1)",
        f"{prefix}%",
    )
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM task_nodes WHERE name LIKE $1", f"{prefix}%")


async def _make_verified_procedure(pool, name: str, **kwargs) -> dict:
    from app.services.procedures import (
        MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
        MIN_SUCCESSES_FOR_VERIFIED,
        capture_procedure,
        record_execution_outcome,
    )

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


def test_search_solutions_blends_a_real_procedure_and_a_real_task():
    async def _run_test():
        from app.db.session import create_pool
        from app.services.access import AccessScope
        from app.services.embeddings import to_pgvector
        from app.services.solution_search import search_solutions

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = "solsrch-test-basic"
        try:
            await _cleanup(pool, prefix)
            proc_vec, task_vec = _vec(0.7101), _vec(0.7102)

            proc_name = f"{prefix}-procedure roll out the release"
            made = await _make_verified_procedure(pool, proc_name, embedding=proc_vec)

            task_row = await pool.fetchrow(
                "INSERT INTO task_nodes (name, description, skill_ref, embedding) "
                "VALUES ($1, 'a real task', $2, $3::vector) RETURNING id",
                f"{prefix}-task roll out the release checklist", f"{prefix}-skill", to_pgvector(task_vec),
            )
            task_id = str(task_row["id"])

            embedder = FakeEmbedder({
                "roll out the release": proc_vec,
                "release checklist": task_vec,
            })

            # Real-corpus live DB: `limit` is the size of the final blended
            # list. This shared DB carries hundreds of other procedures/tasks;
            # a small `limit` can push the two rows THIS test seeded (each an
            # exact-vector similarity match via the FakeEmbedder) off the end
            # of the blended list, which would surface as a `next(...)` /
            # set-equality failure that has nothing to do with the blend
            # logic under test. Widened so both seeded rows survive into the
            # list; every assertion below is unchanged.
            result = await search_solutions(
                pool, f"{prefix} roll out the release checklist",
                scope=AccessScope.unrestricted(), embedder=embedder, limit=500,
            )

            types_present = {hit["type"] for hit in result["results"]}
            assert types_present == {"procedure", "task"}, f"expected both types, got: {result}"

            proc_hit = next(h for h in result["results"] if h["type"] == "procedure" and h["id"] == made["id"])
            task_hit = next(h for h in result["results"] if h["type"] == "task" and h["id"] == task_id)

            from app.services.procedure_display import build_display_name

            assert proc_hit["applicable"] is True
            # `capture_procedure` auto-derives `display_name` from `name`
            # (build_display_name) whenever the caller doesn't supply one
            # explicitly, and `_base_result` prefers display_name for a
            # procedure's title -- assert against the real function's own
            # output, not a hand-duplicated title-casing rule.
            assert proc_hit["title"] == build_display_name({"name": proc_name})
            assert task_hit["applicable"] is None
            assert task_hit["title"] == f"{prefix}-task roll out the release checklist"

            assert result["interleave_strategy"] == "round_robin_by_rank_position"
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    _run(_run_test())


def test_search_solutions_hydrates_top_procedure_hit_with_real_capability():
    async def _run_test():
        from app.db.session import create_pool
        from app.services.access import AccessScope
        from app.services.solution_search import search_solutions

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = "solsrch-test-hydrate"
        try:
            await _cleanup(pool, prefix)
            proc_vec = _vec(0.7201)
            proc_name = f"{prefix}-procedure restart the fleet"
            made = await _make_verified_procedure(pool, proc_name, embedding=proc_vec)

            embedder = FakeEmbedder({"restart the fleet": proc_vec})
            result = await search_solutions(
                pool, f"{prefix} restart the fleet",
                scope=AccessScope.unrestricted(), embedder=embedder, limit=5,
            )

            proc_hit = next(h for h in result["results"] if h["type"] == "procedure" and h["id"] == made["id"])
            assert proc_hit["capability"] is not None
            assert proc_hit["capability"]["evidence_count"] > 0
            assert proc_hit["capability"]["success_count"] > 0
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    _run(_run_test())


def test_rest_solutions_search_route_end_to_end_against_real_db(monkeypatch):
    import functools

    import httpx
    from fastapi import FastAPI

    import app.api.solutions as solutions_api

    async def _run_test():
        from app.db.session import create_pool
        from app.services.solution_search import search_solutions

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = "solsrch-test-restapi"
        try:
            await _cleanup(pool, prefix)
            proc_vec = _vec(0.7301)
            proc_name = f"{prefix}-procedure drain the queue"
            await _make_verified_procedure(pool, proc_name, embedding=proc_vec)

            fake_embedder = FakeEmbedder({"drain the queue": proc_vec})
            monkeypatch.setattr(
                solutions_api, "search_solutions",
                functools.partial(search_solutions, embedder=fake_embedder),
            )

            app = FastAPI()
            app.state.pool = pool
            app.include_router(solutions_api.router)

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # limit=100 (the route's max): real-corpus live DB, keep the
                # seeded exact-match procedure inside the returned blended
                # list rather than risk it falling off a small default page.
                resp = await client.get("/v1/solutions/search", params={
                    "q": f"{prefix} drain the queue",
                    "limit": 100,
                })
                from app.services.procedure_display import build_display_name

                assert resp.status_code == 200
                body = resp.json()
                names = [hit["title"] for hit in body["results"]]
                # Same display-name derivation as the in-process test above
                # -- the REST route serializes through the same _base_result
                # path, which prefers display_name over the raw name.
                assert build_display_name({"name": proc_name}) in names
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    _run(_run_test())
