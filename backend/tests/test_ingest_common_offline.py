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
        claimed_recently = 0

        async def fetchrow(self, sql, *a):
            return {"stale": 1263, "pending": 1263, "oldest": "2026-09-25"}

        async def fetchval(self, sql, *a):
            return self.claimed_recently

        async def fetch(self, sql, *a):
            return [{"job_type": "goal_abstraction_placement", "n": 1263}]

    with pytest.raises(PreflightFailed, match="no job worker"):
        asyncio.run(check_queue_alive(Pool()))
    # a backlog behind a worker that IS claiming jobs is not a dead queue
    busy = Pool()
    busy.claimed_recently = 40
    assert asyncio.run(check_queue_alive(busy))["claimed_recently"] == 40


def test_a_deliberately_paused_job_type_is_not_a_dead_queue(monkeypatch):
    """Placement paused for cost (QUEUE_PAUSED_JOB_TYPES): its backlog must not refuse every run (2026-10-01)."""
    seen = []

    class Pool:
        async def fetchrow(self, sql, *a):
            seen.append(a)
            paused = a[1]
            return {"stale": 0 if "goal_abstraction_placement" in paused else 8830, "pending": 0, "oldest": None}

        async def fetchval(self, sql, *a):
            return 0

        async def fetch(self, sql, *a):
            return [{"job_type": "goal_abstraction_placement", "n": 8830}]

    monkeypatch.delenv("QUEUE_PAUSED_JOB_TYPES", raising=False)
    with pytest.raises(PreflightFailed, match="no job worker"):
        asyncio.run(check_queue_alive(Pool()))
    monkeypatch.setenv("QUEUE_PAUSED_JOB_TYPES", "goal_abstraction_placement, goal_abstraction_audit")
    report = asyncio.run(check_queue_alive(Pool()))
    assert report["paused_job_types"] == ["goal_abstraction_placement", "goal_abstraction_audit"]


def test_ingest_model_is_read_from_settings_not_only_the_process_environment(monkeypatch):
    """A value in backend/.env reaches `settings`, not os.environ; it was silently ignored before."""
    from app.config import settings
    from app.ingest.common import llm

    monkeypatch.delenv("INGEST_MODEL", raising=False)
    monkeypatch.setattr(settings, "ingest_model", "google/gemini-3.8-flash")
    assert llm.ingest_model() == "google/gemini-3.8-flash"
    monkeypatch.setenv("INGEST_MODEL", "deepseek-v3.2")
    assert llm.ingest_model() == "deepseek-v3.2"


def test_vertex_calls_are_priced_like_google_not_worst_case():
    from app.services.governance import estimate_cost

    assert estimate_cost("vertex", 1_000_000, 1_000_000) == estimate_cost("google", 1_000_000, 1_000_000) == 7.5


def test_vertex_client_refreshes_an_expiring_token():
    """ADC tokens last ~1h; the client held one fixed token, so long ingestion runs 401'd after an hour."""
    import datetime as dt
    from types import SimpleNamespace as NS

    from app.services.ingestion_jobs import _VertexOAuthCompletions

    class Creds:
        def __init__(self):
            self.token, self.valid, self.refreshed = "old", True, 0
            self.expiry = dt.datetime.utcnow() + dt.timedelta(minutes=2)       # expiring soon

        def refresh(self, _request):
            self.refreshed += 1
            self.token, self.expiry = f"new{self.refreshed}", dt.datetime.utcnow() + dt.timedelta(hours=1)

    calls = []
    inner = NS(api_key="old", chat=NS(completions=NS(create=lambda **kw: calls.append(kw) or "ok")))
    creds = Creds()
    c = _VertexOAuthCompletions(inner, "google/gemini-3.8-flash", creds)
    assert c.create(messages=[]) == "ok" and inner.api_key == "new1" and creds.refreshed == 1
    c.create(messages=[])                       # fresh now: no second refresh
    assert creds.refreshed == 1 and len(calls) == 2


