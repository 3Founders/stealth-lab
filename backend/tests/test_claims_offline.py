"""
DB-free coverage for claims.capture_claim's B7 anchoring relaxation:

  a Claim MUST be creatable with valid provenance and ZERO task_nodes /
  episodes (V4-hardening "CLAIM CREATION"), but a totally unprovenanced
  opaque claim is still a silent no-op.

Hand-rolled FakeConn / FakePool (this repo rolls its own per file). The
older, exhaustive FakeDB in tests/test_claims.py covers the task_node /
episode paths; this file only adds the new provenance-ref path.
"""
from __future__ import annotations

import asyncio

from app.services.claims import capture_claim as _real_capture_claim

CTX_ID = "00000000-0000-4000-8000-0000000000f1"
OBS_ID = "00000000-0000-4000-8000-0000000000f2"


def _run(coro):
    return asyncio.run(coro)


def _norm(sql: str) -> str:
    return " ".join(sql.split())


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.0] * 1024


class FakeConn:
    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []
        self.node_id = "00000000-0000-4000-8000-00000000aaaa"

    class _Txn:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    def transaction(self):
        return FakeConn._Txn()

    async def fetch(self, sql, *args):
        self.statements.append((_norm(sql), args))
        if "FROM task_nodes WHERE skill_ref = ANY" in _norm(sql):
            return []          # nothing resolves -- the point of these tests
        raise AssertionError(f"unexpected fetch: {_norm(sql)}")

    async def fetchval(self, sql, *args):
        self.statements.append((_norm(sql), args))
        if "INSERT INTO knowledge_nodes" in _norm(sql):
            return self.node_id
        raise AssertionError(f"unexpected fetchval: {_norm(sql)}")

    async def execute(self, sql, *args):
        self.statements.append((_norm(sql), args))
        return "INSERT 0 1"

    def stmt(self, needle: str):
        for s, a in self.statements:
            if needle in s:
                return s, a
        return None


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    class _Acquire:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, *exc):
            return False

    def acquire(self):
        return FakePool._Acquire(self._conn)


async def _capture(conn, **kw):
    return await _real_capture_claim(FakePool(conn), embedder=FakeEmbedder(), **kw)


# ---------------------------------------------------------------------


def test_capture_claim_accepts_document_provenance_without_task_or_episode():
    conn = FakeConn()
    new_id = _run(_capture(
        conn,
        statement="the CLI rejects --force on a dirty tree",
        task_ids=[],
        source_ref="skill_md:acme/repo@deadbeef",
        ingestion_context_id=CTX_ID,
    ))
    assert new_id == conn.node_id

    ins = conn.stmt("INSERT INTO knowledge_nodes")
    assert ins is not None, "the claim node must still be written"
    sql, args = ins
    # the ingestion_context_id column is set (9-arg INSERT variant)
    assert "ingestion_context_id" in sql
    assert str(args[-1]) == CTX_ID
    # source_ref, which has no column, is kept in properties
    assert args[1].get("source_ref") == "skill_md:acme/repo@deadbeef"
    # no task edge written (no task_nodes resolved)
    assert conn.stmt("INSERT INTO edges") is None


def test_capture_claim_accepts_observation_provenance_and_writes_claim_sources():
    conn = FakeConn()
    new_id = _run(_capture(
        conn,
        statement="observed: retries backoff exponentially",
        task_ids=[],
        observation_id=OBS_ID,
    ))
    assert new_id == conn.node_id
    # observation_id alone is a valid anchor -> node written, 8-arg INSERT
    ins = conn.stmt("INSERT INTO knowledge_nodes")
    assert ins is not None
    assert "ingestion_context_id" not in ins[0]
    cs = conn.stmt("INSERT INTO claim_sources")
    assert cs is not None, "a claim_sources row must be written for observation provenance"
    assert str(cs[1][0]) == conn.node_id and str(cs[1][1]) == OBS_ID
    assert "ON CONFLICT DO NOTHING" in cs[0]


def test_capture_claim_still_drops_a_claim_with_no_anchor_and_no_provenance():
    conn = FakeConn()
    result = _run(_capture(
        conn,
        statement="an opaque unprovenanced assertion",
        task_ids=[],
    ))
    assert result is None
    # dropped before any DB work AND before the embedding spend
    assert conn.statements == []


def test_capture_claim_drops_when_task_ids_given_but_none_resolve_and_no_provenance():
    conn = FakeConn()
    result = _run(_capture(
        conn,
        statement="claim against a held-out task, no provenance",
        task_ids=["ghost_instance"],
    ))
    assert result is None
    # the task_nodes resolution ran, then the no-anchor no-op fired
    assert conn.stmt("FROM task_nodes WHERE skill_ref = ANY") is not None
    assert conn.stmt("INSERT INTO knowledge_nodes") is None
