"""The agent-facing model flow (app/routing/plan.py): a plan inside find_ways, and one report per
attempt whose reply names the next model. Offline: the recommender and the stores are faked."""
from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest

import app.mcp_server.server as srv
from app.routing import plan, service, store
from app.services.access import AccessScope

GOAL = str(uuid.uuid4())
PROC = str(uuid.uuid4())
SCOPE = AccessScope.unrestricted()


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_providers():
    saved = list(plan._PROVIDERS)
    plan._PROVIDERS.clear()
    yield
    plan._PROVIDERS[:] = saved


class Provider:
    def __init__(self, name, units=(), boom=False):
        self.name, self.units, self.boom, self.calls = name, list(units), boom, 0

    async def candidates(self, pool, *, scope, goal_id, constraints):
        self.calls += 1
        if self.boom:
            raise RuntimeError("registry down")
        return self.units


def _rec(ladder, **extra):
    return {"status": "ok", "instance_key": extra.pop("instance_key", f"{GOAL}.abc"), "recommendation_id": "rec-1",
            "recommended": {"ladder": ladder, "p_success": 0.9}, "alternatives": [], "excluded": [],
            "meets_reliability_target": True, "reliability_target": 0.9, "check_kind": "tests", **extra}


# ------------------------------------------------------------------ providers

def test_no_candidates_means_no_guess_and_no_recommendation(monkeypatch):
    async def never(*a, **k):
        raise AssertionError("recommend must not run without candidates")
    monkeypatch.setattr(service, "recommend", never)
    out = run(plan.model_plan(None, scope=SCOPE, goal_id=GOAL))
    assert out["status"] == "no_candidates"
    assert not plan.wants_plan(None) and not plan.wants_plan([])


def test_providers_extend_explicit_candidates_and_a_failing_one_is_reported(monkeypatch):
    seen = {}

    async def fake(pool, **kw):
        seen["candidates"] = kw["candidates"]
        return _rec(["a|h"], instance_key=kw["instance_key"])
    monkeypatch.setattr(service, "recommend", fake)
    good, bad = Provider("registry", ["b|h"]), Provider("flaky", boom=True)
    plan.register_candidate_provider(good)
    plan.register_candidate_provider(bad)
    assert plan.wants_plan(None)
    out = run(plan.model_plan(None, scope=SCOPE, goal_id=GOAL, candidates=["a|h"]))
    assert seen["candidates"] == ["a|h", "b|h"]
    assert out["status"] == "ok" and out["provider_errors"][0]["provider"] == "flaky"


def test_registering_the_same_name_replaces():
    plan.register_candidate_provider(Provider("x", ["a|h"]))
    plan.register_candidate_provider(Provider("x", ["b|h"]))
    assert [p.name for p in plan.registered_candidate_providers()] == ["x"]
    plan.unregister_candidate_provider("x")
    assert plan.registered_candidate_providers() == ()


# ------------------------------------------------------------------ the plan

def test_plan_carries_an_instance_key_that_names_its_goal(monkeypatch):
    async def fake(pool, **kw):
        assert kw["instance_key"].startswith(GOAL + ".")
        return _rec(["a|h", "b|h"], instance_key=kw["instance_key"])
    monkeypatch.setattr(service, "recommend", fake)
    out = run(plan.model_plan(None, scope=SCOPE, goal_id=GOAL, procedure_id=PROC, candidates=["a|h", "b|h"]))
    assert out["status"] == "ok" and out["ladder"] == ["a|h", "b|h"]
    assert plan.goal_of_instance(out["instance_key"]) == GOAL
    assert "report_result" in out["next"]


