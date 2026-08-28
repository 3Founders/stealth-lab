"""episodes.metadata must be passed as a Python object, never pre-dumped.

CONTEXT: app pools register a jsonb type codec whose encoder IS json.dumps
(db/session.py:23-25). write_session_episodes() additionally called
json.dumps() on the metadata dict, so the value was encoded TWICE and the
jsonb column stored a JSON *string* rather than an object -- RUNBOOK.md
line 53's exact documented pitfall ("pass Python objects, not
json.dumps(...) strings").

WHY THIS MATTERED, beyond tidiness: the dedup SELECT in the same function
reads `metadata->>'assembly_fingerprint'`, which returns NULL on a
double-encoded value. So no fingerprint ever matched, skipped_existing was
permanently 0, and every re-run duplicated every episode -- silently
breaking the replay contract process_transcript_session's own docstring
promises ("rerunning against the same files inserts nothing new").

Measured on real data 2026-08-28: a second run over one unchanged
transcript took episodes 50 -> 100. After the fix, run 2 inserted 0 and
skipped 50. It also made 12 episodes' segmentation flags readable for the
first time (subdivided/folded_trivial/oversize_unsubdivided), which the
double-encode had been hiding behind a NULL.

Offline: FakePool/FakeConn capture the real SQL + params, same convention
as test_trace_payload_cap.py.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

import app.services.trace_worker as tw


class FakeConn:
    def __init__(self, existing_rows=None):
        self._existing = existing_rows or []
        self.inserts: list[tuple] = []

    async def fetch(self, sql, *args):
        return self._existing

    async def fetchval(self, sql, *args):
        self.inserts.append((sql, args))
        return "00000000-0000-0000-0000-0000000000aa"

    async def execute(self, sql, *args):
        return "OK"

    @asynccontextmanager
    async def transaction(self):
        yield


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


def _one_episode_assembly():
    """Smallest real assembly: one prompt-led episode."""
    lines = [{
        "uuid": "u1",
        "timestamp": "2026-08-28T10:00:00.000Z",
        "type": "user",
        "message": {"role": "user", "content": "do the thing"},
    }]
    return tw.assemble_episodes(lines)


@pytest.mark.asyncio
async def test_metadata_param_is_a_dict_not_a_prepared_json_string():
    """The regression itself. If someone re-adds json.dumps(), this fails."""
    conn = FakeConn()
    await tw.write_session_episodes(
        FakePool(conn), session_id="sess-1", assembly=_one_episode_assembly(),
        content_ref_prefix="sess-1.jsonl",
    )
    assert conn.inserts, "expected one episode INSERT"
    _, args = conn.inserts[0]
    metadata_arg = next(a for a in args if isinstance(a, (dict, str)) and "fingerprint" in str(a))
    assert isinstance(metadata_arg, dict), (
        "metadata must be passed as a dict -- the pool's jsonb codec encodes "
        "it. Pre-dumping double-encodes and the column stores a JSON string, "
        "which makes metadata->>'assembly_fingerprint' NULL and breaks dedup."
    )
    assert "assembly_fingerprint" in metadata_arg


@pytest.mark.asyncio
async def test_matching_fingerprint_is_skipped_not_reinserted():
    """The replay contract: a row whose fingerprint already exists must be
    skipped. This is what silently never happened before the fix."""
    assembly = _one_episode_assembly()
    ep = assembly.episodes[0]
    fp = ep.fingerprint("sess-1")

    conn = FakeConn(existing_rows=[{"fp": fp, "id": "existing-row-id"}])
    stats = await tw.write_session_episodes(
        FakePool(conn), session_id="sess-1", assembly=assembly,
        content_ref_prefix="sess-1.jsonl",
    )
    assert stats["skipped_existing"] == 1
    assert stats["parents_inserted"] == 0
    assert conn.inserts == [], "nothing may be inserted when the fingerprint matches"


@pytest.mark.asyncio
async def test_null_fingerprint_rows_do_not_swallow_real_episodes():
    """Defensive: legacy double-encoded rows read back as fp=None. A None
    key must not collide with a real fingerprint and cause a false skip."""
    conn = FakeConn(existing_rows=[{"fp": None, "id": "legacy-double-encoded"}])
    stats = await tw.write_session_episodes(
        FakePool(conn), session_id="sess-1", assembly=_one_episode_assembly(),
        content_ref_prefix="sess-1.jsonl",
    )
    assert stats["parents_inserted"] == 1, "a real episode must still insert"


@pytest.mark.asyncio
async def test_content_ref_is_a_locator_and_content_is_never_written():
    """Privacy posture: this path reads a native UNREDACTED transcript, so
    only a locator may land -- never message content."""
    conn = FakeConn()
    await tw.write_session_episodes(
        FakePool(conn), session_id="sess-1", assembly=_one_episode_assembly(),
        content_ref_prefix="sess-1.jsonl",
    )
    sql, args = conn.inserts[0]
    assert "content_ref" in sql
    refs = [a for a in args if isinstance(a, str) and a.startswith("sess-1.jsonl#")]
    assert refs, "expected a file#source:start:end locator"
    assert "do the thing" not in str(args), "raw message content must never be persisted"
