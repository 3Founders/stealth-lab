"""Phase 6 (launch compliance) — export + dependency-aware deletion.

Offline: a routing FakePool. Proves the export bundles only the caller's
own data (Commons appears as publication references, not owned rows); a
deletion physically removes private rows with no downstream publication,
tombstones a published source (keeps history, clears the vector),
preserves independently-sourced global objects and publication records,
and refuses under legal hold.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.data_rights import (
    delete_user_data,
    export_user_data,
    plan_user_deletion,
)


class FakePool:
    def __init__(self, *, user=None, procs=(), pubs=(), legal_hold=False):
        self.user = user
        self.procs = list(procs)
        self.pubs = list(pubs)
        self.legal_hold = legal_hold
        self.execs = []
        self.audits = []
        self.requests = []

    def _f(self, s):
        return " ".join(s.split())

    async def fetchrow(self, sql, *a):
        f = self._f(sql)
        if f.startswith("SELECT id::text, issuer"):
            return self.user
        if "INSERT INTO audit_events" in f:
            self.audits.append((a[2], a[4]))
            return {"id": len(self.audits)}
        if "FROM contributor_profiles" in f:
            return getattr(self, "contributor_profile", None)
        raise AssertionError(f[:60])

    async def fetch(self, sql, *a):
        f = self._f(sql)
        if "FROM procedures WHERE owner_id" in f:
            return self.procs
        if "FROM publication_records WHERE actor_subject" in f:
            return self.pubs
        raise AssertionError(f[:60])

    async def fetchval(self, sql, *a):
        f = self._f(sql)
        if "legal_hold IS TRUE" in f:
            return 1 if self.legal_hold else None
        if "INSERT INTO data_requests" in f:
            self.requests.append(a)
            return f"req-{len(self.requests)}"
        raise AssertionError(f[:60])

    async def execute(self, sql, *a):
        self.execs.append((self._f(sql), a))
        return "OK"


def _proc(pid, vis="private"):
    return {"id": pid, "name": "p", "display_name": "P", "visibility": vis,
            "availability": "active", "t_created": "now"}


def _pub(src, published="glob-1"):
    return {"id": "pub-1", "source_object_id": src, "published_object_id": published,
            "destination_scope": "global_candidate", "review_state": "candidate",
            "withdrawal_state": None, "t_created": "now"}


def test_export_bundles_only_own_data_and_commons_as_references():
    pool = FakePool(
        user={"id": "u1", "issuer": "supabase", "external_subject": "alice",
              "display_name": "Alice", "email": "a@x", "is_active": True, "t_created": "now"},
        procs=[_proc("p1"), _proc("p2")],
        pubs=[_pub("p1")],
    )
    out = asyncio.run(export_user_data(pool, subject="alice", actor_user_id="u1"))
    assert out["format"] == "stealthlab.export/v1"
    assert out["account"]["email"] == "a@x"
    assert len(out["private_procedures"]) == 2
    assert out["publication_actions"][0]["published_object_id"] == "glob-1"
    assert "not private property" in out["notes"]
    assert ("export_requested", "req-1") in pool.audits
    assert ("export_completed", "req-1") in pool.audits


def test_deletion_plan_splits_physical_vs_tombstone():
    pool = FakePool(procs=[_proc("p1"), _proc("p2")], pubs=[_pub("p1")])
    plan = asyncio.run(plan_user_deletion(pool, subject="alice"))
    assert plan["private_procedures_physical_delete"] == ["p2"]
    assert plan["private_procedures_tombstone"] == ["p1"]      # published source
    assert plan["global_objects_preserved"] == ["glob-1"]


def test_deletion_execute_deletes_unpublished_tombstones_published_keeps_commons():
    pool = FakePool(procs=[_proc("p1"), _proc("p2")], pubs=[_pub("p1")])
    out = asyncio.run(
        delete_user_data(pool, subject="alice", actor_user_id="u1", dry_run=False)
    )
    assert out["physically_deleted"] == 1
    assert out["tombstoned"] == 1
    assert out["global_objects_preserved"] == 1

    joined = " ".join(s for s, _ in pool.execs)
    assert "DELETE FROM procedures" in joined
    assert "availability = 'deleted', embedding = NULL" in joined   # vector cleared
    # no DELETE against the published global object
    assert "glob-1" not in str(pool.execs)
    assert ("deletion_completed", "req-1") in pool.audits


def test_deletion_refused_under_legal_hold():
    pool = FakePool(procs=[_proc("p1")], pubs=[], legal_hold=True)
    with pytest.raises(PermissionError):
        asyncio.run(delete_user_data(pool, subject="alice", dry_run=False))


def test_dry_run_never_mutates():
    pool = FakePool(procs=[_proc("p1")], pubs=[])
    out = asyncio.run(delete_user_data(pool, subject="alice", dry_run=True))
    assert out["dry_run"] is True
    assert pool.execs == []
