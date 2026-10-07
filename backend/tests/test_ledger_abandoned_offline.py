"""Abandoned provider-call reservations (org_governance.settle_abandoned; securityp1.md P1-C). Offline: the tenant
transaction is faked, so this checks the SQL contract -- each organisation under its own scope, worst-case settlement,
a floor on the age -- not the database. tests/test_org_governance_e2e.py exercises the real trigger and RLS."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from app.services import org_governance as og

ORG_A, ORG_B = "00000000-0000-0000-0000-00000000000a", "00000000-0000-0000-0000-00000000000b"


class FakeConn:
    def __init__(self, log, scope):
        self.log, self.scope = log, scope

    async def execute(self, sql, *args):
        self.log.append(("execute", self.scope, sql, args))
        return "UPDATE 2" if args[0] == ORG_A else "UPDATE 0"

    async def fetchval(self, sql, *args):
        self.log.append(("fetchval", self.scope, sql, args))
        return 3


class FakePool:
    def __init__(self):
        self.log = []

    async def fetch(self, sql, *args):
        self.log.append(("fetch", None, sql, args))
        return [{"id": ORG_A}, {"id": ORG_B}]


@pytest.fixture
def pool(monkeypatch):
    p = FakePool()

    @asynccontextmanager
    async def fake_tx(_pool, scope):
        yield FakeConn(p.log, scope)
    monkeypatch.setattr(og, "tenant_transaction", fake_tx)
    return p


def test_abandoned_reservations_settle_at_the_worst_case_under_each_orgs_scope(pool):
    done = asyncio.run(og.settle_abandoned_all(pool))
    assert done == {ORG_A: 2}
    updates = [e for e in pool.log if e[0] == "execute"]
    assert len(updates) == 2                                    # one tenant transaction per organisation
    for _, scope, sql, args in updates:
        assert "status = 'settled'" in sql and "cost_usd = reserved_usd" in sql and "'upper_bound'" in sql
        assert "error_type = 'abandoned'" in sql and "status = 'reserved'" in sql
        assert args[1] == og.ABANDONED_AFTER_MINUTES
    # each update runs under that organisation's own scope (the ledger has FORCE RLS), never a shared one
    assert [scope for _, scope, _, _ in updates] == [og._scope(ORG_A), og._scope(ORG_B)]
    assert [args[0] for _, _, _, args in updates] == [ORG_A, ORG_B]


def test_a_dry_run_only_counts(pool):
    n = asyncio.run(og.settle_abandoned(pool, ORG_A, apply=False))
    assert n == 3 and not any(e[0] == "execute" for e in pool.log)


def test_a_reservation_that_may_still_be_in_flight_is_never_touched(pool):
    with pytest.raises(og.GovernanceError):
        asyncio.run(og.settle_abandoned(pool, ORG_A, older_than_minutes=1))
    assert pool.log == []
