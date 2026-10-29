"""A deployment holding no search member's connection string runs as one database (app/services/search_group.py):
local .kell memory and routing keep working without the global knowledge databases."""
from __future__ import annotations

import asyncio

from app.services import search_group, shards
from app.services.shards import ROLE_SEARCH, ShardInfo

MEMBERS = [ShardInfo("S001", "active", 100, "S001_DATABASE_URL", role=ROLE_SEARCH),
           ShardInfo("S002", "active", 100, "S002_DATABASE_URL", role=ROLE_SEARCH)]


def _grouped(monkeypatch, env: dict) -> bool:
    async def cached(pool, **_):
        return MEMBERS
    monkeypatch.setattr(shards, "cached_shards", cached)
    for k in ("S001_DATABASE_URL", "S002_DATABASE_URL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return asyncio.run(search_group.grouped(object()))


def test_no_member_reachable_means_one_database(monkeypatch):
    assert _grouped(monkeypatch, {}) is False


def test_any_member_reachable_keeps_the_group(monkeypatch):
    assert _grouped(monkeypatch, {"S001_DATABASE_URL": "postgresql://x"}) is True
