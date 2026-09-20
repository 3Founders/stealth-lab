"""Object storage for large raw payloads: stores (offline), and the queue/worker path (live DB)."""
import io
import os
import uuid
from dataclasses import replace

import pytest
import pytest_asyncio

from app.ingestion import queue as q
from app.ingestion.config import WorkerConfig
from app.ingestion.worker import Worker, is_retryable
from app.services import object_storage as os_
from app.services.object_storage import (
    LocalFileStore, MemoryStore, ObjectStoreCorrupt, ObjectStoreUnavailable, PayloadTooLarge, S3Store,
    hydrate_payload, offload_payload,
)

DB = os.environ.get("DATABASE_URL")


class _Pool:  # records raw_objects inserts without a database (offline tests)
    def __init__(self):
        self.rows = []

    async def execute(self, sql, *a):
        self.rows.append(a)


@pytest.mark.asyncio
async def test_file_store_round_trip_is_content_addressed_and_idempotent(tmp_path):
    st = LocalFileStore(str(tmp_path))
    sha1, loc1 = await st.put(b"hello world")
    sha2, loc2 = await st.put(b"hello world")
    assert (sha1, loc1) == (sha2, loc2) and await st.get(loc1) == b"hello world"
    with pytest.raises(ObjectStoreCorrupt):
        await st.get(loc1.replace(sha1, "0" * 64))


@pytest.mark.asyncio
async def test_offload_keeps_small_strings_inline_and_moves_big_ones_out(monkeypatch):
    monkeypatch.setenv("RAW_PAYLOAD_INLINE_MAX_BYTES", "100")
    st, pool = MemoryStore(), _Pool()
    big = "x" * 5000
    out = await offload_payload(pool, {"goal": "small", "raw_text": big, "n": 3}, store=st)
    assert out["goal"] == "small" and out["n"] == 3 and "$blob" in out["raw_text"] and len(str(out)) < 400
    assert pool.rows and pool.rows[0][3] == 5000                       # locator recorded in raw_objects
    back = await hydrate_payload(out, store=st)
    assert back["raw_text"] == big


@pytest.mark.asyncio
async def test_hydrate_detects_corruption_and_reports_outage_separately(monkeypatch):
    monkeypatch.setenv("RAW_PAYLOAD_INLINE_MAX_BYTES", "10")
    st, pool = MemoryStore(), _Pool()
    out = await offload_payload(pool, {"raw": "y" * 500}, store=st)
    loc = out["raw"]["$blob"]["locator"]
    st.objects[loc] = b"tampered"
    with pytest.raises(ObjectStoreCorrupt):
        await hydrate_payload(out, store=st)
    st.fail_next = ObjectStoreUnavailable("503")
    with pytest.raises(ObjectStoreUnavailable):
        await hydrate_payload(out, store=st)
    assert is_retryable(ObjectStoreUnavailable("x")) and not is_retryable(ObjectStoreCorrupt("x"))


@pytest.mark.asyncio
async def test_without_a_store_a_huge_payload_is_refused_not_stored_inline(monkeypatch):
    monkeypatch.setenv("RAW_PAYLOAD_INLINE_MAX_BYTES", "100")
    monkeypatch.setenv("RAW_PAYLOAD_HARD_MAX_BYTES", "1000")
    os_.set_store(None)
    with pytest.raises(PayloadTooLarge):
        await offload_payload(_Pool(), {"raw": "z" * 5000})
    assert (await offload_payload(_Pool(), {"raw": "z" * 500}))["raw"] == "z" * 500      # between the limits: inline, allowed


def test_memory_store_is_refused_in_production(monkeypatch):
    monkeypatch.setenv("STEALTHLAB_ENV", "PRODUCTION")
    monkeypatch.setenv("OBJECT_STORAGE_URL", "memory://")
    os_._STORE_LOADED = False
    with pytest.raises(RuntimeError, match="not allowed"):
        os_.get_store()
    os_._STORE_LOADED = False
    from app.ingestion.config import validate_startup
    assert any("memory://" in p for p in validate_startup(strict=True))


class _FakeS3:
    class _Err(Exception):
        def __init__(self, code):
            self.response = {"Error": {"Code": code}}

    def __init__(self):
        self.objs, self.down = {}, False

    def head_object(self, Bucket, Key):
        if self.down:
            raise self._Err("SlowDown")
        if Key not in self.objs:
            raise self._Err("404")

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objs[Key] = Body

    def get_object(self, Bucket, Key):
        if self.down:
            raise self._Err("InternalError")
        if Key not in self.objs:
            raise self._Err("NoSuchKey")
        return {"Body": io.BytesIO(self.objs[Key])}


