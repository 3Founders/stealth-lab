"""S1 (ingestion_problems.md): GitHub documents can be queued through app.ingestion.enqueue,
not only SKILL.md packages. Pure: a capture pool stands in for asyncpg."""
from __future__ import annotations

import asyncio

import pytest

from app.ingestion import enqueue as e


class _CapPool:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *a):
        self.calls.append(a)
        return {"id": len(self.calls)}


DOC = {"repository": "openai/codex", "path": "AGENTS.md", "commit": "a" * 40, "kind": "agents_md"}


def test_enqueue_documents_queues_ingest_document_jobs_with_stable_identity():
    pool = _CapPool()
    res = asyncio.run(e.enqueue_documents(pool, [DOC, dict(DOC, path="docs/RUNBOOK.md")]))
    assert res["created"] == 2
    assert all(c[0] == "ingest_document" for c in pool.calls)
    assert pool.calls[0][1]["kind"] == "agents_md"   # extra manifest fields ride along in the payload
    assert e.document_key("openai/codex", "a" * 40, "AGENTS.md") == e.document_key("openai/codex", "a" * 40, "AGENTS.md")
    assert e.document_key("openai/codex", "a" * 40, "AGENTS.md") != e.document_key("openai/codex", "b" * 40, "AGENTS.md")


def test_enqueue_documents_requires_a_pinned_commit():
    with pytest.raises(ValueError, match="commit"):
        asyncio.run(e.enqueue_documents(_CapPool(), [dict(DOC, commit=None)]))
