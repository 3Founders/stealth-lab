"""Regression tests for provider-neutral distributed skill workers."""
from __future__ import annotations

import pytest

from app.services.embeddings import EmbeddingError, Embedder
from app.services.ingestion_jobs import enqueue_skill_package_jobs


class RecordingQueuePool:
    def __init__(self) -> None:
        self.fetches: list[tuple[str, tuple]] = []
        self.executions: list[tuple[str, tuple]] = []

    async def fetchval(self, sql, *params):
        self.fetches.append((sql, params))
        return None

    async def execute(self, sql, *params):
        self.executions.append((sql, params))
        return "INSERT 0 1"


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _LegacyQueuePool:
    def __init__(self, row):
        self.row = row
        self.fetches: list[tuple[str, tuple]] = []
        self.executions: list[tuple[str, tuple]] = []

    def acquire(self):
        return _AsyncContext(self)

    def transaction(self):
        return _AsyncContext(self)

    async def fetch(self, sql, *params):
        self.fetches.append((sql, params))
        return [dict(self.row)]

    async def execute(self, sql, *params):
        self.executions.append((sql, params))
        return "UPDATE 1"


@pytest.mark.parametrize(
    ("scope_type", "visibility", "owner_id"),
    [("user", "private", "alice"), ("organization", "org", "tenant-1")],
)
@pytest.mark.asyncio
async def test_document_enqueue_refuses_private_and_org_scope(scope_type, visibility, owner_id):
    from app.ingestion import queue as q

    with pytest.raises(q.ScopeError, match="writes global public knowledge"):
        await q.enqueue(
            object(),
            "ingest_document",
            {},
            idempotency_key="document-scope",
            scope_type=scope_type,
            scope_entity_id=owner_id,
            owner_id=owner_id,
            visibility=visibility,
            offload=False,
        )


@pytest.mark.asyncio
async def test_skill_package_enqueue_uses_json_object_and_decodes_legacy_shape():
    pool = RecordingQueuePool()
    payload_spec = {
        "source_id": "example", "priority": 1, "source_type": "github",
        "repo": "https://github.com/example/skills", "ref": "main",
    }
    refs = [{"uri": "https://example.test/SKILL.md", "path": "SKILL.md", "commit": "a" * 40}]

    assert await enqueue_skill_package_jobs(pool, source_spec=payload_spec, refs=refs) == 1
    dedupe_sql, _ = pool.fetches[0]
    assert "jsonb_typeof(payload)='string'" in dedupe_sql
    _, insert_params = pool.executions[0]
    assert isinstance(insert_params[1], dict)
    assert insert_params[1]["source_id"] == "example"


@pytest.mark.asyncio
async def test_embedder_does_not_silently_fallback_to_another_vector_space(monkeypatch):
    from app.services import embeddings

    monkeypatch.setattr(embeddings.settings, "use_local_models", False)
    monkeypatch.setattr(embeddings.settings, "embedding_provider_chain", "gemini,voyage")

    embedder = Embedder()
    voyage_called = False

    async def gemini_fails(texts, input_type):
        raise EmbeddingError("quota exhausted")

    async def voyage_must_not_run(texts, input_type):
        nonlocal voyage_called
        voyage_called = True
        return [[0.1] * 1024 for _ in texts]

    monkeypatch.setattr(embedder, "_embed_gemini", gemini_fails)
    monkeypatch.setattr(embedder, "_embed_voyage", voyage_must_not_run)

    with pytest.raises(EmbeddingError, match="quota exhausted"):
        await embedder.embed_one("debug a failing test")
    assert voyage_called is False


@pytest.mark.asyncio
async def test_embedding_metadata_records_the_selected_vector_space(monkeypatch):
    from app.services import embeddings

    monkeypatch.setattr(embeddings.settings, "use_local_models", False)
    monkeypatch.setattr(embeddings.settings, "embedding_provider_chain", "gemini")
    embedder = Embedder()

    async def gemini_succeeds(texts, input_type):
        return [[0.1] * 1024 for _ in texts]

    monkeypatch.setattr(embedder, "_embed_gemini", gemini_succeeds)
    vector, metadata = await embedder.embed_one_with_metadata("debug a failing test")

    assert len(vector) == 1024
    assert metadata.provider == "gemini"
    assert metadata.model_id == "gemini:gemini-embedding-001"
    assert metadata.input_type == "document"
    assert len(metadata.text_sha256) == 64


