"""Offline: dynamic storage allocation (app/services/storage_autoscale.py) -- which databases it opens or creates."""
from __future__ import annotations

import asyncio

from app.services import storage_autoscale as sa
from app.services.shards import ShardInfo


class _Pool:
    def __init__(self):
        self.weights = {}

    async def execute(self, sql, shard_id, weight):
        assert sql.startswith("UPDATE knowledge_shards SET weight")
        self.weights[shard_id] = weight
        return "UPDATE 1"


def _run(monkeypatch, shards, used, *, min_k=2, min_s=1, provision=True, key=True):
    pool, provisioned = _Pool(), []

    async def fake_list(_pool):
        return shards

    async def fake_provision(_pool, role, index):
        provisioned.append((role, index))

    monkeypatch.setattr(sa, "list_shards", fake_list)
    monkeypatch.setattr(sa, "_provision_one", fake_provision)
    monkeypatch.setenv("STEALTH_MIN_WRITABLE_KNOWLEDGE", str(min_k))
    monkeypatch.setenv("STEALTH_MIN_WRITABLE_SEARCH", str(min_s))
    if key:
        monkeypatch.setenv("NEON_API_KEY", "k")
    else:
        monkeypatch.delenv("NEON_API_KEY", raising=False)
    report = [{"shard_id": sid, "used_ratio": r} for sid, r in used.items()]
    actions = asyncio.run(sa.autoscale(pool, capacity=report, provision=provision))
    return actions, pool.weights, provisioned


def K(sid, weight=100, status="active", role="knowledge"):
    return ShardInfo(sid, status, weight, f"{sid}_DATABASE_URL", None, 500, role)


def test_enough_writable_room_changes_nothing(monkeypatch):
    actions, weights, prov = _run(monkeypatch, [K("K000"), K("K001"), K("K002")], {"K001": .1, "K002": .5})
    assert actions == [] and weights == {} and prov == []


def test_an_idle_registered_database_is_opened_before_any_new_one_is_created(monkeypatch):
    shards = [K("K000"), K("K001"), K("K002", status="full"), K("K003", weight=0), K("K004", weight=0)]
    actions, weights, prov = _run(monkeypatch, shards, {"K001": .2, "K002": .9, "K003": .01, "K004": .01}, min_k=2)
    assert weights == {"K003": 100} and prov == []
    assert [a["action"] for a in actions] == ["promote"]


def test_a_full_role_gets_a_new_database_numbered_after_the_last(monkeypatch):
    shards = [K("K000"), K("S001", role="search"), K("S002", role="search", status="full")]
    actions, weights, prov = _run(monkeypatch, shards, {"S001": .86, "S002": .9}, min_k=1, min_s=1)
    assert prov == [("search", 3)]
    assert {"role": "search", "action": "provision", "shard_id": "S003"} in actions


def test_a_role_the_deployment_does_not_use_never_grows(monkeypatch):
    actions, _w, prov = _run(monkeypatch, [K("K000")], {})
    assert actions == [] and prov == []


def test_without_an_api_key_it_reports_instead_of_provisioning(monkeypatch):
    actions, _w, prov = _run(monkeypatch, [K("K000"), K("K001")], {"K001": .9}, min_k=1, key=False)
    assert prov == [] and actions[0]["action"] == "short_of_room"
