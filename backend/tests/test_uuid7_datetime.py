"""uuid7_to_datetime: aware UTC, agrees with uuid7_timestamp_ms, refuses non-v7.

Same offline shape as test_uuid7_timestamp.py -- pure Python, no pool.
"""
from __future__ import annotations

import uuid
from datetime import timezone

import pytest

from app.utils.ids import uuid7, uuid7_timestamp_ms, uuid7_to_datetime


def test_returns_aware_utc_datetime():
    dt = uuid7_to_datetime(uuid7())
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0


def test_agrees_with_the_ms_accessor():
    u = uuid7()
    assert int(uuid7_to_datetime(u).timestamp() * 1000) == uuid7_timestamp_ms(u)


def test_rejects_non_v7():
    # Inherited from uuid7_timestamp_ms -- one parser, one refusal path.
    with pytest.raises(ValueError, match="not a UUIDv7"):
        uuid7_to_datetime(uuid.uuid4())
