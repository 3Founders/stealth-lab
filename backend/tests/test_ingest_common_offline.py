"""Offline: the rebuilt ingestion foundation (app/ingest/common) -- target guard, license decisions, pinned sources,
preflight decisions."""
from __future__ import annotations

import asyncio

import pytest

from app.ingest.common import licenses, target
from app.ingest.common.hf import PinnedFile
from app.ingest.common.preflight import PreflightFailed, check_embedding_space, check_queue_alive

LOCAL = "postgresql://postgres@127.0.0.1:55432/kel_ingest_test"
HOSTED = "postgresql://u:p@ep-example.neon.tech/db"


# --- target ---------------------------------------------------------------------------------------------------

def test_local_target_needs_a_named_local_dsn():
    with pytest.raises(target.TargetRefused, match="--dsn-env"):
        target.resolve_target("local", env={})
    with pytest.raises(target.TargetRefused, match="NAME"):
        target.resolve_target("local", dsn_env=LOCAL, env={})
    with pytest.raises(target.TargetRefused, match="not set"):
        target.resolve_target("local", dsn_env="X", env={})
    with pytest.raises(target.TargetRefused, match="this machine"):
        target.resolve_target("local", dsn_env="X", env={"X": HOSTED})
    assert target.resolve_target("local", dsn_env="X", env={"X": LOCAL}).name == "local"


def test_production_needs_an_approver():
    with pytest.raises(target.TargetRefused, match="approved-by"):
        target.resolve_target("production", env={})
    with pytest.raises(target.TargetRefused, match="unknown target"):
        target.resolve_target("staging", env={})


def test_bind_local_rebinds_every_address_and_refuses_a_leftover(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", HOSTED, raising=False)
    monkeypatch.setattr(settings, "search_database_url", HOSTED, raising=False)
    env = {"DATABASE_URL": HOSTED, "SEARCH_DATABASE_URL": HOSTED, "CONTROL_DATABASE_URL": HOSTED}
    t = target.Target("local", LOCAL, None)
    target.bind_local(t, env=env)
    assert env["DATABASE_URL"] == LOCAL and env["CONTROL_DATABASE_URL"] == LOCAL and "SEARCH_DATABASE_URL" not in env
    assert settings.database_url == LOCAL and settings.search_database_url is None
    env["DATABASE_URL_DIRECT"] = HOSTED
    with pytest.raises(target.TargetRefused, match="DATABASE_URL_DIRECT"):
        target.assert_no_hosted_address(env)


def test_shard_dsns_must_be_local_on_a_local_run():
    class Pool:
        async def fetch(self, sql, *a):
            return [{"shard_id": "K002", "dsn_env": "K002_DSN"}]

    with pytest.raises(target.TargetRefused, match="K002"):
        asyncio.run(target.assert_shards_local(Pool(), env={"K002_DSN": HOSTED}))
    asyncio.run(target.assert_shards_local(Pool(), env={"K002_DSN": LOCAL}))


# --- licenses ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name,spdx", [
    ("MIT License", "MIT"), ('BSD 3-Clause "New" or "Revised" License', "BSD-3-Clause"),
    ("New BSD License", "BSD-3-Clause"), ("Apache License 2.0 or MIT License", "Apache-2.0 OR MIT"),
])
def test_github_names_map_exactly(name, spdx):
    assert licenses.spdx_for_github_name(name) == spdx


@pytest.mark.parametrize("name", ["BSD", "BSD License", "Public Domain", None, "", "Some License"])
def test_names_that_do_not_say_which_license_are_not_guessed(name):
    assert licenses.spdx_for_github_name(name) is None


def test_expressions():
    assert licenses.decide("MIT", records_attribution=True).allowed
    either = licenses.decide("GPL-3.0-only OR MIT", records_attribution=True)
    assert either.allowed and either.used_under == "MIT"
    both = licenses.decide("Apache-2.0 AND GPL-3.0-only", records_attribution=True)
    assert both.decision == "REJECT"
    assert licenses.decide("NOASSERTION", records_attribution=True).decision == "UNMAPPABLE"
    assert licenses.decide("MIT OR Apache-2.0 AND BSD-3-Clause", records_attribution=True).decision == "UNMAPPABLE"
    assert licenses.decide("CC-BY-4.0", records_attribution=False).decision == "QUARANTINE"
    assert licenses.decide("CC-BY-4.0", records_attribution=True).allowed


# --- pinned sources ---------------------------------------------------------------------------------------------

def test_a_source_must_be_pinned_to_a_full_commit():
    with pytest.raises(ValueError, match="40-character"):
        PinnedFile("a/b", "main", "x.parquet")
    with pytest.raises(ValueError):
        PinnedFile("a/b", "0d73048a", "x.parquet")
    f = PinnedFile("a/b", "0" * 40, "x.parquet")
    assert f.uri("r1") == f"hf://datasets/a/b@{'0' * 40}/x.parquet#r1"


# --- preflight decisions ----------------------------------------------------------------------------------------

class _VectorPool:
    def __init__(self, models):
        self.models = models

    async def fetchval(self, sql, *a):
        return True

    async def fetch(self, sql, *a):
        if "information_schema" in sql:
            return [{"column_name": c} for c in ("embedding", "embedding_model_id", "embedding_model")]
        return [{"m": m, "n": n} for m, n in self.models.items()]

    async def fetchrow(self, sql, *a):
        return None


def test_a_foreign_embedding_model_in_the_index_refuses_the_run():
    with pytest.raises(PreflightFailed, match="mix two vector spaces"):
        asyncio.run(check_embedding_space(_VectorPool({"vertex:gemini-embedding-001": 26,
                                                        "vertex:gemini-embedding-2": 1687}),
                                          "vertex:gemini-embedding-2"))
    asyncio.run(check_embedding_space(_VectorPool({"vertex:gemini-embedding-2": 5}), "vertex:gemini-embedding-2"))
    asyncio.run(check_embedding_space(_VectorPool({}), "vertex:gemini-embedding-2"))


def test_an_undrained_queue_refuses_the_run():
    class Pool:
        async def fetchrow(self, sql, *a):
            return {"stale": 1263, "pending": 1263, "oldest": "2026-09-25"}

        async def fetch(self, sql, *a):
            return [{"job_type": "goal_abstraction_placement", "n": 1263}]

    with pytest.raises(PreflightFailed, match="no job worker"):
        asyncio.run(check_queue_alive(Pool()))


def test_ingest_model_is_read_from_settings_not_only_the_process_environment(monkeypatch):
    """A value in backend/.env reaches `settings`, not os.environ; it was silently ignored before."""
    from app.config import settings
    from app.ingest.common import llm

    monkeypatch.delenv("INGEST_MODEL", raising=False)
    monkeypatch.setattr(settings, "ingest_model", "google/gemini-3.8-flash")
    assert llm.ingest_model() == "google/gemini-3.8-flash"
    monkeypatch.setenv("INGEST_MODEL", "deepseek-v3.2")
    assert llm.ingest_model() == "deepseek-v3.2"
