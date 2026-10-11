"""Routing per KIND of task (the request's codebook group): the router skips the cheap models only for a kind where
they keep failing, keeps them first where they pass, starts a new kind optimistic, and comes back when a kind
recovers. Offline: the store answers from the bundled prior; the kind's record is what goal_outcome_counts returns
(the caller's outcomes on that kind, from every repository)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments" / "local_eval"))

from app.mcp_server import server
from app.routing import service, store
from app.services.access import AccessScope

UNITS = ["gemma-4-31b-it|stealth", "gpt-oss-120b|stealth", "minimax-m2-7|stealth", "claude-opus-5-5|claude-code"]
OWN = "claude-opus-5-5|claude-code"
CHEAP = UNITS[:3]


def _opus_first_share(monkeypatch, record, n=30, group="c07"):
    """The share of plans (different instances) whose first rung is the session's own model."""
    from route_run import _no_database

    async def counts(*a, **k):
        return record

    async def run():
        firsts = []
        with _no_database():
            monkeypatch.setattr(store, "goal_outcome_counts", counts)
            for i in range(n):
                r = await service._recommend(None, goal_id=service.virtual_goal_id("code", group), candidates=UNITS,
                    access_scope=AccessScope.unrestricted(), virtual={"kind": "code", "code": f"{group}.1"},
                    instance_key=f"k{i}", check_kind="tests", previous_attempts=[], local_obs=[], record=False,
                    constraints={"reliability_baseline": OWN})
                firsts.append((r.get("recommended") or {}).get("ladder", [None])[0])
        return firsts

    firsts = asyncio.run(run())
    return sum(1 for f in firsts if f == OWN) / len(firsts)


def test_a_new_kind_starts_with_the_cheap_models(monkeypatch):
    assert _opus_first_share(monkeypatch, []) <= 0.2


def test_a_kind_the_cheap_models_pass_keeps_them_first(monkeypatch):
    easy = [{"unit": u, "n": 10, "ok": 9} for u in CHEAP] + [{"unit": OWN, "n": 4, "ok": 4}]
    assert _opus_first_share(monkeypatch, easy) <= 0.2


def test_a_kind_they_keep_failing_goes_to_the_session_model_but_a_recovered_kind_comes_back(monkeypatch):
    hard = [{"unit": u, "n": 12, "ok": 1} for u in CHEAP] + [{"unit": OWN, "n": 8, "ok": 7}]
    assert _opus_first_share(monkeypatch, hard) >= 0.6, "a kind where cheap models pass ~8% is not worth dispatching"
    recovered = [{"unit": u, "n": 30, "ok": 22} for u in CHEAP] + [{"unit": OWN, "n": 8, "ok": 7}]
    assert _opus_first_share(monkeypatch, recovered) <= 0.2


def test_thin_evidence_does_not_tip_a_kind_to_the_session_model(monkeypatch):
    unlucky = [{"unit": u, "n": 2, "ok": 0} for u in CHEAP]           # two failures each: too little to give up
    assert _opus_first_share(monkeypatch, unlucky) <= 0.5


def test_a_request_with_no_goal_is_keyed_by_its_kind_and_a_repository_by_repo_and_kind():
    class Lib:
        identity = None

    root, v = server._plan_case({}, Lib(), {}, "c07.3")
    assert root is None and v["kind"] == "code" and v["id"] == service.virtual_goal_id("code", "c07")
    assert v["code"] == "c07.3"

    class Repo:
        class identity:
            repo_id = "r1"

    _, v = server._plan_case({}, Repo(), {}, "c07.3")
    assert v["kind"] == "repo" and v["id"] == service.virtual_goal_id("repo", "r1:c07") and v["code"] == "c07.3"
    _, v = server._plan_case({}, Repo(), {}, None)
    assert v["id"] == service.virtual_goal_id("repo", "r1"), "no code: the repository's record, as before"
    _, v = server._plan_case({}, Lib(), {}, None)
    assert v["id"] == service.virtual_goal_id("generic"), "no code: the generic record, as before"
