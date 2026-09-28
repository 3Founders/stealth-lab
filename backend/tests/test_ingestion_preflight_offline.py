"""Offline: writing ingestion pilots refuse to start on a database with pending migrations
(app/ingestion/preflight.py; docs/ingestion_review.md lesson 2)."""
from __future__ import annotations

import asyncio

import pytest

from app.ingestion import preflight


class FakePool:
    def __init__(self, applied, ledger=True):
        self.applied, self.ledger = applied, ledger

    async def fetchval(self, sql, *a):
        return self.ledger

    async def fetch(self, sql, *a):
        return [{"filename": f} for f in self.applied]


def test_selection_matches_migrate_py():
    control = preflight.expected_migrations("control")
    search = preflight.expected_migrations("search")
    assert control and search and not set(control) & set(search)
    assert "124_verified_solution_role.sql" in control
    assert "123_step_routing_search.sql" in search and "123_step_routing_search.sql" not in control
    nums = [preflight._number(n)[0] for n in control]
    assert nums == sorted(nums), "numeric order (100+ after 99), as migrate.py applies them"


def test_current_database_passes_and_pending_ones_are_named():
    everything = preflight.expected_migrations()
    asyncio.run(preflight.assert_schema_current(FakePool(everything), command="skillmd-import", env={}))
    missing = everything[:-2]
    with pytest.raises(preflight.PendingMigrations) as exc:
        asyncio.run(preflight.assert_schema_current(FakePool(missing), command="skillmd-import", env={}))
    assert everything[-1] in str(exc.value) and "2 migration(s)" in str(exc.value)


def test_no_ledger_means_everything_is_pending():
    pending = asyncio.run(preflight.pending_migrations(FakePool([], ledger=False)))
    assert pending == preflight.expected_migrations()


def test_explicit_skip():
    asyncio.run(preflight.assert_schema_current(FakePool([]), command="x", env={"STEALTH_SKIP_MIGRATION_CHECK": "1"}))
