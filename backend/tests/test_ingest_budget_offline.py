"""Ingestion workers obey the SAME daily model budget as the API (llm_spend ledger + DAILY_LLM_BUDGET_USD),
and ops alerts are pure + de-duplicated. Offline: fake pool, fake providers."""
from __future__ import annotations

import asyncio

import pytest

from app.ingestion import ops_alerts
from app.services import ingest_budget
from app.services.governance import BudgetExceeded
from app.services.semantic.chain import SemanticJudge
from app.services.semantic.providers import SemanticProvider


class LedgerPool:
    """Just enough of asyncpg for CostGovernor: spend_since + record."""

    def __init__(self, spent: float = 0.0):
        self.spent, self.rows = spent, []

    async def fetchval(self, sql, *a):
        return self.spent

    async def execute(self, sql, *a):
        self.rows.append(a)
        self.spent += float(a[4])   # estimated_cost
        return "INSERT 0 1"


@pytest.fixture(autouse=True)
def _reset():
    yield
    ingest_budget.uninstall()


def run(coro):
    return asyncio.run(coro)


def test_status_thresholds():
    async def go():
        for spent, want in ((0.0, "ok"), (8.5, "warn"), (10.0, "exceeded"), (25.0, "exceeded")):
            b = ingest_budget.IngestBudget(LedgerPool(spent), cap_usd=10.0, warn_fraction=0.8, cache_s=0)
            assert (await b.status()).state == want
    run(go())


def test_ledger_failure_pauses_instead_of_spending_blind():
    class Broken:
        async def fetchval(self, *a):
            raise RuntimeError("db down")
    b = ingest_budget.IngestBudget(Broken(), cap_usd=10.0, cache_s=0)
    assert run(b.status()).state == "unavailable"
    with pytest.raises(BudgetExceeded):
        run(b.assert_available())


def test_guard_is_noop_without_worker_and_blocks_when_exceeded():
    run(ingest_budget.guard("embedding"))                         # nothing installed: API/tests unchanged
    ingest_budget.install(LedgerPool(50.0), cap_usd=10.0, cache_s=0)
    with pytest.raises(BudgetExceeded):
        run(ingest_budget.guard("embedding"))


class Fake(SemanticProvider):
    def __init__(self, name):
        self.name, self.model, self.calls = name, "m", 0

    def supports(self, capability):
        return True

    async def identity(self, kind, a, b):
        self.calls += 1
        return {"relation": "distinct"}


def test_judge_chain_records_spend_and_stops_when_over_budget():
    pool = LedgerPool(0.0)
    ingest_budget.install(pool, cap_usd=10.0, cache_s=0)
    p = Fake("gemini")
    judge = SemanticJudge([p])
    res = run(judge.judge_identity("goal", "a", "b"))
    assert res.ok and p.calls == 1
    assert pool.rows[0][1] == "google"                       # priced as a paid provider
    assert pool.rows[0][0] == ingest_budget.INGEST_SCOPE_KEY
    pool.spent = 99.0                                              # budget now gone
    ingest_budget._ACTIVE._cached = None
    with pytest.raises(BudgetExceeded):
        run(judge.judge_identity("goal", "a", "b"))
    assert p.calls == 1                                            # the paid call never happened


def test_embedding_spend_recorded():
    pool = LedgerPool(0.0)
    ingest_budget.install(pool, cap_usd=10.0, cache_s=0)
    run(ingest_budget.record_embedding("gemini", "gemini-embedding-001", ["x" * 400, "y" * 400]))
    assert pool.rows and pool.rows[0][3] == "embedding"


def _metrics(**over):
    m = {
        "jobs": {"queued": 0, "running": 0, "expired_leases": 0, "retryable_failed": 0, "retryable_failed_repeat": 0, "completed": 5,
                 "permanent_failed": 0, "permanent_failed_60m": 0, "throughput_per_min": 1.0, "p95_duration_s": 3.0},
        "providers": {"embedding_failures_15m": 0, "judge_failures_15m": 0, "judge_unavailable_60m": 0, "judge_decisions_60m": 0,
                      "fallback_rate_60m": 0.0, "primary_judge": "jev", "rate_limited_15m": 0},
        "retrieval": {"requests_60m": 0, "degraded_rate_60m": 0.0},
        "projection": {"pending": 0, "failed": 0, "oldest_pending_s": 0.0},
        "shards": [{"shard_id": "K000", "status": "active", "reachable": True, "size_bytes": 10, "connection_use": 0.1,
                    "connections": 5, "max_connections": 100}],
        "cost": {"state": "ok", "spent_24h_usd": 1.0, "daily_cap_usd": 10.0, "fraction": 0.1},
    }
    for k, v in over.items():
        m[k].update(v) if isinstance(v, dict) else m.__setitem__(k, v)
    return m