def test_a_routing_error_becomes_a_status_not_an_exception(monkeypatch):
    async def fake(pool, **kw):
        raise service.RoutingError("goal not found")
    monkeypatch.setattr(service, "recommend", fake)
    out = run(plan.model_plan(None, scope=SCOPE, goal_id=GOAL, candidates=["a|h"]))
    assert {k: v for k, v in out.items() if k != "plan_ms"} == {"status": "unavailable", "reason": "goal not found"}
    assert isinstance(out["plan_ms"], int) and out["plan_ms"] >= 0                # the router's time is reported even on failure


def test_not_ready_passes_through():
    assert plan._compact({"status": "not_ready", "reason": "no fitted model yet"}) == {
        "status": "not_ready", "reason": "no fitted model yet"}


def test_the_routers_plan_time_is_stored_with_the_find_ways_record(monkeypatch):
    import app.mcp_server.server as srv
    from app.services import search_group
    seen = {}

    class Log:
        async def execute(self, sql, *args):
            seen["detail"] = args[-1]

    async def log_pool(pool, key, **kw):
        return Log()
    monkeypatch.setattr(search_group, "pool_for_log", log_pool)
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: SimpleNamespace(viewer_id="u1"))
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"pool": object()}))
    reply = json.dumps({"outcome": "resolved", "model_plan": {"status": "ok", "plan_ms": 7}})
    run(srv._record_find_ways(ctx, "q", reply, {}, 123.4))
    assert seen["detail"]["plan_ms"] == 7 and seen["detail"]["total_ms"] == 123.4 and seen["detail"]["outcome"] == "resolved"
    seen.clear()
    run(srv._record_find_ways(ctx, "q", json.dumps({"outcome": "no_match"}), {}, 5.0))
    assert "plan_ms" not in seen["detail"]                                            # no plan, no invented number


def test_goal_of_instance_rejects_foreign_keys():
    assert plan.goal_of_instance("whatever") is None
    assert plan.goal_of_instance("not-a-uuid.abc") is None
    assert plan.goal_of_instance(f"{GOAL}.") is None


# ------------------------------------------------------------------ reporting

def _instance(attempts=(), ladder=("a|h", "b|h", "c|h"), consumed=0, visibility="public"):
    return plan.Instance(f"{GOAL}.abc", {"visibility": visibility, "owner_id": None},
                         {"id": "rec-1", "goal_id": GOAL, "procedure_id": PROC, "candidates": ["a|h", "b|h", "c|h"],
                          "ladder": list(ladder), "visibility": visibility, "owner_id": None,
                          "constraints": {"max_rungs": 2, "check_kind": "tests", "previous_attempts": consumed,
                                          "previous_steps": 0, "remaining_steps": 0}}, list(attempts))


def _record(monkeypatch):
    rows = []

    async def fake(pool, obs, **kw):
        rows.append(obs)
        return "obs-1"
    monkeypatch.setattr(service, "record_observation", fake)
    return rows


def test_a_pass_records_the_first_rung_and_stops(monkeypatch):
    rows = _record(monkeypatch)

    async def never(*a, **k):
        raise AssertionError("no recommendation after a pass")
    monkeypatch.setattr(service, "recommend", never)
    out = run(plan.report_result(None, scope=SCOPE, instance=_instance(), accepted=True, tokens_in=10, tokens_out=5))
    assert out["status"] == "accepted" and "Stop" in out["next"]
    row = rows[0]
    assert (row["model_key"], row["scaffold"], row["attempt_index"], row["check_kind"]) == ("a", "h", 0, "tests")
    assert row["recommendation_id"] == "rec-1" and row["procedure_id"] == PROC and row["goal_id"] == GOAL


