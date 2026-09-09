"""
Offline tests for scripts/backfill_procedure_embeddings.py -- the
representation + display-metadata backfill. DB-free and provider-free: a
FakePool records UPDATEs and a FakeEmbedder returns fixed vectors (or
raises).

Asserts the production requirements: atomic per-row persist, resumability,
explicit failure recording, and that a failed embedding never leaves a row
claiming the new representation version.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from uuid import uuid4

_SPEC = importlib.util.spec_from_file_location(
    "backfill_procedure_embeddings",
    Path(__file__).resolve().parents[1] / "scripts" / "backfill_procedure_embeddings.py",
)
bf = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = bf
_SPEC.loader.exec_module(bf)


class _Row(dict):
    __getattr__ = dict.get


class FakePool:
    def __init__(self, procedures):
        self._procs = procedures
        self.updates: list[tuple] = []

    async def fetch(self, sql, *args):
        if "FROM procedure_dependencies" in sql:
            return []
        return [_Row(p) for p in self._procs]

    async def execute(self, sql, *args):
        self.updates.append((sql, args))
        return "UPDATE 1"


class FakeEmbedder:
    dimension = 1024

    def __init__(self, *, fail_ids=frozenset(), bad_dim_ids=frozenset()):
        self._fail = fail_ids
        self._bad_dim = bad_dim_ids
        self.calls: list[str] = []

    def embedding_model_id(self):
        return "gemini:gemini-embedding-001"

    def _configured_provider(self):
        return "gemini"

    async def embed(self, texts, input_type="document"):
        self.calls.extend(texts)
        out = []
        for text in texts:
            for tok in self._fail:
                if tok in text:
                    raise RuntimeError(f"provider 429 for {tok}")
            dim = 3 if any(tok in text for tok in self._bad_dim) else self.dimension
            out.append([0.01] * dim)
        return out


def _proc(name, *, token, version=None, sha=None, embedding="[0.0]",
          embedding_model_id=None):
    return {
        "id": uuid4(), "procedure_id": uuid4(), "name": name,
        "goal": f"Do the {name} thing. token:{token}", "steps": [{"goal": "s1"}],
        "preconditions": [], "invariants": [], "postconditions": [],
        "failure_conditions": [], "domain": None, "domain_payload": {},
        "capability_statement": None,
        "retrieval_document_version": version, "retrieval_document_sha256": sha,
        "embedding": embedding, "embedding_model_id": embedding_model_id,
    }


def test_reembeds_and_persists_vector_and_version_atomically():
    pool = FakePool([_proc("alpha", token="A"), _proc("beta", token="B")])
    emb = FakeEmbedder()
    stats = asyncio.run(bf.backfill_representation(pool=pool, embedder=emb))
    assert stats["selected"] == 2 and stats["reembedded"] == 2 and stats["failed"] == 0
    # one UPDATE per row, and it sets BOTH the vector and the version
    assert len(pool.updates) == 2
    for sql, _args in pool.updates:
        assert "embedding = $2::vector" in sql
        assert "retrieval_document_version = $9" in sql
        assert bf.RETRIEVAL_DOCUMENT_VERSION in _args


def test_failed_embedding_records_failure_and_does_not_touch_the_row(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "_FAIL_LOG", tmp_path / "failed.jsonl")
    pool = FakePool([_proc("good", token="G"), _proc("bad", token="XFAIL")])
    emb = FakeEmbedder(fail_ids={"token:XFAIL"})
    stats = asyncio.run(bf.backfill_representation(pool=pool, embedder=emb))
    assert stats["reembedded"] == 1 and stats["failed"] == 1
    # only the good row was written
    assert len(pool.updates) == 1
    logged = [json.loads(l) for l in (tmp_path / "failed.jsonl").read_text().splitlines()]
    assert len(logged) == 1 and logged[0]["name"] == "bad" and "429" in logged[0]["error"]


def test_dimension_mismatch_is_a_failure_not_a_silent_write(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "_FAIL_LOG", tmp_path / "failed.jsonl")
    pool = FakePool([_proc("wrongdim", token="XDIM")])
    emb = FakeEmbedder(bad_dim_ids={"token:XDIM"})
    stats = asyncio.run(bf.backfill_representation(pool=pool, embedder=emb))
    assert stats["failed"] == 1 and pool.updates == []


def test_resumable_skips_rows_already_on_current_version_via_where_clause():
    # A row already on the current version is filtered out by the SQL, so
    # the FakePool would simply not return it; here we prove the fast path
    # for a row whose text is unchanged: version stamped, no provider call.
    p = _proc("stable", token="S", version="procdoc_v0", embedding="[0.1]",
              embedding_model_id="gemini:gemini-embedding-001")
    # precompute the sha the builder will produce for this row
    doc = bf.build_procedure_retrieval_document(_Row(p))
    p["retrieval_document_sha256"] = bf.retrieval_document_sha256(doc)
    pool = FakePool([p])
    emb = FakeEmbedder()
    stats = asyncio.run(bf.backfill_representation(pool=pool, embedder=emb))
    assert stats["unchanged_text"] == 1 and stats["reembedded"] == 0
    assert emb.calls == []  # no embedding provider call


def test_embedding_model_mismatch_forces_reembed_even_if_text_unchanged():
    # Same canonical text + sha, but the stored vector is in a DIFFERENT
    # model's space -> it must be re-embedded, not fast-path stamped.
    p = _proc("stale-space", token="M", version=bf.RETRIEVAL_DOCUMENT_VERSION,
              embedding="[0.1]", embedding_model_id="local:mxbai-embed-large")
    doc = bf.build_procedure_retrieval_document(_Row(p))
    p["retrieval_document_sha256"] = bf.retrieval_document_sha256(doc)
    pool = FakePool([p])
    emb = FakeEmbedder()  # embedding_model_id() -> gemini:gemini-embedding-001
    stats = asyncio.run(bf.backfill_representation(pool=pool, embedder=emb))
    assert stats["reembedded"] == 1 and stats["unchanged_text"] == 0
    assert emb.calls  # provider WAS called
    sql, _args = pool.updates[0]
    assert "embedding = $2::vector" in sql  # the real re-embed UPDATE, not stamp-only


def test_dry_run_writes_nothing():
    pool = FakePool([_proc("x", token="X")])
    stats = asyncio.run(bf.backfill_representation(pool=pool, embedder=FakeEmbedder(), dry_run=True))
    assert pool.updates == [] and stats["skipped_dry"] == 1


def test_display_metadata_backfill_stamps_version_and_reports_fallbacks(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "_DISPLAY_REPORT", tmp_path / "dq.jsonl")
    rows = [
        {"id": uuid4(), "name": "isolate-parallel-agents",
         "goal": "Run several coding agents on one repo without their git operations colliding.",
         "capability_statement": None, "display_name": None, "display_metadata_version": None},
        {"id": uuid4(), "name": "pm-gold-abc-strong", "goal": "pm-gold-abc-strong goal",
         "capability_statement": None, "display_name": None, "display_metadata_version": None},
    ]
    pool = FakePool(rows)
    stats = asyncio.run(bf.backfill_display_metadata(pool=pool))
    assert stats["updated"] == 2 and stats["fallback"] == 1
    versions = {args[3] for _sql, args in pool.updates}
    assert bf.DISPLAY_METADATA_VERSION in versions
    assert bf.DISPLAY_METADATA_FALLBACK_VERSION in versions
    dq = [json.loads(l) for l in (tmp_path / "dq.jsonl").read_text().splitlines()]
    assert dq[0]["name"] == "pm-gold-abc-strong"
