"""uuid7_timestamp_ms: the timestamp round-trips, and non-v7 ids are refused.

Offline by construction -- ids.py is pure Python with no pool, no network.
"""
from __future__ import annotations

import time
import uuid

import pytest

from app.utils.ids import uuid7, uuid7_timestamp_ms


def test_timestamp_round_trips_within_a_second():
    before = time.time_ns() // 1_000_000
    u = uuid7()
    after = time.time_ns() // 1_000_000
    assert before <= uuid7_timestamp_ms(u) <= after


def test_accepts_string_form():
    u = uuid7()
    assert uuid7_timestamp_ms(str(u)) == uuid7_timestamp_ms(u)


def test_rejects_non_v7():
    # A v4 id's top 48 bits are random -- returning a number here would be
    # a plausible-looking lie, so the function must refuse instead.
    with pytest.raises(ValueError, match="not a UUIDv7"):
        uuid7_timestamp_ms(uuid.uuid4())
