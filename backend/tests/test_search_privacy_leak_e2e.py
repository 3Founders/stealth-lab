"""
P0 fix: `find_best_way`'s tier-1 automatic lookup and `search_procedures`
used to call `find_applicable_procedures(...)` with no `access_scope` at
all, which silently defaults to `AccessScope.unrestricted()`
(`applicability.py`'s own `access_scope or AccessScope.unrestricted()`)
-- every caller could find every OTHER caller's private procedures via
plain search, not just by-id fetch. Both call sites now pass
`access_scope=_caller_access_scope()`, the exact fix `inspect_problem`/
`inspect_evaluation`/etc already had for the identical bug class
(final-V1 eval Bug #8).

This proves: a private procedure owned by user A is invisible to
`search_procedures`/`find_best_way` called as user B or anonymously, and
stays visible to A themself.

Skips (never fails) without DATABASE_URL.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest

from app.db.session import create_pool
from app.services.authn import Actor, reset_current_actor, set_current_actor
from app.services.embeddings import Embedder
from app.services.procedures import capture_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- live-database search-privacy test"
)

_MARK = "test-search-privacy-e2e"


class _FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class _FakeContext:
    def __init__(self, pool):
        self.request_context = _FakeRequestContext(pool)


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM procedures WHERE name LIKE $1 "
        "AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        f"{name_prefix}%",
    )


def _as(actor_subject):
    """Context manager-ish helper: set the actor contextvar for the
    duration of a call, always reset."""
    class _Ctx:
        def __enter__(self):
            self._token = set_current_actor(Actor(subject=actor_subject) if actor_subject else None)
            return self

        def __exit__(self, *exc):
            reset_current_actor(self._token)
    return _Ctx()


def test_private_procedure_invisible_to_search_by_a_different_caller():
    async def _run():
        import app.mcp_server.server as srv

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        tag = uuid.uuid4().hex[:8]
        name = f"{_MARK}-{tag}"
        goal_text = f"{_MARK}: a private way to rotate secrets {tag}"
        try:
            embedder = Embedder()
            vec = await embedder.embed_one(goal_text, input_type="document")
            await capture_procedure(
                pool, name=name, goal=goal_text, provenance="system_pending_review",
                scope_type="global", visibility="private", owner_id="owner-a",
                steps=[{"order": 0, "goal": "rotate the key"}],
                embedding=vec, embedding_model_id=embedder.embedding_model_id(),
            )

            ctx = _FakeContext(pool)

            # anonymous caller: must not see owner-a's private procedure
            with _as(None):
                result = json.loads(await srv.search_procedures(
                    task=goal_text, ctx=ctx, require_verified=False,
                ))
            assert all(name not in r.get("name", "") for r in result), \
                "an anonymous caller must not find another user's private procedure"

            # a DIFFERENT authenticated user: must also not see it
            with _as("owner-b"):
                result = json.loads(await srv.search_procedures(
                    task=goal_text, ctx=ctx, require_verified=False,
                ))
            assert all(name not in r.get("name", "") for r in result), \
                "a different user must not find another user's private procedure"

            # the OWNER: must still see their own private procedure
            with _as("owner-a"):
                result = json.loads(await srv.search_procedures(
                    task=goal_text, ctx=ctx, require_verified=False,
                ))
            assert any(name == r.get("name") for r in result), \
                "the owner must still find their own private procedure"
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_find_best_way_tier1_does_not_leak_a_private_procedure():
    async def _run():
        import app.mcp_server.server as srv

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        tag = uuid.uuid4().hex[:8]
        name = f"{_MARK}-fbw-{tag}"
        goal_text = f"{_MARK}: privately provision the staging box {tag}"
        try:
            embedder = Embedder()
            vec = await embedder.embed_one(goal_text, input_type="document")
            captured = await capture_procedure(
                pool, name=name, goal=goal_text, provenance="system_pending_review",
                scope_type="global", visibility="private", owner_id="owner-a",
                steps=[{"order": 0, "goal": "provision it"}],
                embedding=vec, embedding_model_id=embedder.embedding_model_id(),
            )
            private_procedure_id = str(captured["procedure_id"])

            ctx = _FakeContext(pool)

            # a different user: whatever find_best_way's tier-1 lookup
            # matches (it may legitimately match an unrelated PUBLIC
            # procedure when allow_unverified_procedures=True widens the
            # pool -- that is a separate relevance question, not a privacy
            # one), it must never be owner-a's private procedure.
            with _as("owner-b"):
                plan_result = await srv.find_best_way(
                    task_description=goal_text, ctx=ctx, mode="plan_only",
                    allow_unverified_procedures=True,
                )
            assert private_procedure_id not in plan_result, (
                "find_best_way's automatic lookup must not match another user's "
                f"private procedure -- got: {plan_result[:400]}"
            )

            # the owner: must find their OWN private procedure specifically.
            with _as("owner-a"):
                plan_result = await srv.find_best_way(
                    task_description=goal_text, ctx=ctx, mode="plan_only",
                    allow_unverified_procedures=True,
                )
            assert private_procedure_id in plan_result, (
                f"the owner must still find their own private procedure -- got: {plan_result[:400]}"
            )
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())
