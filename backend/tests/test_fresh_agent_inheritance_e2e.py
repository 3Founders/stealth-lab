"""
THE definitive local-learning golden E2E: Agent A learns something inside
one real local MCP-execution run, Agent A's process/session is discarded,
and a completely fresh Agent B -- new run, new pool, no shared Python
state, no manually pre-seeded Claim/Procedure -- retrieves what Agent A
learned, with real provenance back to Agent A's Episode, and a third,
unrelated owner sees none of it.

Real, live-database test: requires DATABASE_URL, skips without one (same
convention as every other *_e2e.py file here). Uses the same
`_plan_chain`/`start_run`/`execute_run` fixture shape
test_durable_run_e2e.py established. The Claim-producing LLM pass is
exercised against a scripted fake client (see test_local_episode_learning.py
for why a real grounded_hybrid_v1 procedure-extraction call is NOT faked
here -- this test's Claim/retrieval assertions do not depend on it).
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
from app.services.access import AccessScope, visibility_predicate  # noqa: E402
from app.services.procedures import capture_procedure  # noqa: E402
from app.utils.ids import uuid7  # noqa: E402

DEPS_LINEAR = {0: []}


class _FakeClaimClient:
    """Scripted stand-in for the OpenAI-compatible client
    claim_extraction.py's extract_claim_candidates() calls -- same fixture
    as test_local_episode_learning.py's own (duplicated rather than
    imported across test modules, since backend/tests/ has no __init__.py
    and is not a stable import target)."""

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


class _FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.02] * 1024


async def _new_run(pool, *, owner: str, scope_entity_id: str, goal: str):
    res = await capture_procedure(
        pool, name=f"fresh-agent-{uuid.uuid4().hex[:8]}", goal=goal,
        steps=[{"order": 0, "goal": goal}],
        provenance="prior_library", scope_type="global", created_by="fresh_agent_e2e",
        embedding=await _FakeEmbedder().embed_one("x"),
    )
    proc_id, row_id = res["procedure_id"], res["id"]
    async with pool.acquire() as c:
        pv = await c.fetchval("SELECT version FROM procedures WHERE id=$1", row_id)
        plan_id = await c.fetchval(
            "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
            " task_description, procedure_content_hash, content_hash, scope_type) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
            str(uuid7()), proc_id, pv, row_id, "fresh-agent-e2e",
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

    async def run_node(order: int, attempt: int) -> dict:
        return {"order": order, "attempt": attempt, "ok": True}

    result = await execute_run(pool, run_id, deps=DEPS_LINEAR, run_node=run_node, worker_id="w1")
    assert result["status"] == "succeeded", result
    return row_id, run_id


async def _cleanup(pool, *, row_ids: list[str], run_ids: list[str]) -> None:
    for run_id in run_ids:
        # `evidence` is append-only (real DB trigger, Band 1.9a invariant
        # #19) -- tombstone via t_invalid, never DELETE. knowledge_nodes/
        # episode_links MUST be handled while episode_links still exists
        # (both join through it); episode_links itself is deleted LAST.
        await pool.execute(
            "UPDATE evidence SET t_invalid = now() WHERE t_invalid IS NULL AND target_id IN "
            "(SELECT target_id FROM episode_links el JOIN episodes ep ON ep.id = el.episode_id "
            " WHERE ep.execution_run_id = $1)",
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
        await pool.execute("DELETE FROM episodes WHERE execution_run_id = $1", run_id)
        await pool.execute("DELETE FROM execution_runs WHERE id = $1", run_id)
    await pool.execute(
        "DELETE FROM observations WHERE id NOT IN (SELECT observation_id FROM observation_events)",
    )
    for row_id in row_ids:
        await pool.execute(
            "DELETE FROM procedures WHERE id=$1 AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
            row_id,
        )


@pytest.mark.asyncio
async def test_fresh_agent_b_inherits_what_agent_a_learned():
    owner = f"fresh-agent-owner-{uuid.uuid4().hex[:8]}"
    stranger = f"fresh-agent-stranger-{uuid.uuid4().hex[:8]}"
    repo_scope = f"repo-fresh-{uuid.uuid4().hex[:8]}"
    goal = f"fresh-agent-inheritance goal {uuid.uuid4().hex[:8]}"
    # Must match block_index 0's real rendered text exactly --
    # _render_local_episode_text() always emits "# Local execution
    # episode" as the first (heading) block, and claim_extraction.py's
    # grounding check requires this quote to be a real substring of the
    # block the candidate cites (source_block_index=0 below).
    fake_quote = "Local execution episode"

    row_ids: list[str] = []
    run_ids: list[str] = []

    # =========================== RUN 1 -- AGENT A ===========================
    pool_a = await create_pool(statement_cache_size=0)
    try:
        # 1. Target knowledge does not exist yet for this fresh, unique
        # goal/scope -- nothing pre-seeded, nothing to collide with.
        pre_existing = await pool_a.fetchval(
            "SELECT count(*) FROM knowledge_nodes WHERE properties->>'source_quote' = $1", fake_quote,
        )
        assert pre_existing == 0

        row_id_a, run_id_a = await _new_run(pool_a, owner=owner, scope_entity_id=repo_scope, goal=goal)
        row_ids.append(row_id_a)
        run_ids.append(run_id_a)

        episode_id_a = await pool_a.fetchval(
            "SELECT id FROM episodes WHERE execution_run_id = $1", run_id_a,
        )
        assert episode_id_a is not None

        fake_client = _FakeClaimClient(quote=fake_quote)
        orig_extraction_client = ingestion_jobs._extraction_client
        ingestion_jobs._extraction_client = lambda: fake_client
        try:
            await ingestion_jobs.handle_consolidate_local_episode(
                pool_a, {"episode_id": str(episode_id_a), "execution_run_id": run_id_a},
            )
        finally:
            ingestion_jobs._extraction_client = orig_extraction_client

        claim_id = await pool_a.fetchval(
            "SELECT target_id FROM episode_links WHERE episode_id = $1 AND target_table = 'knowledge_nodes'",
            episode_id_a,
        )
        assert claim_id is not None, "Agent A's episode must have produced a real private Claim"

        claim_row = await pool_a.fetchrow(
            "SELECT visibility, owner_id, properties FROM knowledge_nodes WHERE id = $1", claim_id,
        )
        assert claim_row["visibility"] == "private"
        assert claim_row["owner_id"] == owner
        props = claim_row["properties"]
        props = json.loads(props) if isinstance(props, str) else props
        assert props.get("source_quote") == fake_quote

        evidence_count = await pool_a.fetchval(
            "SELECT count(*) FROM evidence WHERE target_type = 'claim' AND target_id = $1", claim_id,
        )
        assert evidence_count >= 1, "Agent A's Claim must carry real execution-result Evidence"
    finally:
        # =============== "TERMINATE AGENT A" ===============
        # Real process boundary: close this pool, drop every Python
        # reference from Agent A's phase. Nothing below reuses them.
        await pool_a.close()

    # =========================== RUN 2 -- AGENT B ===========================
    # Fresh pool (a real new connection, not reused from Agent A), fresh
    # run, same owner/repository scope, NOTHING manually pre-seeded.
    pool_b = await create_pool(statement_cache_size=0)
    try:
        row_id_b, run_id_b = await _new_run(
            pool_b, owner=owner, scope_entity_id=repo_scope, goal="a related but distinct goal",
        )
        row_ids.append(row_id_b)
        run_ids.append(run_id_b)

        from app.services.relevant_claims import get_relevant_claims

        refs = await get_relevant_claims(
            pool_b, goal=goal, top_k=10, access_scope=AccessScope.for_user(owner),
        )
        matched = [r for r in refs if fake_quote.lower() in (r.get("statement") or "").lower()
                   or "succeeding is evidence" in (r.get("statement") or "").lower()]
        assert matched, (
            f"Agent B (same owner, fresh run/pool) must retrieve Agent A's private Claim via "
            f"get_relevant_claims; got {refs!r}"
        )

        # 25: no global leakage -- an unrelated stranger, same goal query,
        # must retrieve nothing from this private Claim.
        refs_stranger = await get_relevant_claims(
            pool_b, goal=goal, top_k=10, access_scope=AccessScope.for_user(stranger),
        )
        leaked = [r for r in refs_stranger if r.get("claim_id") == str(claim_id)]
        assert not leaked, "a private Claim must never be retrievable by an unrelated owner"

        # Provenance: the Claim Agent B retrieved traces back to Agent A's
        # real Episode via episode_links, and that Episode's own
        # execution_run_id is Agent A's real run -- never Agent B's.
        provenance_run = await pool_b.fetchval(
            "SELECT ep.execution_run_id FROM episode_links el "
            "JOIN episodes ep ON ep.id = el.episode_id "
            "WHERE el.target_id = $1 AND el.target_table = 'knowledge_nodes'",
            claim_id,
        )
        assert str(provenance_run) == run_id_a
        assert str(provenance_run) != run_id_b
    finally:
        await pool_b.close()

    # =========================== cleanup ===========================
    pool_c = await create_pool(statement_cache_size=0)
    try:
        await _cleanup(pool_c, row_ids=row_ids, run_ids=run_ids)
    finally:
        await pool_c.close()
