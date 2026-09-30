"""
Offline tests for the single contribution tool, `submit_way`: a signed-in user
submits a way, and its Goal when the Goal is new -- and nothing is stored when
something like it already exists. No DB, no model: the services it composes
(intent resolution, goal creation, the submission service) are faked, and the
duplicate refusal inside the submission service is tested on its own.
"""
from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace

import pytest

os.environ.pop("DATABASE_URL", None)

import app.mcp_server.server as srv  # noqa: E402
from app.economy import constants as econ  # noqa: E402
from app.economy import submissions as subs  # noqa: E402
from app.execution.intent_resolution import GoalCandidate, IntentResolution, NormalizedIntent  # noqa: E402
from app.services.access import AccessScope  # noqa: E402

STEPS = json.dumps(["Install docx", "Write the document and open it to check it renders"])
PRE = json.dumps([{"subject": "runtime", "predicate": "is", "value": "node"}])
OUT = json.dumps({"summary": "a .docx file exists and opens"})


def _run(coro):
    return asyncio.run(coro)


class _Ctx:
    request_context = SimpleNamespace(lifespan_context={"pool": object()})


def _cand(gid, name, score):
    return GoalCandidate(goal={"id": gid, "canonical_name": name}, score=score, lexical_overlap=0.5,
                         scope_match=0.5, status_score=1.0, fusion_position_score=1.0, rationale="")


@pytest.fixture
def world(monkeypatch):
    """A signed-in caller, a permissive rate limiter, and recorders for every write."""
    calls = {"goal_created": [], "submitted": [], "resolve": None, "rate_checked": 0}
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: AccessScope.for_user("user-1"))

    class _Limiter:
        def __init__(self, *a, **k):
            pass

        async def check_and_record(self, *a, **k):
            calls["rate_checked"] += 1

    monkeypatch.setattr("app.services.governance.RateLimiter", _Limiter)
    monkeypatch.setattr("app.services.embeddings.Embedder", lambda *a, **k: object())

    def set_resolution(outcome, selected=None, candidates=()):
        async def fake_resolve(pool, text, **kw):
            return IntentResolution(raw_input=text, outcome=outcome,
                                    normalized=NormalizedIntent(raw_input=text, outcome=text, used_fallback=True),
                                    candidates=list(candidates), selected_goal=selected)
        monkeypatch.setattr("app.execution.intent_resolution.resolve_intent", fake_resolve)

    async def fake_create_goal(pool, **kw):
        calls["goal_created"].append(kw)
        return {"outcome": "created", "goal": {"id": "G-new", "canonical_name": kw["canonical_name"], "created": True}}

    async def fake_submission(pool, **kw):
        calls["submitted"].append(kw)
        return {"id": "S-1", "goal_id": kw["goal_id"], "procedure_row_id": None, "status": "candidate",
                "status_reason": None, "layer1_result": {"issues": []}}

    monkeypatch.setattr("app.services.goals.create_goal_from_user", fake_create_goal)
    monkeypatch.setattr(subs, "create_procedure_submission", fake_submission)
    calls["set_resolution"] = set_resolution
    return calls


def _submit(**kw):
    return _run(srv.submit_way("Word export with docx-js", STEPS, "docx-js is maintained", PRE, OUT, _Ctx(), **kw))


def test_refused_without_a_signed_in_user(world, monkeypatch):
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: AccessScope.anonymous())
    out = _submit(goal="Export a Word document", goal_objective="a .docx opens")
    assert out.startswith("REFUSED: sign in")
    assert world["goal_created"] == [] and world["submitted"] == [] and world["rate_checked"] == 0


def test_exactly_one_way_to_name_the_goal(world):
    assert "exactly one of goal_id" in _submit()
    assert "exactly one of goal_id" in _submit(goal_id="G-1", goal="Export a Word document", goal_objective="x")
    assert "goal_objective" in _submit(goal="Export a Word document")
    assert world["submitted"] == [] and world["goal_created"] == []


def test_new_goal_is_created_only_when_nothing_like_it_exists(world):
    world["set_resolution"]("no_match")
    out = json.loads(_submit(goal="Export a Word document from Node", goal_objective="a .docx file opens"))
    assert out["outcome"] == "submitted" and out["goal"]["created"] is True and out["goal_id"] == "G-new"
    made = world["goal_created"][0]
    assert made["owner_id"] == "user-1" and made["allow_create_anyway"] is True
    assert made["rationale"] == "docx-js is maintained"          # goal_rationale defaults to the way's
    assert world["submitted"][0]["goal_id"] == "G-new"
    assert world["submitted"][0]["refuse_duplicate_at"] == econ.DUPLICATE_REVIEW_THRESHOLD
    assert world["submitted"][0]["actor_subject"] == "user-1"


