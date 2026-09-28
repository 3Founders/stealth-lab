"""CC-BY-4.0 ruling (2026-09-29): accepted WITH attribution, and removable as a class.

Offline: hand-rolled fakes, no database. Proves (a) the policy, (b) that the license and its attribution are tagged
on the IngestionContext only when required, (c) that `license_takedown` plans without writing, tombstones with the
guarded UPDATEs, queues the search refresh for ALL matching objects and audits the action.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services import license_takedown as lt
from app.services import repo_license_policy as pol
from app.services.ingestion_context import open_ingestion_context


def _run(coro):
    return asyncio.run(coro)


def _norm(sql: str) -> str:
    return " ".join(sql.split())


# ------------------------------------------------------------------------------------------------ policy

def test_cc_by_4_is_allowed_and_attribution_is_mandatory():
    assert pol.classify_spdx("CC-BY-4.0").decision == "ALLOW"
    assert pol.attribution_required("CC-BY-4.0") and pol.attribution_required("cc-by-4.0")
    assert pol.ATTRIBUTION_REQUIRED <= pol.DEFAULT_ALLOWLIST      # a required-attribution id must also be allowed


@pytest.mark.parametrize("spdx,decision", [
    ("CC-BY-SA-4.0", "QUARANTINE"),      # share-alike still needs its own ruling
    ("CC-BY-NC-4.0", "REJECT"), ("CC-BY-ND-4.0", "REJECT"), ("GPL-3.0-only", "REJECT"), ("MIT", "ALLOW"),
])
def test_the_ruling_did_not_widen_anything_else(spdx, decision):
    assert pol.classify_spdx(spdx).decision == decision


def test_attribution_record_carries_the_credit_link_and_modification_notice():
    a = pol.attribution_for("CC-BY-4.0", source_uri="https://example.org/doc", creator="A. Author", title="How to X")
    assert a["license"] == "CC-BY-4.0" and a["license_url"].startswith("https://creativecommons.org/licenses/by/4.0")
    assert a["creator"] == "A. Author" and a["source_uri"] == "https://example.org/doc"
    assert "modified" in a["notice"] and "A. Author" in a["notice"] and "CC-BY-4.0" in a["notice"]
    assert pol.attribution_for("MIT") is None and pol.attribution_for(None) is None


def test_allowlist_version_moved_so_audit_rows_show_which_ruling_applied():
    assert pol.ALLOWLIST_VERSION.endswith("@v2")


# ------------------------------------------------------------------------------ context tagging

class _Conn:
    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []

    def transaction(self):
        conn = self

        class _CM:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _CM()

    async def execute(self, sql, *args):
        self.statements.append((_norm(sql), args))
        return "OK"

    async def fetchrow(self, sql, *args):
        self.statements.append((_norm(sql), args))
        return {"id": args[0]} if "INSERT INTO ingestion_contexts" in sql else None


class _TxnPool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _CM:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _CM()


_OPEN = dict(source_type="skill_md", extractor_id="e", extractor_version="v", actor_id="a", scope_type="global")


def _insert(conn):
    return next((s, a) for s, a in conn.statements if "INSERT INTO ingestion_contexts" in s)


def test_context_without_a_license_uses_the_unchanged_statement():
    conn = _Conn()
    _run(open_ingestion_context(_TxnPool(conn), **_OPEN))
    sql, args = _insert(conn)
    assert "license_spdx" not in sql and len(args) == 17          # a database without migration 126 is unaffected


def test_context_with_a_license_records_it_and_the_attribution():
    conn = _Conn()
    attribution = pol.attribution_for("CC-BY-4.0", source_uri="u", creator="c", title="t")
    _run(open_ingestion_context(_TxnPool(conn), license_spdx="CC-BY-4.0", attribution=attribution, **_OPEN))
    sql, args = _insert(conn)
    assert "license_spdx, attribution" in sql and "$18, $19::jsonb" in sql
    assert args[17] == "CC-BY-4.0" and args[18] == attribution


def _stub_provenance(monkeypatch, license_metadata):
    from app.services import skill_ingestion as si

    seen: dict = {}

    async def fake_register_source(pool, **kw):
        return {"id": "11111111-1111-1111-1111-111111111111", "reused": False}

    async def fake_open(pool, **kw):
        seen.update(kw)
        return "22222222-2222-2222-2222-222222222222"

    monkeypatch.setattr(si, "register_source", fake_register_source)
    monkeypatch.setattr(si, "open_ingestion_context", fake_open)
    artifact = SimpleNamespace(uri="https://example.org/d", repository="acme/docs", content_hash="h",
                               license_metadata=license_metadata)
    parsed = SimpleNamespace(name="How to X", license=None)
    _run(si._open_ingestion_provenance(
        object(), artifact, parsed, domain=None, created_by="w", extractor_version="v", run_id=None,
        injection_signals=[], owner_id=None))
    return seen


def test_skill_ingestion_tags_a_cc_by_context_with_license_and_attribution(monkeypatch):
    seen = _stub_provenance(monkeypatch, {"spdx_id": "CC-BY-4.0"})
    assert seen["license_spdx"] == "CC-BY-4.0"
    assert seen["attribution"]["source_uri"] == "https://example.org/d"
    assert seen["attribution"]["title"] == "How to X" and "modified" in seen["attribution"]["notice"]


@pytest.mark.parametrize("meta", [{"spdx_id": "MIT"}, {}, {"spdx_id": "Apache-2.0"}])
def test_skill_ingestion_leaves_other_licenses_untagged(monkeypatch, meta):
    seen = _stub_provenance(monkeypatch, meta)
    assert "license_spdx" not in seen and "attribution" not in seen


# ---------------------------------------------------------------------------------------- takedown

CTX = ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"]


class _Pool:
    """Scripted pool: answers the takedown's queries by shape and records every statement."""

    def __init__(self, contexts=None, counts=None, ids=None):
        self.contexts = contexts if contexts is not None else CTX
        self.counts = counts or {"procedures": 3, "claims": 5, "evidence": 4, "artifacts": 2}
        self.ids = ids or {"procedure": ["p1", "p2"], "claim": ["c1"]}
        self.statements: list[str] = []

    async def fetch(self, sql, *args):
        n = _norm(sql)
        self.statements.append(n)
        if n.startswith("SELECT id::text FROM ingestion_contexts"):
            return [(c,) for c in self.contexts]
        if n.startswith("SELECT DISTINCT procedure_id"):
            return [(i,) for i in self.ids["procedure"]]
        if "FROM knowledge_nodes" in n and n.startswith("SELECT id::text"):
            return [(i,) for i in self.ids["claim"]]
        raise AssertionError(f"unexpected fetch: {n[:100]}")

    async def fetchval(self, sql, *args):
        n = _norm(sql)
        self.statements.append(n)
        for kind, table in (("procedures", "FROM procedures"), ("claims", "FROM knowledge_nodes"),
                            ("evidence", "FROM evidence"), ("artifacts", "FROM ingested_artifacts")):
            if table in n:
                return self.counts[kind]
        raise AssertionError(f"unexpected fetchval: {n[:100]}")

    async def execute(self, sql, *args):
        self.statements.append(_norm(sql))
        return "UPDATE 1"