def test_a_run_resumes_after_a_dropped_connection_and_keeps_its_limit(monkeypatch):
    """2026-09-30: a production run died when the network dropped. It now waits, reconnects and resumes; items it
    already decided count against --limit; anything that is not a connection failure still stops the run."""
    from types import SimpleNamespace as NS

    from app.ingest import cli

    monkeypatch.setattr(cli, "_RESUME_WAIT_S", (0,))
    decided = {"n": 0}

    class Pool:
        async def fetchval(self, *_a):
            return decided["n"]

    limits = []

    async def once(limit):
        limits.append(limit)
        if len(limits) == 1:
            decided["n"] = 30
            raise ConnectionAbortedError("[WinError 1236] aborted by the local system")
        return {"ok": True}

    assert asyncio.run(cli._resuming(Pool(), "r", NS(limit=100), once)) == {"ok": True}
    assert limits == [100, 70]

    async def broken(_limit):
        raise ValueError("a real bug")

    with pytest.raises(ValueError):
        asyncio.run(cli._resuming(Pool(), "r", NS(limit=None), broken))


def test_a_worker_stops_itself_before_its_credential_expires():
    import datetime as dt
    from types import SimpleNamespace as NS

    from app.ingestion import worker as w

    class Loop:
        def call_later(self, delay, fn):
            self.delay, self.fn = delay, fn

    stop = asyncio.Event()
    loop = Loop()
    expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
    delay = w._stop_before_expiry(loop, NS(stop=stop), NS(expires_at=expires))
    assert 3600 - w.STOP_BEFORE_EXPIRY_S - 5 <= delay <= 3600 - w.STOP_BEFORE_EXPIRY_S
    loop.fn()
    assert stop.is_set()
    assert w._stop_before_expiry(loop, NS(stop=asyncio.Event()), None) is None


def test_units_run_in_parallel_but_items_of_a_unit_stay_in_order():
    from app.ingest.common.parallel import Stop, run_units

    log, live, peak = [], [0], [0]

    async def handle(item):
        live[0] += 1
        peak[0] = max(peak[0], live[0])
        await asyncio.sleep(0.01)
        log.append(item)
        live[0] -= 1

    units = [[(u, i) for i in range(3)] for u in range(6)]
    asyncio.run(run_units(units, handle, concurrency=4, stop=Stop()))
    assert peak[0] == 4 and len(log) == 18
    for u in range(6):
        assert [i for (uu, i) in log if uu == u] == [0, 1, 2]


def test_a_stop_starts_nothing_new_and_a_failure_cancels_the_rest():
    from app.ingest.common.parallel import Stop, run_units

    stop, seen = Stop(), []

    async def handle(item):
        seen.append(item)
        stop.set("budget")

    asyncio.run(run_units([[1, 2], [3, 4]], handle, concurrency=1, stop=stop))
    assert seen == [1] and stop.reason == "budget"

    async def boom(item):
        if item == "bad":
            raise ConnectionResetError("dropped")
        await asyncio.sleep(10)

    with pytest.raises(ConnectionResetError):
        asyncio.run(asyncio.wait_for(run_units([["bad"], ["slow"]], boom, concurrency=2, stop=Stop()), 5))


def test_an_unreadable_spend_ledger_resumes_but_a_reached_cap_stops(monkeypatch):
    from types import SimpleNamespace as NS

    from app.ingest import cli

    monkeypatch.setattr(cli, "_RESUME_WAIT_S", (0,))

    class Pool:
        async def fetchval(self, *_a):
            return 0

    calls = []

    async def once(limit):
        calls.append(limit)
        return {"stopped": "budget: ingestion model budget ledger unavailable"} if len(calls) == 1 else {"stopped": None}

    assert asyncio.run(cli._resuming(Pool(), "r", NS(limit=None), once)) == {"stopped": None}
    assert len(calls) == 2

    async def capped(limit):
        return {"stopped": "budget: ingestion model budget exceeded: $600.10 of $600.00"}

    assert "exceeded" in asyncio.run(cli._resuming(Pool(), "r", NS(limit=None), capped))["stopped"]


def test_a_database_short_of_memory_is_waited_out():
    import asyncpg

    from app.ingest.cli import _transient

    assert _transient(asyncpg.exceptions.InternalServerError("Couldn't connect to compute node"))
    assert _transient(asyncpg.exceptions.OutOfMemoryError("out of memory"))
    assert not _transient(asyncpg.exceptions.InternalServerError("something else"))
    assert not _transient(ValueError("a real bug"))
