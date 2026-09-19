"""Offline tests for shard routing (pure functions + hydration batching)."""
import asyncio
from collections import Counter

import pytest

from app.services.shards import (
    HOME_SHARD, NoWritableShard, ShardInfo, ShardPools, ShardUnavailable,
    choose_child_shard, choose_shard, hydrate_rows,
)


def _s(sid, status="active", weight=100):
    return ShardInfo(sid, status, weight)


def test_choose_shard_is_deterministic_and_stable():
    shards = [_s("K001"), _s("K002"), _s("K003")]
    picks = {choose_shard(f"goal-{i}", shards) for i in range(50)}
    assert picks <= {"K001", "K002", "K003"}
    assert choose_shard("goal-7", shards) == choose_shard("goal-7", list(reversed(shards)))


def test_weights_bias_placement():
    shards = [_s("A", weight=300), _s("B", weight=100)]
    c = Counter(choose_shard(f"g{i}", shards) for i in range(4000))
    assert 0.70 < c["A"] / 4000 < 0.80


def test_adding_a_shard_only_moves_keys_to_the_new_shard():
    before = [_s("A"), _s("B")]
    after = before + [_s("C")]
    moved_elsewhere = 0
    for i in range(2000):
        b, a = choose_shard(f"g{i}", before), choose_shard(f"g{i}", after)
        if a != b and a != "C":
            moved_elsewhere += 1
    assert moved_elsewhere == 0


def test_non_active_shards_never_receive_new_objects():
    shards = [_s("A", status="full"), _s("B", status="readonly"), _s("C", status="unhealthy"),
              _s("D", weight=0), _s("E")]
    assert {choose_shard(f"g{i}", shards) for i in range(200)} == {"E"}


def test_no_writable_shard_fails_closed():
    with pytest.raises(NoWritableShard):
        choose_shard("g", [_s("A", status="full")])


def test_child_prefers_goal_shard_and_rolls_over_when_full():
    shards = [_s("A"), _s("B")]
    assert choose_child_shard("B", "p1", shards) == "B"
    rolled = choose_child_shard("B", "p1", [_s("A"), _s("B", status="full")])
    assert rolled == "A"


class _Pool:
    def __init__(self, rows, fail=False):
        self.rows, self.fail, self.calls = rows, fail, 0

    async def fetch(self, ids):
        self.calls += 1
        if self.fail:
            raise ShardUnavailable("x", "down")
        return [r for r in self.rows if r["id"] in ids]


def _pools(**named):
    p = ShardPools.__new__(ShardPools)
    p._pools, p._failed_until, p._locks, p.connects = dict(named), {}, {}, 0
    p._backoff_s = 10.0
    return p


@pytest.mark.asyncio
async def test_hydration_is_one_batched_call_per_involved_shard_only():
    a = _Pool([{"id": "1"}, {"id": "2"}])
    b = _Pool([{"id": "3"}])
    untouched = _Pool([{"id": "9"}])
    pools = _pools(K001=a, K002=b, K009=untouched)

    async def fetch(pool, ids):
        return await pool.fetch(ids)

    res = await hydrate_rows(pools, {"1": "K001", "2": "K001", "3": "K002"}, fetch)
    assert set(res.rows) == {"1", "2", "3"}
    assert (a.calls, b.calls, untouched.calls) == (1, 1, 0)   # batched, no fan-out
    assert res.shard_batches == {"K001": 2, "K002": 1}
    assert not res.partial and not res.missing_ids


@pytest.mark.asyncio
async def test_unavailable_shard_is_reported_not_treated_as_absence():
    good = _Pool([{"id": "1"}])
    bad = _Pool([], fail=True)
    pools = _pools(K001=good, K002=bad)

    async def fetch(pool, ids):
        return await pool.fetch(ids)

    res = await hydrate_rows(pools, {"1": "K001", "2": "K002"}, fetch)
    assert res.partial and "K002" in res.unavailable_shards
    assert "2" not in res.missing_ids            # outage != nonexistent
    assert set(res.rows) == {"1"}


@pytest.mark.asyncio
async def test_reachable_shard_missing_id_is_reported_as_missing():
    pools = _pools(K001=_Pool([{"id": "1"}]))

    async def fetch(pool, ids):
        return await pool.fetch(ids)

    res = await hydrate_rows(pools, {"1": "K001", "ghost": "K001"}, fetch)
    assert res.missing_ids == ["ghost"] and not res.partial


@pytest.mark.asyncio
async def test_register_shard_rejects_a_connection_string():
    from app.services.shards import register_shard
    with pytest.raises(ValueError):
        await register_shard(None, "K005", dsn_env="postgresql://u:p@h/db")


@pytest.mark.asyncio
async def test_dead_shard_backoff_prevents_connection_storm(monkeypatch):
    from app.services import shards as sh

    async def fake_cached(_pool, **_):
        return [ShardInfo("K007", "active", 100, "K007_DSN")]

    monkeypatch.setattr(sh, "cached_shards", fake_cached)
    monkeypatch.setenv("K007_DSN", "postgresql://x")

    async def boom(dsn):
        raise OSError("refused")

    p = ShardPools(control_pool=object(), pool_factory=boom, backoff_s=60)
    for _ in range(5):
        with pytest.raises(ShardUnavailable):
            await p.get("K007")
    assert p.connects == 1   # 4 of 5 attempts short-circuited by backoff