@pytest.mark.asyncio
async def test_s3_store_semantics_with_a_fake_client():
    c = _FakeS3()
    st = S3Store("bkt", "pfx", client=c)
    sha, loc = await st.put(b"data")
    assert loc.startswith("s3://bkt/pfx/") and await st.get(loc) == b"data"
    await st.put(b"data")                                       # idempotent: head finds it
    c.down = True
    with pytest.raises(ObjectStoreUnavailable):
        await st.get(loc)
    with pytest.raises(ObjectStoreUnavailable):
        await st.put(b"other")                                  # an outage is NOT mistaken for "not found"
    c.down = False
    c.objs.clear()
    with pytest.raises(ObjectStoreCorrupt):
        await st.get(loc)


# ---------------------------------------------------------------- queue + worker (live DB)

@pytest_asyncio.fixture
async def pool():
    if not DB:
        pytest.skip("requires DATABASE_URL")
    from app.db.session import create_pool
    p = await create_pool()
    yield p
    await p.execute("DELETE FROM ingestion_jobs WHERE idempotency_key LIKE 'objst-%'")
    os_.set_store(None)
    await p.close()


@pytest.mark.asyncio
async def test_large_payload_is_offloaded_at_enqueue_and_hydrated_for_the_handler(pool, monkeypatch, tmp_path):
    monkeypatch.setenv("RAW_PAYLOAD_INLINE_MAX_BYTES", "1000")
    os_.set_store(LocalFileStore(str(tmp_path)))
    big = "raw document line\n" * 5000
    tag = uuid.uuid4().hex[:8]
    jid, _ = await q.enqueue(pool, "objst_test", {"raw_text": big, "k": 1}, idempotency_key=f"objst-{tag}", scope_type="global")
    row = await pool.fetchval("SELECT payload FROM ingestion_jobs WHERE id=$1", jid)
    assert len(str(row)) < 600 and "$blob" in row["raw_text"]                       # the queue row is small
    assert await pool.fetchval("SELECT count(*) FROM raw_objects WHERE sha256=$1", row["raw_text"]["$blob"]["sha256"]) == 1
    seen = {}

    async def handler(p, payload):
        seen.update(payload)

    cfg = replace(WorkerConfig(), concurrency=1, drain_projections=False, reconcile_goals=False)
    r = await Worker(pool, cfg, handlers={"objst_test": handler}, job_types=["objst_test"]).run(loop=False)
    assert r["done"] == 1 and seen["raw_text"] == big                               # handler saw the real content


@pytest.mark.asyncio
async def test_store_outage_is_retryable_and_missing_object_is_permanent(pool, monkeypatch):
    monkeypatch.setenv("RAW_PAYLOAD_INLINE_MAX_BYTES", "100")
    st = MemoryStore()
    os_.set_store(st)
    tag = uuid.uuid4().hex[:8]
    await q.enqueue(pool, "objst_test", {"raw": "q" * 2000}, idempotency_key=f"objst-a-{tag}", scope_type="global")
    await q.enqueue(pool, "objst_test", {"raw": "w" * 2000}, idempotency_key=f"objst-b-{tag}", scope_type="global")
    got = []

    async def handler(p, payload):
        got.append(len(payload["raw"]))

    cfg = replace(WorkerConfig(), concurrency=1, retry_base_seconds=3600.0, retry_cap_seconds=3600.0, drain_projections=False,
                  reconcile_goals=False)
    for k, v in list(st.objects.items()):                   # job b's object is lost -> permanent
        if v == b"w" * 2000:
            del st.objects[k]
    st.fail_next = ObjectStoreUnavailable("503")            # job a hits an outage once -> retryable, then succeeds
    r1 = await Worker(pool, cfg, handlers={"objst_test": handler}, job_types=["objst_test"]).run(loop=False)
    assert r1["retryable_failed"] == 1 and r1["failed"] == 1 and not got
    await pool.execute("UPDATE ingestion_jobs SET run_after = now() WHERE status='retryable_failed' AND idempotency_key LIKE 'objst-%'")
    r2 = await Worker(pool, cfg, handlers={"objst_test": handler}, job_types=["objst_test"]).run(loop=False)
    assert r2["done"] == 1 and got == [2000]
