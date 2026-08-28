"""UUIDv7 generation (RFC 9562) — time-ordered primary keys.

Band 1.5: replaces gen_random_uuid() (UUIDv4) at service write paths.
Random v4 ids fragment B-tree indexes under insert load because their
bits carry no ordering; v7 embeds a millisecond timestamp in the top
bits so new rows always land at the right edge of the index.

Pure Python, no dependency: 48-bit unix-ms timestamp + 74 random bits,
version/variant fields set per RFC 9562 §5.2. Monotonicity within one
millisecond is not guaranteed across processes (same as every
timestamp-first id scheme short of a coordinator); index locality --
the property we need -- holds regardless.
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone

_last = {"ms": 0, "rand": b""}


def uuid7() -> uuid.UUID:
    """Generate a UUIDv7. Layout (RFC 9562 §5.2): 48-bit unix-ms | ver 4b |
    12b rand_a | var 2b | 62b rand_b. Within one millisecond on one process,
    rand_a/rand_b increment deterministically so ids stay ordered."""
    ms = time.time_ns() // 1_000_000
    if ms == _last["ms"]:
        prev = int.from_bytes(_last["rand"], "big")
        rand = ((prev + 1) & ((1 << 80) - 1)).to_bytes(10, "big")
    else:
        rand = os.urandom(10)
    _last["ms"] = ms
    _last["rand"] = rand

    b = bytearray(16)
    b[0:6] = ms.to_bytes(6, "big")          # unix_ts_ms
    b[6] = (rand[0] & 0x0F) | 0x70          # version 7
    b[7] = rand[1]
    b[8] = (rand[2] & 0x3F) | 0x80          # variant 10
    b[9:16] = rand[3:10]
    return uuid.UUID(bytes=bytes(b))


def uuid7_str() -> str:
    return str(uuid7())


def uuid7_timestamp_ms(value: uuid.UUID | str) -> int:
    """Read the unix-ms timestamp back out of a UUIDv7's top 48 bits.

    Useful when debugging insert ordering: given only a row id, recover
    roughly when it was written without joining to a timestamp column.

    HONEST LIMIT: this reads the bits, it does not authenticate them. The
    value is whatever the generating process's clock said -- a machine with
    a skewed clock produces a skewed timestamp here, and nothing in the id
    records which machine that was. Treat it as a debugging aid, never as
    provenance; `t_created` columns remain the source of truth.

    Raises ValueError on a non-v7 UUID rather than returning a meaningless
    number from bits that were never a timestamp.
    """
    u = uuid.UUID(str(value)) if not isinstance(value, uuid.UUID) else value
    if u.version != 7:
        raise ValueError(f"not a UUIDv7 (version={u.version}); no embedded timestamp")
    return int.from_bytes(u.bytes[0:6], "big")


def uuid7_to_datetime(value: uuid.UUID | str) -> datetime:
    """The same embedded timestamp as `uuid7_timestamp_ms`, as an aware UTC
    datetime -- the form that compares directly against this schema's
    `timestamptz` columns without a caller-side conversion.

    Deliberately built on `uuid7_timestamp_ms` rather than re-reading the
    bytes: one parser, so the version check and the byte layout cannot
    drift apart between the two functions.

    HONEST LIMIT: inherits the clock-trust caveat in full. UUIDv7 carries
    millisecond resolution, so the returned datetime is never more precise
    than that regardless of what the microsecond field displays.
    """
    return datetime.fromtimestamp(uuid7_timestamp_ms(value) / 1000, tz=timezone.utc)