def _wire(monkeypatch, pool):
    from app.services import audit, search_projection, shards

    calls = {"all_pools_strict": None, "enqueued": [], "audit": []}

    async def fake_all_pools(p, *, strict=False):
        calls["all_pools_strict"] = strict
        return [("K000", pool)]

    async def fake_enqueue(p, object_type, object_id):
        calls["enqueued"].append((object_type, object_id))

    async def fake_audit(p, **kw):
        calls["audit"].append(kw)

    monkeypatch.setattr(shards, "all_pools", fake_all_pools)
    monkeypatch.setattr(search_projection, "enqueue", fake_enqueue)
    monkeypatch.setattr(audit, "record_audit_event", fake_audit)
    return calls


def _updates(pool):
    return [s for s in pool.statements if s.startswith("UPDATE")]


def test_plan_reports_counts_and_writes_nothing(monkeypatch):
    pool = _Pool()
    calls = _wire(monkeypatch, pool)
    out = _run(lt.plan(pool, "CC-BY-4.0"))
    assert out["applied"] is False and out["contexts"] == 2
    assert out["totals"] == {"procedures": 3, "claims": 5, "evidence": 4, "artifacts": 2}
    assert out["per_shard"]["K000"]["claims"] == 5
    assert _updates(pool) == [] and calls["enqueued"] == [] and calls["audit"] == []


def test_apply_tombstones_every_kind_with_a_reversible_guarded_update(monkeypatch):
    pool = _Pool()
    _wire(monkeypatch, pool)
    _run(lt.apply(pool, "CC-BY-4.0", actor="tester"))
    ups = _updates(pool)
    assert len(ups) == 4
    proc = next(u for u in ups if u.startswith("UPDATE procedures"))
    assert "availability = 'disabled'" in proc and "embedding = NULL" in proc and "t_invalid = now()" in proc
    assert "'deleted'" not in " ".join(ups)                       # not a value of procedure_availability
    assert all("DELETE" not in u for u in ups)                    # nothing is hard-deleted
    for u in ups:
        if "ingested_artifacts" not in u:
            assert "t_invalid IS NULL" in u                       # idempotent: already-tombstoned rows are skipped
        assert "ingestion_context_id = ANY($1::uuid[])" in u      # scoped strictly to the license's contexts


def test_apply_queues_the_search_refresh_for_all_matching_objects_and_audits(monkeypatch):
    pool = _Pool()
    calls = _wire(monkeypatch, pool)
    out = _run(lt.apply(pool, "CC-BY-4.0", actor="tester"))
    assert sorted(calls["enqueued"]) == [("claim", "c1"), ("procedure", "p1"), ("procedure", "p2")]
    assert out["projection_refreshes_queued"] == 3 and out["applied"] is True
    (event,) = calls["audit"]
    assert event["action"] == "license_takedown" and event["object_id"] == "CC-BY-4.0"
    assert event["actor_subject"] == "tester" and event["details"]["contexts"] == 2


def test_projection_ids_are_not_limited_to_currently_valid_rows(monkeypatch):
    """A run that died after tombstoning must be completable: the refresh query cannot filter on t_invalid."""
    for sql in lt._PROJECTION_IDS.values():
        assert "t_invalid" not in sql


def test_no_contexts_means_no_writes_at_all(monkeypatch):
    pool = _Pool(contexts=[])
    calls = _wire(monkeypatch, pool)
    out = _run(lt.apply(pool, "CC-BY-4.0"))
    assert out["totals"] == {k: 0 for k in ("procedures", "claims", "evidence", "artifacts")}
    assert _updates(pool) == [] and calls["enqueued"] == [] and calls["audit"] == []


def test_a_missed_shard_fails_loudly_instead_of_leaving_content_behind(monkeypatch):
    pool = _Pool()
    calls = _wire(monkeypatch, pool)
    _run(lt.plan(pool, "CC-BY-4.0"))
    assert calls["all_pools_strict"] is True
