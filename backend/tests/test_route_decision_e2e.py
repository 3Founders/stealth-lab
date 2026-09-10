"""
MCP hardening B1/B2, live-DB half: `decide_route()`'s integration with
the real applicability cascade, `persist_route_decision`/`get_route_decision`
round-tripping through migration 50's `route_decisions` table, and
`find_best_way`'s new needs_clarification short-circuit (does NOT
silently fall through to a real sandboxed tier-2 run when the single
best-matching procedure is blocked only on an UNKNOWN precondition).

Same pattern as every other `*_e2e.py` file: skips without a real
DATABASE_URL, self-cleaning by name prefix.
"""
import asyncio
import json
import os
import tempfile
from uuid import uuid4

import pytest

import app.mcp_server.server as srv
from app.db.session import create_pool
from app.services.embeddings import Embedder
from app.services.procedures import (
    MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
    MIN_SUCCESSES_FOR_VERIFIED,
    approve_procedure,
    capture_procedure,
    record_execution_outcome,
)
from app.services.route_decision import (
    classify_precondition_gap,
    decide_route,
    get_route_decision,
    persist_route_decision,
)

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


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM procedures WHERE name LIKE $1 "
        "AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        f"{name_prefix}%",
    )
    # A row this test made is_engineering_fixture=false (see
    # _make_verified_approved) can survive the DELETE above if it got
    # referenced by a frozen execution_plans row -- restore the correct
    # flag on any such survivor rather than leaving it permanently
    # mis-tagged as "real" in the shared corpus (confirmed live: exactly
    # this leftover state once leaked into an unrelated query's tier-1
    # match in this same test session).
    await pool.execute(
        "UPDATE procedures SET is_engineering_fixture = true WHERE name LIKE $1",
        f"{name_prefix}%",
    )
    await pool.execute("DELETE FROM route_decisions WHERE task_description LIKE $1", f"%{name_prefix}%")


async def _make_verified_approved(pool, name: str, **kwargs) -> dict:
    kwargs.setdefault("goal", name)
    # capture_procedure() defaults embedding_model_id to
    # settings.embedding_model (a config default, "voyage-3-large")
    # whenever it is omitted -- NOT to whichever embedder actually
    # produced `embedding=vec` below. Every caller here embeds via a
    # real Embedder() (currently Gemini in this environment), so an
    # omitted embedding_model_id would silently tag the row with the
    # wrong model id and make it invisible to any embedding_model_id-
    # scoped query (find_applicable_procedures/diagnose_candidates both
    # pass one) -- confirmed live: a near-exact-match test procedure
    # returned zero embedding-similarity rows until this was fixed.
    if "embedding" in kwargs and "embedding_model_id" not in kwargs:
        kwargs["embedding_model_id"] = Embedder().embedding_model_id()
    result = await capture_procedure(
        pool, name=name, provenance="system_pending_review",
        scope_type="global", **kwargs,
    )
    row_id = result["id"]
    # TEST-ONLY WORKAROUND for a confirmed, separate, live-DB schema-drift
    # bug (flagged as its own follow-up, not fixed here): this database's
    # procedures.is_engineering_fixture column default is `true` (should
    # be `false` per db/45's own committed intent), and capture_procedure()
    # never sets it explicitly -- so every row from this helper would
    # otherwise be silently excluded from applicability.py's
    # _CANDIDATE_BASE_WHERE, making it invisible to diagnose_candidates/
    # find_applicable_procedures regardless of how well it matches. This
    # UPDATE exists ONLY so these route_decision tests can exercise a
    # genuinely non-fixture candidate; it does not touch or fix the drift.
    await pool.execute("UPDATE procedures SET is_engineering_fixture = false WHERE id = $1", row_id)
    for i in range(MIN_SUCCESSES_FOR_VERIFIED):
        await record_execution_outcome(
            pool, procedure_row_id=row_id, success=True,
            context_key=f"ctx-{i % (MIN_DISTINCT_CONTEXTS_FOR_VERIFIED + 1)}",
        )
    await approve_procedure(pool, procedure_row_id=row_id, approved_by="tester")
    return result


