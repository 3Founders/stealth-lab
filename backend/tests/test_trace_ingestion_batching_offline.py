"""
Offline proving tests for the batched trace-event insert in trace_worker.py.

WHAT THIS PINS, and why a round-trip count rather than a wall clock:

`process_collector_file` used to open a transaction PER EVENT and issue
INSERT...RETURNING plus INSERT ingestion_jobs inside it -- four sequential
network round trips per event, on one connection. At the module's own
documented 50k-line worst case that is ~200,000 strictly serial round
trips. Timing that honestly needs a real Postgres over a real network,
and a fake-pool stopwatch measures the harness, not the database.

Round trips are the right metric anyway: the cost being removed *is* the
round trips, they are deterministic, and they can be asserted offline so
the property cannot silently rot back into per-event INSERTs. A wall-clock
benchmark added later would sit on top of this, not replace it.

Fully offline: no database, no clock, no network, no clock reads. A
hand-rolled FakePool/FakeConn counts statements, same convention as
test_trace_payload_cap.py and test_episode_segmentation.py. raw_payload_dir
is always a pytest tmp_path -- never the real backend/data/raw_payloads/.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import app.services.trace_worker as tw

# Deliberately far below tw.INSERT_CHUNK_SIZE so a 1,200-record fixture
# crosses several chunk boundaries. If the chunk size ever grows past this
# the test still passes (one chunk); what it must never do is fall back to
# one statement per record, which is what the counts below forbid.
N_RECORDS = 1_200


class CountingConn:
    """Counts statements, and models the one behaviour the caller depends
    on: INSERT ... ON CONFLICT (dedup_key) DO NOTHING RETURNING only
    returns rows that were really created.

    `conflicting` is the set of dedup_keys the fake database already
    holds, so duplicate accounting can be tested without a real unique
    index.
    """

    def __init__(self, conflicting: set[str] | None = None):
        self.conflicting = conflicting or set()
        self.round_trips = 0
        self.event_batches: list[list[dict]] = []
        self.job_payloads: list[list[dict]] = []
        self.headers: list[tuple] = []
        self.created: list[dict] = []
        self._n = 0

    def transaction(self):
        @asynccontextmanager
        async def _txn():
            # BEGIN and COMMIT are both round trips on the wire; counting
            # them is the point -- the old code paid two per EVENT.
            self.round_trips += 2
            yield
        return _txn()

    async def execute(self, sql, *args):
        flat = " ".join(sql.split())
        self.round_trips += 1
        if "INSERT INTO agent_traces" in flat:
            self.headers.append(args)
        elif "INSERT INTO ingestion_jobs" in flat:
            self.job_payloads.append(json.loads(args[0]))

    async def fetch(self, sql, *args):
        flat = " ".join(sql.split())
        self.round_trips += 1
        if "INSERT INTO trace_events" not in flat:
            return []
        rows = json.loads(args[0])
        self.event_batches.append(rows)
        created = []
        for r in rows:
            if r["dedup_key"] in self.conflicting:
                continue
            self._n += 1
            created.append({"id": f"ev-{self._n}", "dedup_key": r["dedup_key"]})
        self.created.extend(created)
        return created

    async def fetchval(self, sql, *args):  # pragma: no cover - single-row path
        self.round_trips += 1
        return "unused"


class FakePool:
    def __init__(self, conn):
        self.conn = conn
        self.acquisitions = 0

    def acquire(self):
        self.acquisitions += 1
        conn = self.conn

        @asynccontextmanager
        async def _cm():
            yield conn
        return _cm()


def _record(i: int, session: str = "sess-1") -> dict:
    return {
        "dedup_key": f"dedup-{i}",
        "session_id": session,
        "event_type": "PostToolUse",
        "sequence": i,
        "event": {
            "timestamp": "2026-08-27T10:00:00Z",
            "tool_name": "Read",
            "tool_input": {"file": f"f{i}.py"},
            "tool_output": {"content": "ok"},
        },
    }


def _write(tmp_path: Path, n: int, *, session: str = "sess-1") -> Path:
    f = tmp_path / "events.jsonl"
    f.write_text("\n".join(json.dumps(_record(i, session)) for i in range(n)) + "\n")
    return f


def _run(tmp_path: Path, n: int, *, conflicting: set[str] | None = None,
         chunk_size: int | None = None) -> tuple[dict, CountingConn, FakePool]:
    original = tw.INSERT_CHUNK_SIZE
    if chunk_size is not None:
        tw.INSERT_CHUNK_SIZE = chunk_size
    try:
        conn = CountingConn(conflicting)
        pool = FakePool(conn)
        result = asyncio.run(tw.process_collector_file(pool, _write(tmp_path, n)))
        return result, conn, pool
    finally:
        tw.INSERT_CHUNK_SIZE = original


# --------------------------------------------------------------- the numbers


def test_round_trips_scale_with_chunks_not_records(tmp_path):
    """THE regression assertion.

    Before: 4 round trips per event (BEGIN, INSERT...RETURNING, INSERT
    job, COMMIT) -- 1,200 events cost ~4,800, all sequential.
    After: 2 per chunk (the event INSERT and the job INSERT) plus the 2
    the transaction itself costs, so 1,200 events in 500-record chunks
    cost 3 chunks x 4 + 1 header = 13.
    """
    result, conn, _ = _run(tmp_path, N_RECORDS)

    assert result["inserted"] == N_RECORDS
    expected_chunks = -(-N_RECORDS // tw.INSERT_CHUNK_SIZE)
    expected = expected_chunks * 4 + 1  # +1 for the single trace header
    assert conn.round_trips == expected, (
        f"{conn.round_trips} round trips for {N_RECORDS} records; "
        f"expected {expected}. A per-event INSERT has crept back in."
    )
    # The old shape, stated as a hard ceiling so the intent survives edits.
    assert conn.round_trips < N_RECORDS, "must not scale with record count"


def test_one_connection_for_the_whole_file(tmp_path):
    """A3's original guarantee, still true: the run is not back to one
    pool acquisition per event."""
    _, _, pool = _run(tmp_path, N_RECORDS)
    assert pool.acquisitions == 1


def test_header_upserted_once_per_distinct_trace(tmp_path):
    """Not once per record, and not once per chunk either -- still once per
    distinct trace_id actually seen, even across chunk boundaries."""
    _, conn, _ = _run(tmp_path, N_RECORDS, chunk_size=100)
    assert len(conn.headers) == 1


def test_headers_upserted_once_per_distinct_trace_across_sessions(tmp_path):
    """Two sessions in one file -> exactly two header upserts, in file
    order, so each trace's started_at is its own first event's."""
    f = tmp_path / "events.jsonl"
    lines = [_record(i, "sess-a") for i in range(5)] + [_record(i, "sess-b") for i in range(5)]
    f.write_text("\n".join(json.dumps(r) for r in lines) + "\n")

    original = tw.INSERT_CHUNK_SIZE
    tw.INSERT_CHUNK_SIZE = 2  # force several chunks so the split is real
    try:
        conn = CountingConn()
        asyncio.run(tw.process_collector_file(FakePool(conn), f))
    finally:
        tw.INSERT_CHUNK_SIZE = original

    assert len(conn.headers) == 2
    assert [h[0] for h in conn.headers] == ["sess-a", "sess-b"]


# ------------------------------------------------------------ idempotency


def test_duplicate_dedup_key_is_counted_and_queues_no_job(tmp_path):
    """The batch must derive `inserted` from RETURNING, exactly as the
    per-event loop derived it from a None RETURNING id. A dedup_key the
    database already holds creates no row, counts as a duplicate, and
    gets NO downstream job -- a replayed collector file must not fan out
    a second normalize_trace_event for an event already normalized."""
    conflicting = {f"dedup-{i}" for i in range(0, N_RECORDS, 2)}  # every other record
    result, conn, _ = _run(tmp_path, N_RECORDS, conflicting=conflicting)

    assert result["inserted"] == N_RECORDS // 2
    assert result["skipped_duplicate"] == N_RECORDS // 2
    assert result["records_seen"] == N_RECORDS

    queued = [p for batch in conn.job_payloads for p in batch]
    assert len(queued) == N_RECORDS // 2
    assert all(p["dedup_key"] not in conflicting for p in queued)
    # Every queued job points at an id the INSERT actually RETURNED, not at
    # an input row. The bound parameter is {id, dedup_key}; job_type and the
    # 'trace_event_id' payload key are built by the SQL, so what is provable
    # offline is that the queued id set equals the returned id set exactly.
    assert {p["id"] for p in queued} == {c["id"] for c in conn.created}
    assert {p["dedup_key"] for p in queued} == {c["dedup_key"] for c in conn.created}


def test_full_replay_inserts_nothing(tmp_path):
    """The idempotency property the whole replay story rests on: run the
    same file twice with every dedup_key already present and the second
    run creates no rows and queues no jobs."""
    result, conn, _ = _run(
        tmp_path, N_RECORDS, conflicting={f"dedup-{i}" for i in range(N_RECORDS)},
    )
    assert result["inserted"] == 0
    assert result["skipped_duplicate"] == N_RECORDS
    assert all(len(b) == 0 for b in conn.job_payloads)


# --------------------------------------------- the redaction chokepoint


def test_redaction_still_runs_per_record_in_the_batched_path(tmp_path):
    """Batching the SQL must not batch or skip the scrub.

    A planted secret is asserted absent from EVERY bound row, not just
    the first -- the failure mode this guards is a batch where row 0 is
    scrubbed and the rest are not.
    """
    secret = "sk-ant-api03-PLANTEDSECRETVALUE1234567890abcdef"
    f = tmp_path / "events.jsonl"
    lines = []
    for i in range(50):
        r = _record(i)
        r["event"]["tool_output"] = {"echo": f"prefix-{i} {secret} suffix-{i}"}
        lines.append(r)
    f.write_text("\n".join(json.dumps(r) for r in lines) + "\n")

    conn = CountingConn()
    asyncio.run(tw.process_collector_file(FakePool(conn), f))

    bound = [r for batch in conn.event_batches for r in batch]
    assert len(bound) == 50
    for r in bound:
        assert secret not in r["tool_output"], "a bound row carried an unredacted secret"
    # Redaction must have happened, not merely that the value moved.
    assert any("REDACTED" in r["tool_output"] for r in bound)


def test_batched_statement_preserves_the_idempotency_and_visibility_contract(tmp_path):
    """The two clauses that make this statement safe to batch, pinned in
    the SQL text so a future edit cannot quietly drop either."""
    assert "ON CONFLICT (dedup_key) DO NOTHING" in tw._BATCH_INSERT_SQL
    assert "RETURNING id, dedup_key" in tw._BATCH_INSERT_SQL
    # owner_id/visibility are real parameters, not decorative columns.
    assert "r.owner_id" in tw._BATCH_INSERT_SQL
    assert "r.visibility::visibility_level" in tw._BATCH_INSERT_SQL
    # jsonb columns cast from text, never handed to a jsonb codec as a str.
    assert "r.tool_input::jsonb" in tw._BATCH_INSERT_SQL
    assert "r.tool_output::jsonb" in tw._BATCH_INSERT_SQL
    assert "r.raw_event::jsonb" in tw._BATCH_INSERT_SQL


def test_batch_payload_does_not_double_encode_json_columns(tmp_path):
    """tool_input/tool_output arrive as JSON *text* and are cast in SQL.
    If they were ever handed to the jsonb column uncast, the value would
    land as a JSON string literal -- the 'episodes 50 -> 100' class of bug
    documented in RUNBOOK.md. Assert the wire value is still an object."""
    _, conn, _ = _run(tmp_path, 3)
    for batch in conn.event_batches:
        for r in batch:
            assert isinstance(json.loads(r["tool_output"]), dict)
            assert isinstance(json.loads(r["tool_input"]), dict)


# ------------------------------------------------- chunk-boundary edges


def test_exactly_one_chunk_boundary_is_not_off_by_one(tmp_path):
    """A record count that is an exact multiple of the chunk size must not
    produce an empty trailing chunk (which would be a wasted transaction
    and, before the fix, an easy place to lose a count)."""
    n = tw.INSERT_CHUNK_SIZE * 2
    result, conn, _ = _run(tmp_path, n)
    assert result["inserted"] == n
    assert all(len(b) == tw.INSERT_CHUNK_SIZE for b in conn.event_batches)
    assert len(conn.event_batches) == 2


def test_single_record_file_still_works(tmp_path):
    """Degenerate case: one chunk, one row, one job."""
    result, conn, _ = _run(tmp_path, 1)
    assert result["inserted"] == 1
    assert result["skipped_duplicate"] == 0
    assert [p["dedup_key"] for b in conn.job_payloads for p in b] == ["dedup-0"]


def test_empty_file_produces_no_statements(tmp_path):
    f = tmp_path / "events.jsonl"
    f.write_text("")
    conn = CountingConn()
    result = asyncio.run(tw.process_collector_file(FakePool(conn), f))
    assert result == {"records_seen": 0, "inserted": 0, "skipped_duplicate": 0, "quarantined": 0}
    assert conn.round_trips == 0
    assert conn.event_batches == []


def test_untouched_chunks_still_see_every_record(tmp_path):
    """Batching must not drop records at a boundary. 1,201 records across
    500-record chunks is the awkward size (3 chunks, last one short)."""
    n = 1_201
    result, conn, _ = _run(tmp_path, n)
    assert result["inserted"] == n
    seen = [r["sequence"] for batch in conn.event_batches for r in batch]
    assert seen == list(range(n)), "records lost or reordered across a chunk boundary"
    assert [len(b) for b in conn.event_batches] == [500, 500, 201]
