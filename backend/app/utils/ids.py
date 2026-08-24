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