def test_persist_and_get_route_decision_roundtrip():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            from app.services.route_decision import RouteDecision
            decision = RouteDecision(
                route="assist", reason="roundtrip probe", intent="assist",
                task_description="route-decision-roundtrip-probe", mode="auto",
                confidence=0.87, failed_constraints=["temporal_validity"],
                decision_critical_unknowns=[{"subject": "x", "predicate": "y", "object": "z"}],
                environment={"language": "python"}, authorization_detail={"authorized": True},
            )
            decision_id = await persist_route_decision(pool, decision)
            fetched = await get_route_decision(pool, decision_id)
            assert fetched is not None
            assert fetched["route"] == "assist"
            assert fetched["reason"] == "roundtrip probe"
            assert fetched["confidence"] == pytest.approx(0.87)
            # The pool's registered jsonb codec (app/db/session.py) already
            # decodes these to real Python objects -- NOT strings needing
            # a further json.loads() (that used to accidentally "work" only
            # because persist_route_decision double-encoded on write; fixed
            # to pass raw objects, so this must assert the real, decoded
            # shape now, not paper over the old bug from the read side).
            assert fetched["failed_constraints"] == ["temporal_validity"]
            assert fetched["decision_critical_unknowns"] == [
                {"subject": "x", "predicate": "y", "object": "z"}
            ]
            assert fetched["environment"] == {"language": "python"}
            assert fetched["authorization_detail"] == {"authorized": True}

            missing = await get_route_decision(pool, str(uuid4()))
            assert missing is None
        finally:
            await pool.execute(
                "DELETE FROM route_decisions WHERE task_description = $1",
                "route-decision-roundtrip-probe",
            )
            await pool.close()

    asyncio.run(_run())


def test_decide_route_refused_when_not_authorized():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            decision = await decide_route(
                pool, task_description="anything", mode="auto", repo_path=None,
                authorized=False, authorization_detail={"reason": "no workspace"},
                goal_embedding=None,
            )
            assert decision.route == "refused"
            assert decision.authorization_detail == {"reason": "no workspace"}
        finally:
            await pool.close()

    asyncio.run(_run())


def test_decide_route_execution_ready_when_applicable_and_intent_execute():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name_prefix = f"proc-test-routedec-{run_id}"
        try:
            embedder = Embedder()
            goal_text = f"fix the flaky retry logic in the queue worker ({run_id})"
            vec = await embedder.embed_one(goal_text, input_type="document")
            await _make_verified_approved(
                pool, name_prefix, goal=goal_text, embedding=vec,
                steps=[{"order": 0, "goal": "patch retry logic"}],
            )

            query_vec = await embedder.embed_one(goal_text, input_type="query")
            decision = await decide_route(
                pool, task_description=goal_text, mode="auto", repo_path="/tmp/some/repo",
                authorized=True, authorization_detail={},
                goal_embedding=query_vec, embedding_model_id=embedder.embedding_model_id(),
                goal_text=goal_text,
            )
            assert decision.intent == "execute"
            assert decision.route == "execution_ready"
            assert decision.applicable is True
            assert decision.procedure_row_id is not None
        finally:
            await _cleanup(pool, name_prefix)
            await pool.close()

    asyncio.run(_run())


