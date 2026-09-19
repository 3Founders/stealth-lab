"""Migration 95: fresh database AND upgrade from the current baseline (94) with
representative existing rows; re-run is a no-op; backfill is replayable.

Creates and drops a throwaway database on the same server as DATABASE_URL
(needs CREATEDB; skips otherwise)."""
import importlib.util
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

DSN = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="requires DATABASE_URL (a disposable server)")
BACKEND = Path(__file__).resolve().parents[1]


def _with_db(dsn: str, name: str) -> str:
    p = urlsplit(dsn)
    return urlunsplit((p.scheme, p.netloc, "/" + name, p.query, p.fragment))


def _load_migrate():
    spec = importlib.util.spec_from_file_location("migrate_under_test", BACKEND / "scripts" / "migrate.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migrate_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.asyncio
async def test_upgrade_from_94_with_existing_rows_then_rerun(tmp_path):
    name = f"mig95_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(DSN)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    except asyncpg.InsufficientPrivilegeError:
        await admin.close()
        pytest.skip("no CREATEDB privilege")
    dsn = _with_db(DSN, name)
    try:
        conn = await asyncpg.connect(dsn)
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.close()

        # -- baseline: everything before 95
        base = tmp_path / "db94"
        base.mkdir()
        for f in sorted((BACKEND / "db").glob("*.sql")):
            if int(re.match(r"\d+", f.name).group()) < 95:
                shutil.copy(f, base / f.name)
        mig = _load_migrate()
        real_dir = mig.DB_DIR
        mig.DB_DIR = base
        assert await mig.run(dsn) == 0

        # -- representative existing rows (old schema)
        conn = await asyncpg.connect(dsn)
        gid = str(uuid.uuid4())
        await conn.execute("INSERT INTO goals (id, canonical_name, normalized_name, status, scope_type) "
                           "VALUES ($1::uuid, 'legacy goal', 'legacy goal', 'active', 'global')", gid)
        pid = str(uuid.uuid4())
        await conn.execute("INSERT INTO procedures (procedure_id, name, goal, steps, scope_type, achieves_goal_id) "
                           "VALUES ($1::uuid, 'legacy proc', 'legacy goal', '[]', 'global', $2::uuid)", pid, gid)
        cid = str(await conn.fetchval("INSERT INTO knowledge_nodes (node_type, name, scope_type, claim_status) "
                                      "VALUES ('claim', 'legacy claim', 'global', 'supported') RETURNING id"))
        jid = await conn.fetchval("INSERT INTO ingestion_jobs (job_type, payload, status, attempts) "
                                  "VALUES ('normalize_trace_event', '{}'::jsonb, 'processing', 1) RETURNING id")
        await conn.close()

        # -- upgrade
        mig.DB_DIR = real_dir
        assert await mig.run(dsn) == 0
        assert await mig.run(dsn) == 0                      # re-run: ledger says applied, nothing happens

        conn = await asyncpg.connect(dsn)
        routes = {(r["object_type"], str(r["object_id"])): r["home_shard_id"] for r in await conn.fetch("SELECT * FROM object_routes")}
        assert routes[("goal", gid)] == routes[("procedure", pid)] == routes[("claim", cid)] == "K000"
        pending = {(r["object_type"], str(r["object_id"])) for r in await conn.fetch("SELECT * FROM projection_outbox WHERE status='pending'")}
        assert {("goal", gid), ("procedure", pid), ("claim", cid)} <= pending          # backfill queued, not inlined
        assert await conn.fetchval("SELECT reconciled_at IS NOT NULL FROM goals WHERE id=$1::uuid", gid)   # legacy goals are not re-judged
        job = await conn.fetchrow("SELECT status, attempts, max_attempts, lease_until FROM ingestion_jobs WHERE id=$1", jid)
        assert (job["status"], job["attempts"], job["max_attempts"], job["lease_until"]) == ("processing", 1, 5, None)
        assert await conn.fetchval("SELECT status FROM knowledge_shards WHERE shard_id='K000'") == "active"
        # the old status vocabulary is still accepted, the new one added
        await conn.execute("UPDATE ingestion_jobs SET status='retryable_failed' WHERE id=$1", jid)
        await conn.execute("UPDATE ingestion_jobs SET status='done' WHERE id=$1", jid)
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute("UPDATE ingestion_jobs SET status='bogus' WHERE id=$1", jid)
        # backfill is replayable: draining builds the projections for the pre-existing rows
        from app.services import search_projection as sp

        class P:  # minimal pool facade over one connection (drain needs acquire/fetch/execute)
            def __init__(self, c): self.c = c
            def acquire(self): return _Acq(self.c)
            async def fetch(self, *a): return await self.c.fetch(*a)
            async def fetchval(self, *a): return await self.c.fetchval(*a)
            async def fetchrow(self, *a): return await self.c.fetchrow(*a)
            async def execute(self, *a): return await self.c.execute(*a)
        class _Acq:
            def __init__(self, c): self.c = c
            async def __aenter__(self): return self.c
            async def __aexit__(self, *a): return False

        res = await sp.drain_outbox(P(conn))
        assert res["applied"] >= 3 and res["failed"] == 0
        rep = await sp.verify_projection(P(conn))
        assert rep["ok"], rep
        assert await conn.fetchval("SELECT goal_id::text FROM procedure_search_index WHERE procedure_id=$1::uuid", pid) == gid
        await conn.close()
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


@pytest.mark.asyncio
async def test_fresh_database_gets_all_95_objects():
    conn = await asyncpg.connect(DSN)
    try:
        for t in ("knowledge_shards", "object_routes", "goal_relations", "identity_decisions", "retrieval_decisions",
                  "goal_search_index", "procedure_search_index", "claim_search_index", "projection_outbox"):
            assert await conn.fetchval("SELECT to_regclass($1)", f"public.{t}") is not None, t
        cols = {r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name='ingestion_jobs'")}
        assert {"lease_until", "idempotency_key", "scope_type", "max_attempts", "started_at"} <= cols
    finally:
        await conn.close()