@pytest.mark.asyncio
async def test_worker_overwrites_untrusted_job_context(monkeypatch):
    from app.ingestion import queue as q
    from app.ingestion.config import WorkerConfig
    from app.ingestion.worker import Worker
    from app.services import object_storage

    seen = []

    async def handler(_pool, payload):
        seen.append(payload)

    async def complete(_pool, _job):
        return True

    async def heartbeat(_pool, _job, _lease_seconds):
        return True

    async def hydrate(payload):
        return payload

    monkeypatch.setattr(q, "complete", complete)
    monkeypatch.setattr(q, "heartbeat", heartbeat)
    monkeypatch.setattr(q, "validate_scope", lambda *_args: None)
    monkeypatch.setattr(object_storage, "hydrate_payload", hydrate)

    job = q.Job(
        id=41,
        job_type="test",
        payload={
            "value": "kept",
            "_job": {
                "id": 999,
                "attempt": 99,
                "idempotency_key": "forged",
                "scope_type": "user",
                "scope_entity_id": "forged",
                "owner_id": "forged",
                "visibility": "private",
                "source_id": "forged",
                "config_version": "forged",
                "extra": "forged",
            },
        },
        attempt=3,
        max_attempts=5,
        worker_id="worker-1",
        idempotency_key="row-key",
        scope_type="global",
        visibility="public",
    )
    worker = Worker(object(), WorkerConfig(lease_seconds=10, job_timeout_seconds=10), handlers={"test": handler})

    assert await worker.run_job(job) == "done"
    assert seen[0]["value"] == "kept"
    assert seen[0]["_job"] == {
        "id": 41,
        "attempt": 3,
        "idempotency_key": "row-key",
        "scope_type": "global",
        "scope_entity_id": None,
        "owner_id": None,
        "visibility": "public",
        "source_id": None,
        "config_version": None,
    }


@pytest.mark.asyncio
async def test_legacy_process_replaces_forged_job_metadata(monkeypatch):
    from app.services import ingestion_jobs

    seen = []

    async def handler(_pool, payload):
        seen.append(payload)

    row = {
        "id": 81,
        "job_type": "test",
        "payload": {
            "value": "kept",
            "_job": {
                "id": 999,
                "attempt": 99,
                "idempotency_key": "forged",
                "scope_type": "user",
                "scope_entity_id": "forged",
                "owner_id": "forged",
                "visibility": "private",
                "source_id": "forged",
                "config_version": "forged",
                "extra": "forged",
            },
        },
        "attempts": 3,
        "scope_type": "global",
        "scope_entity_id": None,
        "owner_id": None,
        "visibility": "public",
        "idempotency_key": "row-key",
        "source_id": "source-row",
        "config_version": "config-row",
    }
    pool = _LegacyQueuePool(row)
    monkeypatch.setitem(ingestion_jobs.JOB_HANDLERS, "test", handler)

    result = await ingestion_jobs._process_pending_jobs(
        pool, limit=1, job_types=None, worker_id="legacy"
    )

    assert result["done"] == 1
    assert seen[0]["value"] == "kept"
    assert seen[0]["_job"] == {
        "id": 81,
        "attempt": 3,
        "idempotency_key": "row-key",
        "scope_type": "global",
        "scope_entity_id": None,
        "owner_id": None,
        "visibility": "public",
        "source_id": "source-row",
        "config_version": "config-row",
    }
    select_sql = pool.fetches[0][0]
    for column in (
        "scope_type",
        "scope_entity_id",
        "owner_id",
        "visibility",
        "idempotency_key",
        "source_id",
        "config_version",
    ):
        assert column in select_sql


@pytest.mark.asyncio
async def test_legacy_process_refuses_invalid_document_scope_before_handler(monkeypatch):
    from app.services import ingestion_jobs

    called = []

    async def handler(_pool, _payload):
        called.append(True)

    row = {
        "id": 82,
        "job_type": "ingest_document",
        "payload": {"_job": {"id": 999}},
        "attempts": 1,
        "scope_type": "user",
        "scope_entity_id": "alice",
        "owner_id": "alice",
        "visibility": "private",
        "idempotency_key": "document-key",
        "source_id": None,
        "config_version": None,
    }
    pool = _LegacyQueuePool(row)
    monkeypatch.setitem(ingestion_jobs.JOB_HANDLERS, "ingest_document", handler)

    result = await ingestion_jobs._process_pending_jobs(
        pool, limit=1, job_types=None, worker_id="legacy"
    )

    assert result == {
        "claimed": 1,
        "done": 0,
        "failed": 1,
        "unknown_type": 0,
        "worker_id": "legacy",
    }
    assert called == []
    failure_sql, failure_params = pool.executions[1]
    assert "status = 'failed'" in failure_sql
    assert "scope refused" in failure_params[1]