def test_decide_route_runs_the_full_b1_pipeline_including_claims_and_implementations():
    """B1's own pipeline text: 'retrieve Procedures -> retrieve relevant
    Claims -> evaluate applicability -> resolve candidate Implementations
    -> determine missing decision-critical facts -> choose route'. Both
    retrieval steps must actually run and return REAL data (never
    fabricated) -- this proves it with a real Claim and a real
    Procedure<->Implementation binding, not just an empty-list default."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name_prefix = f"proc-test-routedec-pipeline-{run_id}"
        claim_subject = f"project:routedec-pipeline-claim-{run_id}"
        impl_id = None
        try:
            from app.execution import implementation_registry
            from app.services.claims import capture_claim
            from app.services.procedure_implementation_bindings import link_implementation

            embedder = Embedder()
            goal_text = f"rotate the deploy credentials for pipeline probe {run_id}"
            vec = await embedder.embed_one(goal_text, input_type="document")
            procedure = await _make_verified_approved(
                pool, name_prefix, goal=goal_text, embedding=vec,
                steps=[{"order": 0, "goal": "rotate the credential"}],
            )

            await pool.execute("INSERT INTO task_nodes (name, skill_ref) VALUES ('t', $1)", claim_subject)
            claim_id = await capture_claim(
                pool, statement=f"the deploy credential store is reachable for pipeline probe {run_id}",
                task_ids=[claim_subject], subject=claim_subject,
                predicate="reachability", object="reachable", claim_type="fact",
                epistemic_status="observed", created_by="tester", scope_type="global", embedder=embedder,
            )
            assert claim_id is not None

            impl = await implementation_registry.register(
                pool, name=f"routedec-pipeline-impl-{run_id}", kind="tool",
                provider="routedec-pipeline-e2e", created_by="tester",
            )
            impl_id = impl["id"]
            await link_implementation(
                pool, procedure_id=procedure["procedure_id"], implementation_id=impl_id,
                role="primary", created_by="tester",
            )
            await implementation_registry.activate(pool, impl_id)
            await pool.execute(
                "UPDATE procedure_implementations SET status='active' "
                "WHERE procedure_id=$1 AND implementation_id=$2",
                procedure["procedure_id"], impl_id,
            )

            query_vec = await embedder.embed_one(goal_text, input_type="query")
            decision = await decide_route(
                pool, task_description=goal_text, mode="auto", repo_path="/tmp/some/repo",
                authorized=True, authorization_detail={},
                goal_embedding=query_vec, embedding_model_id=embedder.embedding_model_id(),
                goal_text=goal_text,
            )
            assert decision.route == "execution_ready"
            assert decision.relevant_claim_refs, "must retrieve the real claim it was just given"
            assert any(r["claim_id"] == claim_id for r in decision.relevant_claim_refs)
            assert decision.implementation_candidates, "must resolve the real binding it was just given"
            assert any(str(c["implementation_id"]) == impl_id for c in decision.implementation_candidates)
        finally:
            if impl_id is not None:
                await pool.execute("DELETE FROM procedure_implementations WHERE implementation_id=$1", impl_id)
                await pool.execute("DELETE FROM implementations WHERE id=$1", impl_id)
            await pool.execute(
                "DELETE FROM knowledge_nodes WHERE node_type = 'claim' AND properties->>'subject' = $1",
                claim_subject,
            )
            await pool.execute("DELETE FROM task_nodes WHERE skill_ref = $1", claim_subject)
            await _cleanup(pool, name_prefix)
            await pool.close()

    asyncio.run(_run())


def test_decide_route_assist_when_applicable_and_intent_assist_never_requires_confirmation():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name_prefix = f"proc-test-routedec-assist-{run_id}"
        try:
            embedder = Embedder()
            goal_text = f"how should I structure the retry/backoff logic ({run_id})"
            vec = await embedder.embed_one(goal_text, input_type="document")
            await _make_verified_approved(
                pool, name_prefix, goal=goal_text, embedding=vec,
                steps=[{"order": 0, "goal": "use exponential backoff"}],
            )

            query_vec = await embedder.embed_one(goal_text, input_type="query")
            decision = await decide_route(
                pool, task_description=goal_text, mode="auto", repo_path=None,
                authorized=True, authorization_detail={},
                goal_embedding=query_vec, embedding_model_id=embedder.embedding_model_id(),
                goal_text=goal_text,
            )
            assert decision.intent == "assist"
            assert decision.route == "assist"
            assert decision.requires_confirmation is False
        finally:
            await _cleanup(pool, name_prefix)
            await pool.close()

    asyncio.run(_run())


def test_classify_precondition_gap_unknown_vs_false():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        from datetime import datetime, timezone
        try:
            run_id = uuid4().hex[:8]
            never_asserted_subject = f"project:route-decision-gap-probe-{run_id}"
            gap = await classify_precondition_gap(
                pool, subject=never_asserted_subject, as_of=datetime.now(timezone.utc),
                access_scope=None,
            )
            assert gap == "unknown"
        finally:
            await pool.close()

    asyncio.run(_run())


def test_find_best_way_needs_clarification_on_unknown_decision_critical_precondition(monkeypatch):
    """
    The core new B1 behavior: a procedure whose ONLY failing hard
    constraint is a precondition with NO live claim anywhere for its
    subject must route to needs_clarification, and find_best_way must
    return that structured answer instead of silently falling through
    to a real sandboxed tier-2 run.
    """
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name_prefix = f"proc-test-routedec-needsclar-{run_id}"
        never_asserted_subject = f"project:needs-clarification-probe-{run_id}"
        try:
            embedder = Embedder()
            goal_text = f"deploy the payment service to the canary cluster ({run_id})"
            vec = await embedder.embed_one(goal_text, input_type="document")
            await _make_verified_approved(
                pool, name_prefix, goal=goal_text, embedding=vec,
                steps=[{"order": 0, "goal": "run the canary deploy"}],
                preconditions=[
                    {"subject": never_asserted_subject, "predicate": "canary_quota", "object": "available"},
                ],
            )

            def _raise_if_constructed(*args, **kwargs):
                raise AssertionError(
                    "needs_clarification must never construct an LLM client / run tier 2"
                )
            original_openai = srv.OpenAI
            srv.OpenAI = _raise_if_constructed

            with tempfile.TemporaryDirectory() as repo_dir:
                try:
                    ctx = _FakeContext(pool)
                    result = await srv.find_best_way(
                        task_description=goal_text, ctx=ctx, mode="auto",
                        repo_path=repo_dir,
                    )
                finally:
                    srv.OpenAI = original_openai

            payload = json.loads(result)
            assert payload["route"] == "needs_clarification"
            assert payload["blocking_unknowns"], "must name the specific unknown precondition"
            assert payload["blocking_unknowns"][0]["subject"] == never_asserted_subject
            assert "route_decision_id" in payload

            persisted = await get_route_decision(pool, payload["route_decision_id"])
            assert persisted is not None
            assert persisted["route"] == "needs_clarification"
        finally:
            await _cleanup(pool, name_prefix)
            await pool.close()

    asyncio.run(_run())