def test_a_failure_returns_the_next_model_conditioned_on_the_failures(monkeypatch):
    rows = _record(monkeypatch)
    seen = {}

    async def fake(pool, **kw):
        seen.update(kw)
        return _rec(["c|h"], instance_key=kw["instance_key"])
    monkeypatch.setattr(service, "recommend", fake)
    earlier = [{"model_key": "a", "scaffold": "h", "accepted": False, "check_kind": "tests", "attempt_index": 0}]
    out = run(plan.report_result(None, scope=SCOPE, instance=_instance(attempts=earlier, consumed=0),
                                 accepted=False))
    assert rows[0]["model_key"] == "b" and rows[0]["attempt_index"] == 1          # the second rung
    assert [a["unit"] for a in seen["previous_attempts"]] == ["a|h", "b|h"]
    assert all(a["accepted"] is False for a in seen["previous_attempts"])
    assert seen["constraints"] == {"max_rungs": 2}                               # bookkeeping keys stripped
    assert seen["instance_key"] == f"{GOAL}.abc" and seen["candidates"] == ["a|h", "b|h", "c|h"]
    assert out["status"] == "rejected" and out["next_model"] == "c|h"


def test_after_a_redecision_the_default_unit_counts_from_its_own_start(monkeypatch):
    # The latest decision was made after one failure, so its ladder begins at the NEXT model.
    earlier = [{"model_key": "a", "scaffold": "h", "accepted": False, "check_kind": "tests", "attempt_index": 0}]
    inst = _instance(attempts=earlier, ladder=("b|h", "c|h"), consumed=1)
    assert plan._default_unit(inst) == "b|h"
    inst2 = _instance(attempts=earlier + [dict(earlier[0], model_key="b", attempt_index=1)],
                      ladder=("b|h", "c|h"), consumed=1)
    assert plan._default_unit(inst2) == "c|h"


def test_an_explicit_unit_overrides_the_ladder(monkeypatch):
    rows = _record(monkeypatch)
    out = run(plan.report_result(None, scope=SCOPE, instance=_instance(), accepted=True, unit="z@2|other"))
    assert (rows[0]["model_key"], rows[0]["scaffold"]) == ("z@2", "other") and out["status"] == "accepted"


def test_a_ladder_that_is_used_up_asks_for_the_unit_instead_of_guessing(monkeypatch):
    rows = _record(monkeypatch)
    spent = [{"model_key": m, "scaffold": "h", "accepted": False, "check_kind": "tests", "attempt_index": i}
             for i, m in enumerate("abc")]
    with pytest.raises(service.RoutingError, match="pass `unit`"):
        run(plan.report_result(None, scope=SCOPE, instance=_instance(attempts=spent), accepted=False))
    assert rows == []                                                           # nothing written


def test_no_rung_left_says_escalate(monkeypatch):
    _record(monkeypatch)

    async def fake(pool, **kw):
        return _rec([])
    monkeypatch.setattr(service, "recommend", fake)
    out = run(plan.report_result(None, scope=SCOPE, instance=_instance(), accepted=False))
    assert out["status"] == "rejected" and out["ladder"] == [] and "escalate" in out["next"]


def test_private_visibility_is_kept_on_the_observation(monkeypatch):
    rows = _record(monkeypatch)
    run(plan.report_result(None, scope=SCOPE, instance=_instance(visibility="private"), accepted=True,
                           visibility="private", owner_id="u1"))
    assert rows[0]["visibility"] == "private" and rows[0]["owner_id"] == "u1"


# ------------------------------------------------------------------ loading

def test_load_instance_refuses_foreign_unknown_and_invisible(monkeypatch):
    async def none_goal(pool, goal_id, scope):
        return None

    async def real_goal_decision(pool, goal_id, key):     # a decision on a REAL Goal the caller cannot see
        return {"id": "rec-1", "goal_id": goal_id, "constraints": {}, "visibility": "private", "owner_id": "u2"}
    monkeypatch.setattr(store, "visible_goal", none_goal)
    monkeypatch.setattr(store, "instance_decision", real_goal_decision)
    with pytest.raises(service.RoutingError, match="did not come from find_ways"):
        run(plan.load_instance(None, SCOPE, "plain-key"))
    with pytest.raises(service.RoutingError, match="unknown instance_key"):
        run(plan.load_instance(None, SCOPE, f"{GOAL}.abc"))

    async def goal(pool, goal_id, scope):
        return {"visibility": "public", "owner_id": None}

    async def no_decision(pool, goal_id, key):
        return None
    monkeypatch.setattr(store, "visible_goal", goal)
    monkeypatch.setattr(store, "instance_decision", no_decision)
    with pytest.raises(service.RoutingError, match="unknown instance_key"):
        run(plan.load_instance(None, SCOPE, f"{GOAL}.abc"))


