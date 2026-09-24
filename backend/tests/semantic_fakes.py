"""Mocked providers / queue pool for the semantic-judge + compaction tests.
No network, no paid calls."""
from __future__ import annotations

import json
from typing import Callable, Optional

from app.services.semantic.chain import SemanticJudge
from app.services.semantic.errors import ErrorKind, ProviderError
from app.services.semantic.policy import RetryPolicy, SemanticMetrics
from app.services.semantic.providers import ALL_CAPS, SemanticProvider


def transient(msg="boom"):
    return ProviderError(ErrorKind.TRANSIENT, msg)


def permanent(msg="bad key"):
    return ProviderError(ErrorKind.PERMANENT, msg)


class ScriptedProvider(SemanticProvider):
    """Each call pops the next scripted outcome (an Exception is raised, a
    callable is invoked with the call args, anything else is returned). When
    the script runs out the LAST outcome repeats."""

    def __init__(self, name, script, caps=ALL_CAPS, model=None):
        self.name = name
        self.model = model or f"{name}-model"
        self.capabilities = frozenset(caps)
        self._script = list(script)
        self.calls: list[tuple] = []

    def _next(self, op, *args):
        self.calls.append((op, *args))
        out = self._script.pop(0) if len(self._script) > 1 else self._script[0]
        if isinstance(out, BaseException):
            raise out
        if callable(out):
            return out(*args)
        return out

    async def applicability(self, goal, candidates):
        return self._next("applicability", goal, candidates)

    async def retention(self, state, units):
        return self._next("retention", state, units)

    async def summarize(self, state, units):
        return self._next("summary", state, units)

    async def claim_relation(self, a, b):
        return self._next("claim_relation", a, b)

    async def identity(self, kind, a, b):
        return self._next("identity", kind, a, b)

    async def identity_batch(self, kind, a, candidates):
        return self._next("identity_batch", kind, a, candidates)


def make_judge(*providers, attempts=2, deadline_s=None, metrics=None, job_max_retries=3):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    judge = SemanticJudge(
        list(providers),
        RetryPolicy(per_provider_attempts=attempts, job_max_retries=job_max_retries, backoff_base_s=1.0,
                    backoff_max_s=4.0, timeout_s=5.0, deadline_s=deadline_s),
        metrics or SemanticMetrics(), sleep=fake_sleep, rng=lambda: 0.5)
    judge.sleeps = sleeps
    return judge


class QueuePool:
    """Minimal in-memory stand-in for the `ingestion_jobs` queue (only the
    statements semantic/jobs.py issues) + the compaction view tables."""

    def __init__(self):
        self.rows: list[dict] = []
        self.views: list[dict] = []
        self.cache_rows: list[tuple] = []

    async def fetchval(self, sql, *a):
        if sql.startswith("SELECT id FROM ingestion_jobs WHERE job_type"):
            job_type, key = a
            for r in self.rows:
                if r["job_type"] == job_type and r["status"] in ("pending", "processing") \
                        and r["payload"].get("dedup_key") == key:
                    return r["id"]
            return None
        if sql.startswith("INSERT INTO ingestion_jobs"):
            job_type, payload, delay = a
            row = {"id": len(self.rows) + 1, "job_type": job_type, "payload": json.loads(payload),
                   "status": "pending", "delay": float(delay), "last_error": None}
            self.rows.append(row)
            return row["id"]
        if sql.startswith("SELECT 1 FROM ingestion_jobs"):
            job_type, prefix, marker, _minutes = a
            for r in self.rows:
                if r["job_type"] == job_type and r["status"] == "failed" \
                        and r["payload"].get("dedup_key", "").startswith(prefix) \
                        and marker in (r["last_error"] or ""):
                    return 1
            return None
        raise AssertionError(f"unexpected fetchval: {sql[:60]}")

    async def execute(self, sql, *a):
        if sql.startswith("UPDATE ingestion_jobs SET payload = payload ||"):
            for r in self.rows:
                if r["id"] == a[0]:
                    r["payload"]["root_job_id"] = r["id"]
            return "UPDATE 1"
        if sql.startswith("INSERT INTO applicability_judgment_cache"):
            self.cache_rows.append(a)
            return "INSERT 1"
        if sql.startswith("INSERT INTO context_compaction_views"):
            self.views.append({"status": a[6], "view": json.loads(a[7]), "session_id": a[0]})
            return "INSERT 1"
        raise AssertionError(f"unexpected execute: {sql[:60]}")

    async def fetchrow(self, sql, *a):
        if "FROM context_compaction_views" in sql:
            ok = [v for v in self.views if v["status"] == "compacted" and v["session_id"] == a[0]]
            return {"view": ok[-1]["view"]} if ok else None
        raise AssertionError(f"unexpected fetchrow: {sql[:60]}")

    def pending(self, job_type: Optional[str] = None):
        return [r for r in self.rows if r["status"] == "pending" and (job_type is None or r["job_type"] == job_type)]
