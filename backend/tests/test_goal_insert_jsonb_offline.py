"""The Goal insert encodes its jsonb columns as JSON text (2026-11-18).

Why this test exists: submitting any Way failed with

    asyncpg.exceptions.DataError: invalid input for query argument $5: [] (expected str, got list)

because goals.py passed Python lists and dicts straight into `$5::jsonb ... $8::jsonb`. asyncpg does not encode a Python
object for a `::jsonb` cast; it needs a string. Nothing offline covered find_or_create_goal, so the whole Submit a Way
path was broken without a test going red.

This runs the real function against a pool that records the arguments, and asserts every jsonb-bound parameter is text.
It does not need a database: the point is the encoding, which is decided before Postgres is ever reached.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.services import goals as goals_module

# The four parameters the INSERT casts to ::jsonb: constraints, metadata, expected_outcome, verification_requirement.
JSONB_INDEXES = range(4, 8)


class RecordingConn:
    """Stands in for an asyncpg connection: records what would have been sent, and answers like a fresh insert."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql: str, *args: Any) -> dict | None:
        self.calls.append((sql, args))
        if "INSERT INTO goals" in sql:
            return {"id": args[0], "canonical_name": args[1], "home_shard_id": args[-1]}
        if "SELECT id, canonical_name FROM goals" in sql:
            return None            # nothing exists yet: the insert is what this test is about
        return {"id": args[0]}

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append((sql, args))
        return "OK"

    async def fetchval(self, sql: str, *args: Any):
        self.calls.append((sql, args))
        return None

    async def fetch(self, sql: str, *args: Any) -> list:
        self.calls.append((sql, args))
        return []


class RecordingPool:
    def __init__(self) -> None:
        self.conn = RecordingConn()

    def __getattr__(self, name: str):
        # Anything the service calls on the pool goes to the recording connection.
        return getattr(self.conn, name)

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

            def transaction(self):
                return _Ctx()

        return _Ctx()


def test_the_goal_insert_binds_jsonb_columns_as_text() -> None:
    pool = RecordingPool()
    asyncio.get_event_loop_policy()
    try:
        asyncio.run(
            goals_module.find_or_create_goal(
                pool,  # type: ignore[arg-type]
                canonical_name="Postgres migration drift",
                scope_type="entity",
                scope_entity_id="migration-drift",
                provenance="company_ingested",
                description="A deploy is blocked by a drifted migration.",
                constraints=["only Postgres", "never edit an applied file"],
                metadata={"source": "incident-2026-11-18"},
                expected_outcome={"ledger_checksum": "matches"},
                verification_requirement={"checks": ["deploy succeeds"]},
            )
        )
    except Exception as exc:  # noqa: BLE001 -- the assertion below is what matters, not how it failed
        pytest.fail(f"find_or_create_goal raised: {type(exc).__name__}: {exc}")

    inserts = [(sql, args) for sql, args in pool.conn.calls if "INSERT INTO goals" in sql]
    assert inserts, "no INSERT INTO goals reached the database layer"
    sql, args = inserts[0]
    assert "$5::jsonb" in sql and "$8::jsonb" in sql, "the guard is about the jsonb cast this test was written for"

    for i in JSONB_INDEXES:
        value = args[i]
        assert isinstance(value, str), (
            f"argument ${i + 1} ({value!r}) is a {type(value).__name__}: asyncpg needs JSON text for a ::jsonb cast, "
            "and passing a Python object is what broke every Way submission")
        json.loads(value)          # and it must be valid JSON, not just a str() of the object