def test_load_instance_returns_the_stored_state(monkeypatch):
    async def goal(pool, goal_id, scope):
        return {"visibility": "public", "owner_id": None}

    async def decision(pool, goal_id, key):
        return {"id": "rec-1", "goal_id": goal_id, "procedure_id": PROC, "candidates": ["a|h"], "ladder": ["a|h"],
                "constraints": {}, "visibility": "public", "owner_id": None}

    async def attempts(pool, goal_id, key):
        return [{"model_key": "a", "scaffold": "h", "accepted": False, "check_kind": "tests", "attempt_index": 0}]
    async def issuer(pool, goal_id, key):
        return None
    monkeypatch.setattr(store, "visible_goal", goal)
    monkeypatch.setattr(store, "instance_decision", decision)
    monkeypatch.setattr(store, "instance_attempts", attempts)
    monkeypatch.setattr(store, "instance_issuer", issuer)
    inst = run(plan.load_instance(None, SCOPE, f"{GOAL}.abc"))
    assert inst.procedure_id == PROC and len(inst.attempts) == 1


def _issued_to(monkeypatch, who):
    async def goal(pool, goal_id, scope):
        return {"visibility": "public", "owner_id": None}

    async def decision(pool, goal_id, key):
        return {"id": "rec-1", "goal_id": goal_id, "procedure_id": PROC, "candidates": ["a|h"], "ladder": ["a|h"],
                "constraints": {}, "visibility": "public", "owner_id": None}

    async def attempts(pool, goal_id, key):
        return []

    async def issuer(pool, goal_id, key):
        return who
    monkeypatch.setattr(store, "visible_goal", goal)
    monkeypatch.setattr(store, "instance_decision", decision)
    monkeypatch.setattr(store, "instance_attempts", attempts)
    monkeypatch.setattr(store, "instance_issuer", issuer)


def test_an_instance_belongs_to_the_caller_it_was_issued_to(monkeypatch):
    _issued_to(monkeypatch, "u1")
    assert run(plan.load_instance(None, AccessScope.for_user("u1"), f"{GOAL}.abc")).procedure_id == PROC
    assert run(plan.load_instance(None, AccessScope.for_org_member("u1", ["o"]), f"{GOAL}.abc"))
    assert run(plan.load_instance(None, AccessScope.unrestricted(), f"{GOAL}.abc"))              # internal / stdio
    for other in (AccessScope.for_user("u2"), AccessScope.anonymous()):
        with pytest.raises(service.RoutingError, match="unknown instance_key"):
            run(plan.load_instance(None, other, f"{GOAL}.abc"))


def test_an_instance_issued_to_nobody_stays_with_unidentified_callers_only(monkeypatch):
    # securityp1.md §5.1 item 1: issued to nobody (a pre-binding decision, or a plan for the single-user server's
    # unidentified caller) -- the unidentified caller keeps it, a signed-in user can never take it over
    _issued_to(monkeypatch, None)
    assert run(plan.load_instance(None, AccessScope.anonymous(), f"{GOAL}.abc"))
    with pytest.raises(plan.RoutingError, match="unknown instance_key"):
        run(plan.load_instance(None, AccessScope.for_user("u2"), f"{GOAL}.abc"))


def test_an_unidentified_caller_cannot_use_a_named_callers_instance(monkeypatch):
    _issued_to(monkeypatch, "u1")
    with pytest.raises(plan.RoutingError, match="unknown instance_key"):
        run(plan.load_instance(None, AccessScope.anonymous(), f"{GOAL}.abc"))


