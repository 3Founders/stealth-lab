"""Offline: an ingest job records WHAT it did, not just that it finished.

Measured 2026-09-29 on 30 verified-solution jobs: every one ended as a bare `done`, 12 produced no procedure, and nothing
said whether they were rejected, duplicates or unchanged. The worker now stores an explicit `ingest_outcome` marker a
handler returns; any other return value keeps being ignored, exactly as before."""
from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
base = importlib.import_module("test_verified_solutions_jobs_offline")

from app.ingestion import queue as q  # noqa: E402
from app.ingestion.config import WorkerConfig  # noqa: E402
from app.ingestion.worker import Worker  # noqa: E402
from app.services import object_storage  # noqa: E402


def _job():
    return q.Job(id=7, job_type="t", payload={}, attempt=1, max_attempts=3, worker_id="w", idempotency_key="k",
                 scope_type="global", visibility="public")


@pytest.fixture()
def wired(monkeypatch):
    seen = {"complete": []}

    async def complete(*args):
        seen["complete"].append(args[2:])          # whatever follows (pool, job)
        return True

    async def heartbeat(_pool, _job, _lease):
        return True

    async def hydrate(payload):
        return payload

    monkeypatch.setattr(q, "complete", complete)
    monkeypatch.setattr(q, "heartbeat", heartbeat)
    monkeypatch.setattr(q, "validate_scope", lambda *_a: None)
    monkeypatch.setattr(object_storage, "hydrate_payload", hydrate)
    return seen


def _run_worker(handler):
    worker = Worker(object(), WorkerConfig(lease_seconds=10, job_timeout_seconds=10), handlers={"t": handler})
    return asyncio.run(worker.run_job(_job()))


def test_an_explicit_ingest_outcome_is_stored_on_the_job(wired):
    async def handler(_pool, _payload):
        return {"ingest_outcome": {"status": "rejected", "reason": "the document had no extractable structure"}}

    assert _run_worker(handler) == "done"
    assert wired["complete"] == [({"ingest_outcome": {"status": "rejected", "reason": "the document had no extractable structure"}},)]


@pytest.mark.parametrize("returned", [None, {"counts": {"a": 1}}, {"ingest_outcome": "not-a-dict"}, "text", 3])
def test_every_other_return_value_is_ignored_exactly_as_before(wired, returned):
    async def handler(_pool, _payload):
        return returned

    assert _run_worker(handler) == "done"
    assert wired["complete"] == [()], "complete() gets no usage argument at all, so existing callers and fakes are unaffected"


def test_a_verified_solution_job_reports_its_compile_status_and_reason(monkeypatch):
    import app.services.ingestion_jobs as ij
    from app.services import skill_ingestion

    monkeypatch.setattr(ij, "_general_compute_client", lambda: object())

    async def fake_compile(pool, artifact, **kwargs):
        class _Outcome:
            status = "rejected"
            reason = "no extractable structure"
            version_row_id = None

        return _Outcome()

    monkeypatch.setattr(skill_ingestion, "compile_skill_artifact", fake_compile)
    monkeypatch.setattr(base.vsj, "_trusted_identity_job_id", lambda payload: None, raising=False)
    result = asyncio.run(base.vsj.handle_ingest_verified_solution(base.FakePool(), base._payload()))
    outcome = result["ingest_outcome"]
    assert outcome["status"] == "rejected" and outcome["reason"] == "no extractable structure"
    assert outcome["verified_solution_preserved"] is False and outcome["instance_id"] == "acme__widget-42"


def test_a_captured_item_reports_captured_with_no_reason(monkeypatch):
    import app.services.ingestion_jobs as ij

    monkeypatch.setattr(ij, "_general_compute_client", lambda: object())
    base._record_compile(monkeypatch)
    result = asyncio.run(base.vsj.handle_ingest_verified_solution(base.FakePool(), base._payload()))
    assert result["ingest_outcome"]["status"] == "captured" and result["ingest_outcome"]["reason"] is None
