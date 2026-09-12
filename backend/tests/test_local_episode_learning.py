"""
Real, live-database proving tests for the local MCP-execution learning
wiring: app/execution/episode.py, durable_run.py's Episode-open/close
hooks, and app.services.ingestion_jobs::handle_consolidate_local_episode.

Same pattern as test_durable_run_e2e.py / test_episode_evidence_e2e.py:
requires a real DATABASE_URL, skips (not fails) without one. Uses the
SAME `_plan_chain`/`start_run`/`execute_run` fixture shape
test_durable_run_e2e.py already established -- a real procedure +
execution_plan + task_graph + execution_run driven to a real terminal
state, not a hand-rolled fixture shape.

Claim-candidate assertions that need a real LLM call are exercised
against a scripted fake client (same injection pattern
observations.py::extract_model_observation's own tests use) rather than
a real General Compute key, which this sandbox does not have configured.
Procedure-candidate assertions are conditioned on a real extraction
client being configured (`_extraction_client() is not None`) and skipped
otherwise -- grounded_hybrid_v1's own structured-output shape is not
something a scripted fake can honestly stand in for without risking a
false pass.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="e2e: DATABASE_URL unset")

from app.db.session import create_pool  # noqa: E402
from app.execution.durable_run import execute_run, start_run  # noqa: E402
from app.services import ingestion_jobs  # noqa: E402
from app.services.procedures import capture_procedure  # noqa: E402
from app.utils.ids import uuid7  # noqa: E402

DEPS_LINEAR = {0: []}


def _meta(row) -> dict:
    m = row["metadata"]
    return json.loads(m) if isinstance(m, str) else dict(m or {})


class _FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.01] * 1024


class _FakeClaimClient:
    """Scripted stand-in for the OpenAI-compatible client
    claim_extraction.py's extract_claim_candidates() calls. Always
    returns one well-formed, grounded candidate regardless of the
    prompt -- sufficient to exercise persist_claim_candidate()'s real
    write path without a real network call."""

    class _Choice:
        def __init__(self, content: str):
            self.message = type("M", (), {"content": content})()

    class _Response:
        def __init__(self, content: str):
            self.choices = [_FakeClaimClient._Choice(content)]

    def __init__(self, quote: str):
        self._quote = quote
        self.chat = self

    @property
    def completions(self):
        return self

    def create(self, *, model, messages, temperature, max_tokens):
        payload = {
            "claims": [{
                "statement": "Local execution nodes succeeding is evidence the plan was well-formed",
                "claim_type": "fact",
                "scope": "repo_local",
                "conditions": [],
                "source_block_index": 0,
                "source_quote": self._quote,
                "confidence_of_extraction": 0.9,
                "suggested_procedure_role": None,
                "rationale_for_extraction": "test fixture",
            }]
        }
        return _FakeClaimClient._Response(json.dumps(payload))


async def _plan_chain(pool, *, owner: str, scope_entity_id: str):
    # Goal text must be unique PER CALL, not just per test: it feeds
    # directly into handle_consolidate_local_episode's rendered episode
    # text, which extract_claim_candidates_cached() keys its cache on by
    # content hash. Two tests (or two runs of the same test) with
    # byte-identical goal/observation text would silently serve each
    # other's cached extraction result regardless of which client (real,
    # fake, or None) is active for that particular call -- a real,
    # correct production caching behavior that would otherwise make
    # these tests interfere with each other.
    goal = f"local episode learning probe {uuid.uuid4().hex[:12]}"
    res = await capture_procedure(
        pool, name=f"local-episode-test-{uuid.uuid4().hex[:8]}", goal=goal,
        steps=[{"order": 0, "goal": "do the one real thing"}],
        provenance="prior_library", scope_type="global", created_by="local_episode_test",
        embedding=await _FakeEmbedder().embed_one("x"),
    )
    proc_id, row_id = res["procedure_id"], res["id"]
    async with pool.acquire() as c:
        pv = await c.fetchval("SELECT version FROM procedures WHERE id=$1", row_id)
        plan_id = await c.fetchval(
            "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
            " task_description, procedure_content_hash, content_hash, scope_type) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
            str(uuid7()), proc_id, pv, row_id, "local-episode-test",
            f"pch-{uuid.uuid4().hex[:10]}", f"ch-{uuid.uuid4().hex[:10]}",
        )
        graph_id = await c.fetchval(
            "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
            "VALUES ($1,$2,$3,'[]'::jsonb) RETURNING id",
            str(uuid7()), plan_id, f"gh-{uuid.uuid4().hex[:10]}",
        )
    run_id = await start_run(
        pool, execution_plan_id=plan_id, task_graph_id=graph_id,
        procedure_id=proc_id, procedure_version=pv,
        node_orders=[0], deps=DEPS_LINEAR, max_attempts=1, created_by=owner,
        scope_type="repository", scope_entity_id=scope_entity_id,
    )
    return proc_id, row_id, run_id


async def _cleanup(pool, *, row_id, run_id) -> None:
    await pool.execute(
        "DELETE FROM claim_sources WHERE observation_id IN "
        "(SELECT id FROM observations WHERE id IN "
        " (SELECT observation_id FROM observation_events oe "
        "  JOIN execution_run_events ere ON ere.id = oe.execution_run_event_id "
        "  WHERE ere.execution_run_id = $1))",
        run_id,
    )
    # `evidence` is append-only (a real DB trigger, Band 1.9a invariant
    # #19 -- confirmed by running this cleanup against it) -- tombstone
    # via t_invalid, never DELETE. knowledge_nodes/episode_links MUST be
    # handled while episode_links still exists (both subqueries join
    # through it) -- episode_links itself is deleted LAST of the three.
    await pool.execute(
        "UPDATE evidence SET t_invalid = now() WHERE t_invalid IS NULL AND target_id IN "
        "(SELECT target_id FROM episode_links WHERE episode_id IN "
        " (SELECT id FROM episodes WHERE execution_run_id = $1))",
        run_id,
    )
    await pool.execute(
        "DELETE FROM knowledge_nodes WHERE id IN "
        "(SELECT target_id FROM episode_links el JOIN episodes ep ON ep.id = el.episode_id "
        " WHERE ep.execution_run_id = $1)",
        run_id,
    )
    await pool.execute(
        "DELETE FROM episode_links WHERE episode_id IN (SELECT id FROM episodes WHERE execution_run_id = $1)",
        run_id,
    )
    await pool.execute(
        "DELETE FROM observation_events WHERE execution_run_event_id IN "
        "(SELECT id FROM execution_run_events WHERE execution_run_id = $1)",
        run_id,
    )
    await pool.execute(
        "DELETE FROM observations WHERE id NOT IN (SELECT observation_id FROM observation_events)",
    )
    await pool.execute("DELETE FROM episodes WHERE execution_run_id = $1", run_id)
    await pool.execute("DELETE FROM execution_runs WHERE id = $1", run_id)
    await pool.execute(
        "DELETE FROM procedures WHERE id=$1 AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        row_id,
    )


async def _run_one_success_node(pool, run_id) -> None:
    async def run_node(order: int, attempt: int) -> dict:
        return {"order": order, "attempt": attempt, "ok": True}

    res = await execute_run(pool, run_id, deps=DEPS_LINEAR, run_node=run_node, worker_id="w1")
    assert res["status"] == "succeeded", res


@pytest.mark.asyncio
async def test_episode_opens_on_run_creation_and_is_idempotent():
    pool = await create_pool(statement_cache_size=0)
    owner = f"local-ep-owner-{uuid.uuid4().hex[:8]}"
    proc_id = row_id = run_id = None
    try:
        proc_id, row_id, run_id = await _plan_chain(pool, owner=owner, scope_entity_id="repo-a")
        episode = await pool.fetchrow(
            "SELECT id, episode_type, owner_id, visibility, scope_type, scope_entity_id, end_ts "
            "FROM episodes WHERE execution_run_id = $1", run_id,
        )
        assert episode is not None
        assert episode["episode_type"] == "execution"
        assert episode["owner_id"] == owner
        assert episode["visibility"] == "private"
        assert episode["scope_type"] == "repository"
        assert episode["scope_entity_id"] == "repo-a"
        assert episode["end_ts"] is None

        # Calling open_episode_for_run again for the same run must not
        # create a second row (partial unique index, migration 79).
        from app.execution.episode import open_episode_for_run

        async with pool.acquire() as conn:
            again = await open_episode_for_run(conn, run_id, created_by=owner)
        assert again == str(episode["id"])
        count = await pool.fetchval("SELECT count(*) FROM episodes WHERE execution_run_id = $1", run_id)
        assert count == 1
    finally:
        if run_id:
            await _cleanup(pool, row_id=row_id, run_id=run_id)
        await pool.close()


@pytest.mark.asyncio
async def test_episode_closes_once_on_success_and_enqueues_consolidation_once():
    pool = await create_pool(statement_cache_size=0)
    owner = f"local-ep-owner-{uuid.uuid4().hex[:8]}"
    proc_id = row_id = run_id = None
    try:
        proc_id, row_id, run_id = await _plan_chain(pool, owner=owner, scope_entity_id="repo-b")
        await _run_one_success_node(pool, run_id)

        episode = await pool.fetchrow(
            "SELECT id, end_ts, metadata FROM episodes WHERE execution_run_id = $1", run_id,
        )
        assert episode["end_ts"] is not None
        assert _meta(episode)["outcome"] == "success"

        jobs = await pool.fetch(
            "SELECT id FROM ingestion_jobs WHERE job_type = 'consolidate_local_episode' "
            "AND payload->>'execution_run_id' = $1", run_id,
        )
        assert len(jobs) == 1, "exactly one consolidation job, no duplicates"

        # Duplicate finalize (e.g. a caller re-invoking _finalize on an
        # already-terminal run) must not reopen/re-close the episode or
        # enqueue a second job. _persist_success_transition's own
        # `WHERE status IN (from_statuses)` already makes this a no-op at
        # the SQL level; this asserts the episode/job side stays put too.
        from app.execution.durable_run import _finalize

        await _finalize(pool, run_id)
        jobs_after = await pool.fetch(
            "SELECT id FROM ingestion_jobs WHERE job_type = 'consolidate_local_episode' "
            "AND payload->>'execution_run_id' = $1", run_id,
        )
        assert len(jobs_after) == 1
    finally:
        if run_id:
            await _cleanup(pool, row_id=row_id, run_id=run_id)
        await pool.close()


@pytest.mark.asyncio
async def test_consolidation_produces_observations_claim_and_evidence(monkeypatch):
    pool = await create_pool(statement_cache_size=0)
    owner = f"local-ep-owner-{uuid.uuid4().hex[:8]}"
    proc_id = row_id = run_id = None
    try:
        proc_id, row_id, run_id = await _plan_chain(pool, owner=owner, scope_entity_id="repo-c")
        await _run_one_success_node(pool, run_id)

        episode_id = await pool.fetchval("SELECT id FROM episodes WHERE execution_run_id = $1", run_id)
        run_events = await pool.fetch(
            "SELECT id FROM execution_run_events WHERE execution_run_id = $1 AND event_type = 'node_succeeded'",
            run_id,
        )
        assert len(run_events) == 1

        fake_client = _FakeClaimClient(quote="Local execution episode")
        monkeypatch.setattr(ingestion_jobs, "_extraction_client", lambda: fake_client)

        payload = {"episode_id": str(episode_id), "execution_run_id": run_id}
        await ingestion_jobs.handle_consolidate_local_episode(pool, payload)

        # Deterministic Observations: written by consolidation's step 1,
        # unconditionally, per meaningful event -- one per node_succeeded.
        obs_row = await pool.fetchrow(
            "SELECT o.id, o.visibility, o.owner_id FROM observations o "
            "JOIN observation_events oe ON oe.observation_id = o.id "
            "WHERE oe.execution_run_event_id = $1", run_events[0]["id"],
        )
        assert obs_row is not None
        assert obs_row["visibility"] == "private"
        assert obs_row["owner_id"] == owner

        episode_after = await pool.fetchrow("SELECT metadata FROM episodes WHERE id = $1", episode_id)
        assert _meta(episode_after).get("consolidated_at")

        claim = await pool.fetchrow(
            "SELECT k.id, k.visibility, k.owner_id FROM episode_links el "
            "JOIN knowledge_nodes k ON k.id = el.target_id AND el.target_table = 'knowledge_nodes' "
            "WHERE el.episode_id = $1", episode_id,
        )
        assert claim is not None, "consolidation with a real (fake) client must persist a Claim"
        assert claim["visibility"] == "private"
        assert claim["owner_id"] == owner

        evidence = await pool.fetchrow(
            "SELECT evidence_type, outcome_status, visibility, owner_id FROM evidence "
            "WHERE target_type = 'claim' AND target_id = $1", claim["id"],
        )
        assert evidence is not None
        assert evidence["evidence_type"] == "execution_result"
        assert evidence["outcome_status"] == "success"
        assert evidence["visibility"] == "private"
        assert evidence["owner_id"] == owner

        # Re-running consolidation for the same (already-consolidated)
        # episode must not create a second Claim.
        await ingestion_jobs.handle_consolidate_local_episode(pool, payload)
        claim_count = await pool.fetchval(
            "SELECT count(*) FROM episode_links WHERE episode_id = $1 AND target_table = 'knowledge_nodes'",
            episode_id,
        )
        assert claim_count == 1
    finally:
        if run_id:
            await _cleanup(pool, row_id=row_id, run_id=run_id)
        await pool.close()


@pytest.mark.asyncio
async def test_consolidation_without_llm_client_preserves_observations_and_fabricates_nothing(monkeypatch):
    """LLM unavailable (forced here via monkeypatch so this test is
    deterministic regardless of whether a real GENERAL_COMPUTE_API_KEY
    happens to be configured in the environment) -- _extraction_client()
    returns None, extract_claim_candidates degrades to [] (its own
    documented fail-closed contract), and NOTHING is fabricated:
    deterministic Observations still commit, no Claim/Procedure row
    appears, and the episode is still marked consolidated (there was
    nothing more to retry -- a genuine empty result, not a failure to
    distinguish from one at this layer)."""
    monkeypatch.setattr(ingestion_jobs, "_extraction_client", lambda: None)
    pool = await create_pool(statement_cache_size=0)
    owner = f"local-ep-owner-{uuid.uuid4().hex[:8]}"
    proc_id = row_id = run_id = None
    try:
        proc_id, row_id, run_id = await _plan_chain(pool, owner=owner, scope_entity_id="repo-d")
        await _run_one_success_node(pool, run_id)
        episode_id = await pool.fetchval("SELECT id FROM episodes WHERE execution_run_id = $1", run_id)

        assert ingestion_jobs._extraction_client() is None

        await ingestion_jobs.handle_consolidate_local_episode(
            pool, {"episode_id": str(episode_id), "execution_run_id": run_id},
        )

        obs_count = await pool.fetchval(
            "SELECT count(*) FROM observations o JOIN observation_events oe ON oe.observation_id = o.id "
            "JOIN execution_run_events ere ON ere.id = oe.execution_run_event_id "
            "WHERE ere.execution_run_id = $1", run_id,
        )
        assert obs_count >= 1

        claim_count = await pool.fetchval(
            "SELECT count(*) FROM episode_links WHERE episode_id = $1 AND target_table = 'knowledge_nodes'",
            episode_id,
        )
        assert claim_count == 0

        # LIVE rows only: extract_procedure() with no client degrades to
        # deterministic_v1, which DOES produce a row here (one node, one
        # observation clears its bar) -- but the abstention check right
        # after it in handle_consolidate_local_episode (capability_
        # statement == goal_text) immediately tombstones it (t_invalid
        # set, verification_state='retired'). The real assertion is "no
        # LIVE candidate survives", not "no row was ever momentarily
        # created" -- a retired row past the abstention check is the
        # system correctly refusing to leave a no-op candidate live, not
        # a fabricated Procedure.
        proc_count = await pool.fetchval(
            "SELECT count(*) FROM procedures WHERE source_episode_ids @> ARRAY[$1::uuid] AND t_invalid IS NULL",
            episode_id,
        )
        assert proc_count == 0
    finally:
        if run_id:
            await _cleanup(pool, row_id=row_id, run_id=run_id)
        await pool.close()


@pytest.mark.asyncio
async def test_private_claim_invisible_to_unrelated_owner(monkeypatch):
    pool = await create_pool(statement_cache_size=0)
    owner = f"local-ep-owner-{uuid.uuid4().hex[:8]}"
    stranger = f"local-ep-stranger-{uuid.uuid4().hex[:8]}"
    proc_id = row_id = run_id = None
    try:
        proc_id, row_id, run_id = await _plan_chain(pool, owner=owner, scope_entity_id="repo-e")
        await _run_one_success_node(pool, run_id)
        episode_id = await pool.fetchval("SELECT id FROM episodes WHERE execution_run_id = $1", run_id)

        fake_client = _FakeClaimClient(quote="Local execution episode")
        monkeypatch.setattr(ingestion_jobs, "_extraction_client", lambda: fake_client)
        await ingestion_jobs.handle_consolidate_local_episode(
            pool, {"episode_id": str(episode_id), "execution_run_id": run_id},
        )

        claim_id = await pool.fetchval(
            "SELECT target_id FROM episode_links WHERE episode_id = $1 AND target_table = 'knowledge_nodes'",
            episode_id,
        )
        assert claim_id is not None

        from app.services.access import AccessScope, visibility_predicate

        vis_sql, vis_params = visibility_predicate(AccessScope.for_user(stranger), param_index=2)
        visible_to_stranger = await pool.fetchval(
            f"SELECT id FROM knowledge_nodes WHERE id = $1 AND {vis_sql}", claim_id, *vis_params,
        )
        assert visible_to_stranger is None, "a private claim must not be visible to an unrelated owner"

        vis_sql2, vis_params2 = visibility_predicate(AccessScope.for_user(owner), param_index=2)
        visible_to_owner = await pool.fetchval(
            f"SELECT id FROM knowledge_nodes WHERE id = $1 AND {vis_sql2}", claim_id, *vis_params2,
        )
        assert visible_to_owner == claim_id
    finally:
        if run_id:
            await _cleanup(pool, row_id=row_id, run_id=run_id)
        await pool.close()