def test_the_plan_stamps_the_real_caller_and_ignores_one_supplied_by_the_agent(monkeypatch):
    seen = {}

    async def fake(pool, **kw):
        seen.update(kw)
        return _rec(["a|h"], instance_key=kw["instance_key"])
    monkeypatch.setattr(service, "recommend", fake)
    run(plan.model_plan(None, scope=AccessScope.for_user("u1"), goal_id=GOAL, candidates=["a|h"],
                        constraints={"_caller": "someone-else", "max_rungs": 2}))
    assert seen["constraints"] == {"max_rungs": 2, "_caller": "u1"}


def test_a_re_decision_keeps_the_owner(monkeypatch):
    rows = _record(monkeypatch)
    seen = {}

    async def fake(pool, **kw):
        seen.update(kw)
        return _rec(["c|h"], instance_key=kw["instance_key"])
    monkeypatch.setattr(service, "recommend", fake)
    inst = _instance()
    inst.decision["constraints"]["_caller"] = "u1"
    run(plan.report_result(None, scope=SCOPE, instance=inst, accepted=False))
    assert seen["constraints"].get("_caller") == "u1" and rows


# ------------------------------------------------------------------ MCP surface

def test_report_result_is_registered_classified_and_annotated():
    names = {t.name for t in srv.server._tool_manager.list_tools()}
    assert "report_result" in names and "report_result" in srv.V1_TOOLS
    assert srv._TOOL_SCOPES["report_result"] == srv._WRITE
    assert srv._V1_ANNOTATIONS["report_result"]["read_only_hint"] is False


def _ctx():
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"pool": object()}))


def _attach(reply, **kw):
    return run(srv._attach_model_plan(reply, _ctx(), candidates=kw.get("candidates"),
                                      check_kind=None, constraints=None))


def test_find_ways_reply_is_unchanged_without_candidates():
    reply = json.dumps({"outcome": "resolved", "procedures": [{"goal_id": GOAL, "procedure_id": PROC}]})
    assert _attach(reply) == reply


def test_find_ways_attaches_the_plan_for_a_resolved_goal(monkeypatch):
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: SCOPE)
    seen = {}

    async def fake(pool, **kw):
        seen.update(kw)
        return {"status": "ok", "ladder": ["a|h"], "instance_key": f"{GOAL}.abc"}
    monkeypatch.setattr(plan, "model_plan", fake)
    reply = json.dumps({"outcome": "resolved", "procedures": [{"goal_id": GOAL, "procedure_id": PROC}], "k": 1})
    body = json.loads(_attach(reply, candidates=["a|h"]))
    assert body["k"] == 1 and body["model_plan"]["ladder"] == ["a|h"]
    assert (seen["goal_id"], seen["procedure_id"], seen["candidates"]) == (GOAL, PROC, ["a|h"])


def test_find_ways_plans_an_unresolved_task_on_a_virtual_key_and_ignores_refusals(monkeypatch):
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: SCOPE)
    seen = {}

    async def fake_plan(pool, **kw):
        seen.update(kw)
        return {"status": "ok", "ladder": ["a|h"], "instance_key": f"{kw['goal_id']}.k"}
    monkeypatch.setattr(plan, "model_plan", fake_plan)
    body = json.loads(_attach(json.dumps({"outcome": "ambiguous", "suggested": {"goal_id": GOAL}}),
                              candidates=["a|h"]))
    assert body["outcome"] == "ambiguous" and body["model_plan"]["status"] == "ok"
    assert seen["goal_id"] == service.virtual_goal_id("generic")
    assert seen["virtual"] == {"kind": "generic", "parents": [GOAL]}     # the suggested Goal pulls the prior
    assert seen.get("procedure_id") is None
    assert _attach("REFUSED: bad json", candidates=["a|h"]) == "REFUSED: bad json"