def test_an_existing_goal_is_reused_not_recreated(world):
    world["set_resolution"]("resolved", selected={"id": "G-old", "canonical_name": "Export a Word document"})
    out = json.loads(_submit(goal="export a word doc", goal_objective="a .docx opens"))
    assert out["goal"] == {"goal_id": "G-old", "canonical_name": "Export a Word document", "created": False,
                           "matched": "existing"}
    assert world["goal_created"] == [] and world["submitted"][0]["goal_id"] == "G-old"


def test_close_goals_come_back_for_the_caller_to_pick_and_nothing_is_written(world):
    world["set_resolution"]("ambiguous", candidates=[_cand("G-a", "Export a Word document", 0.71),
                                                     _cand("G-b", "Export a PDF document", 0.69)])
    out = json.loads(_submit(goal="export a document", goal_objective="a file opens"))
    assert out["outcome"] == "goal_ambiguous" and [c["goal_id"] for c in out["candidates"]] == ["G-a", "G-b"]
    assert world["goal_created"] == [] and world["submitted"] == []


def test_a_way_like_an_existing_one_is_not_stored(world, monkeypatch):
    async def dup(pool, **kw):
        raise subs.DuplicateWay({"best_match_id": "P-9", "best_match_kind": "procedure", "score": 0.91}, 0.85)

    monkeypatch.setattr(subs, "create_procedure_submission", dup)
    out = json.loads(_submit(goal_id="G-1"))
    assert out["outcome"] == "duplicate_way"
    assert out["existing"] == {"id": "P-9", "kind": "procedure", "similarity": 0.91}


def test_goal_id_path_still_works(world):
    out = json.loads(_submit(goal_id="G-1"))
    assert out["outcome"] == "submitted" and out["goal_id"] == "G-1" and out["goal"]["created"] is False
    assert world["goal_created"] == []


# ------------------------------------------------------------------ the service-level refusal
class _NoWritePool:
    """Any write means the refusal came too late."""

    async def fetchval(self, *a, **k):
        raise AssertionError("nothing may be written or queried after a duplicate is found")

    fetchrow = execute = fetch = fetchval


@pytest.fixture
def service_fakes(monkeypatch):
    async def goal(pool, goal_id, scope):
        return {"id": goal_id, "canonical_name": "Export a Word document", "visibility": "public",
                "owner_id": None, "scope_type": "global", "scope_entity_id": None, "provenance": "system_pending_review"}

    async def embed(embedder, text):
        return [0.1] * 8

    monkeypatch.setattr(subs, "get_goal_for_product", goal)
    monkeypatch.setattr(subs, "embed_submission_text", embed)
    monkeypatch.setattr(subs, "_goal_submission_metadata", lambda g, s: {"provenance": "system_pending_review"})
    monkeypatch.setattr(subs.verification_service, "evaluate_layer1", lambda **kw: {"issues": []})

    def with_score(score):
        async def scored(pool, **kw):
            return {"best_match_id": "P-9", "best_match_kind": "procedure", "score": score}
        monkeypatch.setattr(subs, "score_procedure_duplicate", scored)

    return with_score


def _service_call(**kw):
    return _run(subs.create_procedure_submission(
        _NoWritePool(), goal_id="G-1", submission_type=kw.pop("submission_type", "new"),
        name="Word export", steps=["a", "b"], actor_subject="user-1", rationale="r",
        preconditions=[{"subject": "runtime", "predicate": "is", "value": "node"}],
        expected_outcome={"summary": "ok"}, access_scope=AccessScope.for_user("user-1"), **kw))


def test_service_refuses_a_duplicate_before_writing(service_fakes):
    service_fakes(0.9)
    with pytest.raises(subs.DuplicateWay) as err:
        _service_call(refuse_duplicate_at=0.85)
    assert err.value.match_id == "P-9" and err.value.score == 0.9


def test_service_refusal_is_opt_in(service_fakes):
    """REST callers that don't pass refuse_duplicate_at keep the old behaviour
    (stored as needs_review): the call proceeds past the duplicate check."""
    service_fakes(0.99)
    with pytest.raises(AssertionError, match="nothing may be written"):
        _service_call()