T = ops_alerts.Thresholds(shard_warn_bytes=100, shard_rollover_bytes=200)


def keys(m, verify=None):
    return {a.key for a in ops_alerts.evaluate(m, T, verify)}


def test_healthy_snapshot_raises_nothing():
    assert keys(_metrics()) == set()


def test_each_ingestion_blocker_has_an_alert():
    assert "ingestion.permanent_failure" in keys(_metrics(jobs={"permanent_failed_60m": 1}))
    assert "ingestion.repeated_retryable_failures" in keys(_metrics(jobs={"retryable_failed_repeat": 9}))
    assert "ingestion.expired_leases_spike" in keys(_metrics(jobs={"expired_leases": 9}))
    assert "projection.lag" in keys(_metrics(projection={"oldest_pending_s": 9999.0}))
    assert "projection.outbox_failure" in keys(_metrics(projection={"failed": 2}))
    assert "provider.embedding_outage" in keys(_metrics(providers={"embedding_failures_15m": 9}))
    assert "provider.judge_outage" in keys(_metrics(providers={"judge_failures_15m": 9}))
    assert "provider.semantic_fallback_spike" in keys(_metrics(providers={"judge_decisions_60m": 50, "fallback_rate_60m": 0.6}))
    assert "retrieval.degraded" in keys(_metrics(retrieval={"requests_60m": 40, "degraded_rate_60m": 0.5}))
    assert "cost.budget_near_limit" in keys(_metrics(cost={"state": "warn", "fraction": 0.85}))
    assert "cost.budget_exceeded" in keys(_metrics(cost={"state": "exceeded"}))


def test_shard_alerts_and_verify_failures():
    down = {"shard_id": "K001", "status": "active", "reachable": False, "error": "boom"}
    assert "shard.unavailable.K001" in keys(_metrics(shards=[down]))
    big = {"shard_id": "K002", "status": "active", "reachable": True, "size_bytes": 250, "connection_use": 0.95, "connections": 95, "max_connections": 100}
    k = keys(_metrics(shards=[big]))
    assert {"shard.capacity_rollover.K002", "db.connection_saturation.K002"} <= k
    warn = dict(big, size_bytes=150, connection_use=0.1)
    assert "shard.capacity_warn.K002" in keys(_metrics(shards=[warn]))
    assert "verify.verify-dedup" in keys(_metrics(), {"verify-dedup": False, "verify-refs": True})


def test_noise_control_warns_after_consecutive_checks_and_renotifies_only_after_cooldown():
    class StatePool:
        def __init__(self):
            self.rows = {}

        async def fetch(self, sql, *a):
            return [dict(r, alert_key=k) for k, r in self.rows.items() if "verify:" not in sql or k.startswith("verify:")] \
                if "ops_alert_state" in sql and "LIKE" not in sql else []

        async def fetchval(self, sql, *a):
            return False   # cooldown NOT elapsed

        async def execute(self, sql, key, consecutive=None, severity=None, notified=None, detail=None):
            if sql.startswith("INSERT"):
                prev = self.rows.get(key, {})
                self.rows[key] = {"firing": True, "consecutive": consecutive, "severity": severity,
                                  "last_notified": True if notified else prev.get("last_notified")}
            else:
                self.rows[key]["firing"] = False

    sent = []
    pool = StatePool()
    m = _metrics(jobs={"expired_leases": 9})                       # a WARNING alert (needs 2 consecutive checks)
    for _ in range(4):
        run(ops_alerts.run(pool, m, T, send=sent.append))
    assert len(sent) == 1 and "expired leases" in sent[0]          # 1st check quiet, 2nd notifies, 3rd/4th silent (cooldown)
    run(ops_alerts.run(pool, _metrics(), T, send=sent.append))
    assert len(sent) == 2 and sent[1].startswith("[RESOLVED]")