class _Lib:
    def __init__(self, repo_id):
        self.identity = type("I", (), {"repo_id": repo_id})() if repo_id else None
        self.rows = []


def test_plan_case_order_global_then_library_then_repo_then_generic():
    stats = {"files": 2, "hunks": 3, "lines_added": 10, "lines_removed": 4, "languages": 1, "packages": 1}
    repo = {"files": 1, "hunks": 1, "lines_added": 3, "lines_removed": 1, "languages": 1, "packages": 1}
    tf = {"entries": {"L-0a91f2": stats}, "repo": repo}
    resolved = {"outcome": "resolved", "procedures": [{"goal_id": GOAL, "procedure_id": PROC}]}
    root, virtual = srv._plan_case(resolved, _Lib("r:0123456789abcdef"), tf)
    assert root["goal_id"] == GOAL and virtual is None

    matched = {"outcome": "no_match", "library_matches": [
        {"id": "L-77d3e0", "judged": True, "relation": "matches", "outcome": "fail"},      # a failed attempt: skip
        {"id": "L-0a91f2", "judged": True, "relation": "matches", "outcome": "pass"}]}
    root, virtual = srv._plan_case(matched, _Lib("r:0123456789abcdef"), tf)
    assert root is None and virtual["kind"] == "library" and virtual["ref"] == "L-0a91f2"
    assert virtual["features"] == stats
    assert virtual["id"] == service.virtual_goal_id("library", "r:0123456789abcdef:L-0a91f2")

    partial = {"outcome": "no_match", "library_matches": [{"id": "L-0a91f2", "judged": True, "relation": "partial"}]}
    _, virtual = srv._plan_case(partial, _Lib("r:0123456789abcdef"), tf)
    assert virtual["kind"] == "repo" and virtual["features"] == repo
    assert virtual["id"] == service.virtual_goal_id("repo", "r:0123456789abcdef")

    _, virtual = srv._plan_case({"outcome": "no_match"}, None, None)
    assert virtual == {"id": service.virtual_goal_id("generic"), "kind": "generic", "parents": []}


def test_virtual_keys_are_stable_and_distinct():
    assert service.virtual_goal_id("repo", "r:1") == service.virtual_goal_id("repo", "r:1")
    assert len({service.virtual_goal_id("repo", "r:1"), service.virtual_goal_id("library", "r:1"),
                service.virtual_goal_id("generic")}) == 3
    with pytest.raises(service.RoutingError):
        service.virtual_goal_id("other")


def test_a_virtual_instance_loads_through_its_own_decision(monkeypatch):
    vid = service.virtual_goal_id("repo", "r:1")

    async def never(pool, goal_id, scope):
        raise AssertionError("a virtual key has no Goal row to look up")

    async def decision(pool, goal_id, key):
        return {"id": "rec-1", "goal_id": goal_id, "procedure_id": None, "candidates": ["a|h"], "ladder": ["a|h"],
                "constraints": {"_virtual": {"kind": "repo"}}, "visibility": "private", "owner_id": "u1"}

    async def attempts(pool, goal_id, key):
        return []

    async def issuer(pool, goal_id, key):
        return str(SCOPE.viewer_id) if SCOPE.viewer_id is not None else None
    monkeypatch.setattr(store, "visible_goal", never)
    monkeypatch.setattr(store, "instance_decision", decision)
    monkeypatch.setattr(store, "instance_attempts", attempts)
    monkeypatch.setattr(store, "instance_issuer", issuer)
    inst = run(plan.load_instance(None, SCOPE, f"{vid}.abc"))
    assert inst.decision["constraints"]["_virtual"]["kind"] == "repo"


