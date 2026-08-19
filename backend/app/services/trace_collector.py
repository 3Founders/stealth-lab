"""
The collector half of the ingestion pipeline (ticket 16, memory-substrate
map). Runs client-side, fast, non-blocking -- appends a redacted event to
a local file and returns immediately. The worker (trace_worker.py) is a
separate process that tails this file and does the actual, durable
database write.

HONEST SCOPE: this module is the real, tested collector logic (dedup key,
redaction, bounded file, drop-oldest). It is deliberately NOT wired into
an actual Claude Code hook invocation here -- ticket 07 found hooks
receive JSON on stdin with fields like session_id/hook_event_name/cwd,
but I have no real Claude Code process available to test that wiring
against, and fabricating a claim of having tested it would be dishonest.
See scripts/example_hook_wrapper.py for what that wiring would look like,
clearly marked as untested against a real Claude Code process.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.services.trace_redaction import redact_event

DEFAULT_MAX_LINES = 50_000
TRIM_FRACTION = 0.2  # when over budget, drop the oldest 20% at once,
                      # not one line at a time -- O(n) either way for a
                      # plain text file, so batching amortizes the cost
                      # instead of paying it on every single append.

LOCK_POLL_SECONDS = 0.02
DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0
# Bounded, not indefinite: this module's own docstring promises "fast,
# non-blocking" collector behaviour. A hook that hangs forever waiting on
# a lock would violate that far more than occasionally losing a race
# would -- so append_event fails loudly (LockTimeout) past this budget
# rather than blocking the calling hook indefinitely.


class LockTimeout(TimeoutError):
    """Raised when append_event() cannot acquire the collector file's
    lock within DEFAULT_LOCK_TIMEOUT_SECONDS. The caller (a Claude Code
    hook) should treat this the same as any other collector failure --
    log and move on, never block the agent turn on it."""


@contextmanager
def _locked(lock_path: Path, timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """
    Real fix for the race condition flagged in the last handoff:
    append_event() previously did a full read-modify-write of the whole
    collector file with no locking at all -- safe for one hook firing at
    a time, silently lossy (a classic lost-update) the moment two fire
    close together, which is the normal case, not an edge case, for a
    coding agent that can spawn subagents or tool calls in parallel.

    Uses a separate `.lock` file (not the data file itself) with
    fcntl.flock, so the lock's lifetime is independent of the data file
    being atomically replaced underneath it (see _locked's caller). Only
    fixes concurrent *writers* to the same file -- fcntl.flock is
    per-machine, not distributed; a second host writing to a
    network-shared path is out of scope, consistent with this module's
    already-stated local-first, single-machine design.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise LockTimeout(
                        f"could not acquire lock on {lock_path} within {timeout}s "
                        "-- another writer is holding it unusually long"
                    )
                time.sleep(LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def compute_dedup_key(session_id: str, event_type: str, sequence: int, payload: dict) -> str:
    """
    Deterministic composite key (ticket 06's answer): hooks carry no
    native event id, so the collector computes one instead of trusting
    one to arrive pre-formed. Payload hash included so two events with
    the same session/type/sequence but genuinely different content
    (which should not happen in practice, but might under a bug
    elsewhere) don't silently collide into one dedup key.
    """
    payload_bytes = json.dumps(payload, sort_keys=True, default=str).encode()
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()[:16]
    raw = f"{session_id}:{event_type}:{sequence}:{payload_hash}"
    return hashlib.sha256(raw.encode()).hexdigest()


def append_event(
    event: dict,
    file_path: Path,
    session_id: str,
    event_type: str,
    sequence: int,
    max_lines: int = DEFAULT_MAX_LINES,
) -> dict:
    """
    Redacts, keys, and appends one event to the local collector file.
    Returns the redacted+keyed record actually written (useful for
    testing and for the caller to log/confirm).

    Backpressure (ticket 16's answer): bounded by line count, drop
    oldest, with a running drop counter recorded in the file itself as a
    special first-line record -- silent loss is exactly what makes
    memory correctness unauditable later, so the count is real, visible
    data, not a log message that can be missed.

    Concurrency: the whole read-modify-write below runs under an
    exclusive file lock (see _locked()), so two hooks firing close
    together serialize instead of one silently clobbering the other's
    append. The final write is also atomic (temp file + os.rename) so a
    concurrent *reader* -- trace_worker.py's _read_records(), which does
    not take this lock -- can never observe a half-written file, only
    the fully-old or fully-new version.
    """
    redacted = redact_event(event)
    dedup_key = compute_dedup_key(session_id, event_type, sequence, redacted)
    record = {
        "dedup_key": dedup_key,
        "session_id": session_id,
        "event_type": event_type,
        "sequence": sequence,
        "event": redacted,
    }

    file_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = file_path.with_name(file_path.name + ".lock")

    with _locked(lock_path):
        lines: list[str] = []
        drop_count = 0
        if file_path.exists():
            raw_lines = file_path.read_text().splitlines()
            if raw_lines and raw_lines[0].startswith('{"_drop_count"'):
                drop_count = json.loads(raw_lines[0])["_drop_count"]
                lines = raw_lines[1:]
            else:
                lines = raw_lines

        lines.append(json.dumps(record))

        if len(lines) > max_lines:
            trim_n = max(1, int(max_lines * TRIM_FRACTION))
            drop_count += trim_n
            lines = lines[trim_n:]

        header = json.dumps({"_drop_count": drop_count})
        content = "\n".join([header] + lines) + "\n"

        tmp_path = file_path.with_name(file_path.name + f".tmp.{os.getpid()}.{time.monotonic_ns()}")
        tmp_path.write_text(content)
        os.replace(tmp_path, file_path)  # atomic on the same filesystem

    return record


def read_drop_count(file_path: Path) -> int:
    if not file_path.exists():
        return 0
    first_line = file_path.read_text().splitlines()[:1]
    if first_line and first_line[0].startswith('{"_drop_count"'):
        return json.loads(first_line[0])["_drop_count"]
    return 0
