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
