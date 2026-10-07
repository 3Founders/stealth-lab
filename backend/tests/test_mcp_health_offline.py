"""Liveness, readiness and drain-on-shutdown (app/mcp_server/health.py; securityp1.md P1-C). Offline: fake pools,
the real ASGI app without its lifespan (so no database), and the signal chain on a throwaway signal."""
from __future__ import annotations

import asyncio
import signal

import pytest

from app.mcp_server import health

SECRET_DSN = "postgresql://owner:hunter2@db.internal.example:5432/prod"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fresh():
    health.reset_for_tests()
    yield
    health.reset_for_tests()


class FakePool:
    def __init__(self, select1=1, migrated=True, raise_with=None, hang=False):
        self.select1, self.migrated, self.raise_with, self.hang, self.calls = select1, migrated, raise_with, hang, []

    async def fetchval(self, sql, *args):
        self.calls.append(sql)
        if self.hang:
            await asyncio.sleep(60)
        if self.raise_with is not None:
            raise self.raise_with
        if "schema_migrations" in sql:
            return 1 if self.migrated else None
        return self.select1


def test_ready_when_pool_database_and_migrations_are_ok():
    code, body = run(health.readiness(FakePool(), expected="147_routing_priors.sql"))
    assert code == 200 and body["status"] == "ready"
    assert body["checks"] == {"pool": "ok", "database": "ok", "migrations": "ok"}
    assert body["providers"] == "not_checked"          # a degraded embedding provider never makes us unready


def test_not_ready_without_a_pool():
    code, body = run(health.readiness(None, expected="x.sql"))
    assert code == 503 and body["checks"] == {"pool": "missing"}


def test_a_failing_database_is_503_and_the_body_leaks_nothing():
    code, body = run(health.readiness(FakePool(raise_with=OSError(f"could not connect to {SECRET_DSN}")),
                                      expected="x.sql"))
    assert code == 503 and body["checks"]["database"] == "unavailable"
    text = repr(body)
    for leak in ("hunter2", "db.internal", "postgresql://", "OSError", "Traceback"):
        assert leak not in text


def test_a_hung_database_times_out_quickly(monkeypatch):
    monkeypatch.setattr(health, "READY_DB_TIMEOUT_S", 0.05)
    code, body = run(health.readiness(FakePool(hang=True), expected="x.sql"))
    assert code == 503 and body["checks"]["database"] == "unavailable"


def test_migrations_behind_is_503_and_the_answer_is_cached():
    pool = FakePool(migrated=False)
    code, body = run(health.readiness(pool, expected="147_routing_priors.sql"))
    assert code == 503 and body["checks"]["migrations"] == "behind"
    n = sum("schema_migrations" in c for c in pool.calls)
    run(health.readiness(pool, expected="147_routing_priors.sql"))
    assert sum("schema_migrations" in c for c in pool.calls) == n      # not re-queried within the cache window


def test_draining_answers_503_before_touching_the_database():
    pool = FakePool()
    health.mark_draining("test")
    code, body = run(health.readiness(pool, expected="x.sql"))
    assert code == 503 and body["status"] == "draining" and pool.calls == []


def test_expected_migration_is_the_newest_control_file(tmp_path):
    (tmp_path / "9_a.sql").write_text("select 1;\n", encoding="utf-8")
    (tmp_path / "147_routing_priors.sql").write_text("-- control\n", encoding="utf-8")
    (tmp_path / "148_routing_priors_search.sql").write_text("-- target: search\n", encoding="utf-8")
    (tmp_path / "notes.sql").write_text("x", encoding="utf-8")
    assert health.expected_migration(tmp_path) == "147_routing_priors.sql"


def test_the_real_db_dir_has_an_expected_migration():
    assert health.expected_migration() is not None


def test_probes_are_rate_limited():
    codes = [run(health.healthz_response())[0] for _ in range(int(health.RATE_BURST) + 5)]
    assert codes[0] == 200 and 429 in codes


def test_sigterm_marks_draining_and_still_runs_the_previous_handler():
    sig = getattr(signal, "SIGTERM")
    seen = []
    old = signal.signal(sig, lambda s, f: seen.append(s))
    try:
        restore = health.install_drain_on_signals((sig,))
        signal.getsignal(sig)(sig, None)                  # what the OS would call
        assert health.is_draining() and seen == [sig]
        restore()
        assert signal.getsignal(sig) is not None and not getattr(signal.getsignal(sig), "__name__", "") == "handler"
    finally:
        signal.signal(sig, old)


def test_routes_answer_without_credentials_and_without_a_database():
    from starlette.testclient import TestClient

    import app.mcp_server.server as srv

    client = TestClient(srv.app)                          # no `with`: the lifespan (database pool) does not run
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "alive"}
    r = client.get("/readyz")
    assert r.status_code == 503 and r.json()["checks"] == {"pool": "missing"}
    assert r.headers.get("cache-control") == "no-store"
    assert client.get("/").json()["status"] == "ok"      # the old route is unchanged


def test_the_docker_probe_targets_liveness_and_the_host_probe_targets_readiness():
    import json
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    probe = (root / "backend" / "scripts" / "docker_healthcheck.py").read_text(encoding="utf-8")
    assert "/healthz" in probe
    assert json.loads((root / "railway.mcp.json").read_text(encoding="utf-8"))["deploy"]["healthcheckPath"] == "/readyz"
    assert "--timeout-graceful-shutdown" in (root / "backend" / "Dockerfile.mcp-server").read_text(encoding="utf-8")