def test_a_plan_failure_never_breaks_find_ways(monkeypatch):
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: SCOPE)

    async def boom(pool, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(plan, "model_plan", boom)
    reply = json.dumps({"outcome": "resolved", "procedures": [{"goal_id": GOAL}]})
    body = json.loads(_attach(reply, candidates=["a|h"]))
    assert body["outcome"] == "resolved" and body["model_plan"]["status"] == "unavailable"


def test_stored_json_is_read_whether_or_not_it_was_double_encoded():
    # record_decision writes json.dumps() text through a pool whose jsonb codec encodes again, so the column holds a
    # JSON string of JSON text; readers must give the same dict for that, for a plain dict, and for plain text.
    value = {"_caller": "u1", "check_kind": "tests"}
    once = json.dumps(value)
    assert store._json_value(value) == value
    assert store._json_value(once) == value
    assert store._json_value(json.dumps(once)) == value
    assert store._json_value(once.encode()) == value
    assert store._json_value(None) is None


# ------------------------------------------------------------------ default candidates and the reply shape (plan §2.4, §5.2)

def test_the_callers_own_model_is_a_unit_on_its_client_scaffold():
    assert plan.caller_unit("claude-sonnet-4-5", "Claude Code") == "claude-sonnet-4-5|claude-code"
    assert plan.caller_unit("gpt-5|my-agent", "cursor") == "gpt-5|my-agent"
    assert plan.caller_unit("deepseek-v3.2") == "deepseek-v3.2|direct"
    assert plan.caller_unit("  ") is None and plan.caller_unit(None) is None


def test_catalog_candidates_come_from_the_deployment_setting_and_are_inert_without_it():
    empty = plan.PublicCatalogCandidates(env={})
    assert not empty.configured()
    cat = plan.PublicCatalogCandidates(env={plan.DEFAULT_MODELS_ENV: "gpt-oss-120b, qwen3-coder|openhands,gpt-oss-120b"})
    assert cat.configured()
    assert run(cat.candidates(None, scope=SCOPE, goal_id=GOAL, constraints={})) == [
        "gpt-oss-120b|direct", "qwen3-coder|openhands"]
    plan.register_candidate_provider(empty)
    assert not plan.wants_plan(None)                     # an unset catalogue never turns the plan on
    plan.register_candidate_provider(cat)
    assert plan.wants_plan(None)


def test_the_plan_carries_basis_fit_and_per_rung_uncertainty(monkeypatch):
    rec = _rec(["a|s", "b|s"], params_version=7, as_of="2026-10-07T00:00:00+00:00",
               units={"a|s": {"p_ok_mean": 0.4, "p_ok_q05": 0.2, "p_ok_q95": 0.6, "cost_mean": 0.01},
                      "b|s": {"p_ok_mean": 0.8, "p_ok_q05": 0.7, "p_ok_q95": 0.9, "cost_mean": 0.2}},
               evidence={"goal_observations": 0, "models": {"a|s": "card", "b|s": "population"}})

    async def fake(*a, **k):
        return rec
    monkeypatch.setattr(service, "recommend", fake)
    out = run(plan.model_plan(None, scope=SCOPE, goal_id=GOAL, candidates=["a|s", "b|s"]))
    assert out["basis"] == "prior" and out["fit_id"] == 7 and out["as_of"].startswith("2026-10-07")
    assert out["steps"] == [{"step": "*", "ladder": [
        {"unit": "a|s", "p_ok_mean": 0.4, "p_ok_q05": 0.2, "p_ok_q95": 0.6, "cost_mean": 0.01},
        {"unit": "b|s", "p_ok_mean": 0.8, "p_ok_q05": 0.7, "p_ok_q95": 0.9, "cost_mean": 0.2}]}]
    assert out["ladder"] == ["a|s", "b|s"]                 # the old fields are still there
    rec["evidence"]["models"]["a|s"] = "fitted"
    assert run(plan.model_plan(None, scope=SCOPE, goal_id=GOAL, candidates=["a|s"]))["basis"] == "posterior"
