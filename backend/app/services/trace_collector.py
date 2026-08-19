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
import hashlib
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.services.trace_redaction import redact_event

# A1 real fix (code review): the old version unconditionally
# `import fcntl`, which does not exist on Windows -- this module could
# not even be IMPORTED on the platform at least one real collaborator on
# this repo actually develops on. fcntl/msvcrt are both optional here;
# exactly one is used, chosen at runtime by platform, and the whole
# module still imports cleanly on either OS.
if sys.platform == "win32":
    import msvcrt
    fcntl = None
else:
    import fcntl
    msvcrt = None

DEFAULT_MAX_LINES = 50_000
TRIM_FRACTION = 0.2  # when over budget, drop the oldest 20% at once,
                      # not one line at a time -- batches the O(n)
                      # compaction cost across many appends instead of
                      # paying it on every single one. (Previously false
                      # in practice, since every append WAS an O(n)
                      # rewrite regardless -- see A1 fix below, which is
                      # what actually makes this comment true now.)

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


def _try_lock(fd: int) -> bool:
    """Returns True if the exclusive lock was acquired, False if it's
    currently held elsewhere (never raises for the ordinary contention
    case on either platform)."""
    if sys.platform == "win32":
        try:
            # msvcrt has no separate LOCK_EX concept -- locking any
            # region non-blocking (LK_NBLCK) is exclusive by definition.
            # Lock a fixed 1-byte region at offset 0; the lock file's
            # actual content is irrelevant, only its existence as a
            # mutex handle matters (same role the POSIX branch gives
            # the fd itself).
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    else:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            return False


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _locked(lock_path: Path, timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """
    Real fix for the race condition flagged in the last handoff:
    append_event() previously did a full read-modify-write of the whole
    collector file with no locking at all -- safe for one hook firing at
    a time, silently lossy (a classic lost-update) the moment two fire
    close together, which is the normal case, not an edge case, for a
    coding agent that can spawn subagents or tool calls in parallel.

    Cross-platform (A1 fix, second half): fcntl.flock on POSIX,
    msvcrt.locking on Windows -- "that's the path that must work here"
    per the code review, since real collaborators on this repo develop
    on both. Uses a separate `.lock` file (not the data file itself), so
    the lock's lifetime is independent of the data file being atomically
    replaced/appended underneath it. Only fixes concurrent *writers* on
    one machine -- neither primitive is distributed; a second host
    writing to a network-shared path is out of scope, consistent with
    this module's already-stated local-first, single-machine design.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        deadline = time.monotonic() + timeout
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise LockTimeout(
                    f"could not acquire lock on {lock_path} within {timeout}s "
                    "-- another writer is holding it unusually long"
                )
            time.sleep(LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


def _meta_path_for(file_path: Path) -> Path:
    return file_path.with_name(file_path.name + ".meta.json")


def _lock_path_for(file_path: Path) -> Path:
    return file_path.with_name(file_path.name + ".lock")


def _read_meta(meta_path: Path) -> dict:
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_meta(meta_path: Path, meta: dict) -> None:
    tmp_path = meta_path.with_name(meta_path.name + f".tmp.{os.getpid()}.{time.monotonic_ns()}")
    tmp_path.write_text(json.dumps(meta))
    os.replace(tmp_path, meta_path)


def mark_worker_seen(file_path: Path, seen_count: int) -> None:
    """
    A2 real fix, called by trace_worker.py after a successful run: the
    old design let compaction "drop the oldest 20%" purely by local line
    count, with zero knowledge of whether the worker had read those
    lines yet -- "if the worker is down during one 50k burst, those
    events are gone... drop_count increments and nothing ever reads it,
    so the loss is invisible in the DB."

    Records a real, monotonic high-water mark (never decreases -- a
    worker run that saw fewer lines than a previous run, e.g. because it
    ran on a smaller file, must not un-mark lines a prior run already
    confirmed) that append_event()'s compaction below will not trim past.
    Runs under the same lock as append_event() -- the worker and any
    concurrent collector append must not race on this file.
    """
    lock_path = _lock_path_for(file_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _locked(lock_path):
        meta_path = _meta_path_for(file_path)
        meta = _read_meta(meta_path)
        meta["worker_seen_count"] = max(meta.get("worker_seen_count", 0), seen_count)
        _write_meta(meta_path, meta)


def compute_dedup_key(session_id: str, event_type: str, sequence: int, payload: dict) -> str:
    """
    Deterministic composite key (ticket 06's answer): hooks carry no
    native event id, so the collector computes one instead of trusting
    one to arrive pre-formed. Payload hash included so two events with
    the same session/type/sequence but genuinely different content
    (which should not happen in practice, but might under a bug
    elsewhere) don't silently collide into one dedup key.

    Real fix (code review, "smaller, worth knowing"): must be computed
    from the payload BEFORE `_redaction` metadata is injected into it --
    hashing the post-redaction dict (as append_event used to) means
    adding one new redaction pattern silently changes the dedup_key for
    every future occurrence of an already-seen logical event, breaking
    the "idempotent replay" property trace_worker.py's own docstring
    promises. Callers must pass the pre-`_redaction` event.
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
    oldest, with a running drop counter -- silent loss is exactly what
    makes memory correctness unauditable later, so the count is real,
    visible data (in the sidecar meta file, and from there surfaced into
    agent_traces.collector_drop_count by the worker), not a log message
    that can be missed.

    A1 real fix (code review, real severity -- was previously a rewrite,
    not an append): the normal-case path below is now a genuine O(1)
    append -- one open("ab"), one write() of a single line, one flush()
    + os.fsync(). No read of the existing file at all in the common
    case. This also fixes the crash-data-loss half of A1 for free: the
    old version's full-file write_text("w") truncated first, so a
    process killed mid-write lost the ENTIRE file; an append-mode write
    can, at absolute worst, leave one incomplete trailing line, and
    trace_worker.py's A4 fix (per-line try/except, quarantine on parse
    failure) already treats exactly that case as a single quarantined
    line rather than a stalled pipeline -- these two fixes compose
    correctly together, not by accident.

    Compaction (dropping the oldest lines once over max_lines) is still
    a real full-file read + rewrite -- there's no way to remove lines
    from the FRONT of a flat file without one -- but it now only runs
    when actually over budget, and even then only trims what
    mark_worker_seen() has confirmed the worker has read (see A2). The
    normal per-event cost is O(1), matching what this module's docstring
    always claimed but, before this fix, did not deliver.

    Concurrency: the whole operation runs under an exclusive file lock
    (see _locked()), so two hooks firing close together serialize
    instead of one silently clobbering the other's append. Compaction's
    rewrite is additionally atomic (temp file + os.replace) so a
    concurrent *reader* (trace_worker.py's _read_records(), which does
    not take this lock) can never observe a half-written file during
    compaction, only the fully-old or fully-new version.
    """
    redacted = redact_event(event)
    dedup_key = compute_dedup_key(session_id, event_type, sequence, event)
    record = {
        "dedup_key": dedup_key,
        "session_id": session_id,
        "event_type": event_type,
        "sequence": sequence,
        "event": redacted,
    }
    line = json.dumps(record)

    file_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path_for(file_path)
    meta_path = _meta_path_for(file_path)

    with _locked(lock_path):
        # The real, O(1) append. fsync so a crash immediately after
        # append_event() returns cannot lose the write -- the caller (a
        # Claude Code hook) has no way to know to retry.
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

        meta = _read_meta(meta_path)
        line_count = meta.get("line_count", 0) + 1
        drop_count = meta.get("drop_count", 0)
        worker_seen = meta.get("worker_seen_count", 0)

        if line_count > max_lines:
            trim_n = max(1, int(max_lines * TRIM_FRACTION))
            # A2: only trim what the worker has confirmed reading. If
            # worker_seen is less than trim_n, trim only that much -- the
            # file temporarily exceeds max_lines rather than silently
            # discarding data the worker never saw.
            safe_trim_n = min(trim_n, worker_seen, line_count)
            if safe_trim_n > 0:
                lines = file_path.read_text().splitlines()
                remaining = lines[safe_trim_n:]
                content = "\n".join(remaining) + ("\n" if remaining else "")
                tmp_path = file_path.with_name(
                    file_path.name + f".tmp.{os.getpid()}.{time.monotonic_ns()}"
                )
                tmp_path.write_text(content)
                os.replace(tmp_path, file_path)  # atomic on the same filesystem

                line_count -= safe_trim_n
                drop_count += safe_trim_n
                worker_seen -= safe_trim_n

        meta["line_count"] = line_count
        meta["drop_count"] = drop_count
        meta["worker_seen_count"] = worker_seen
        _write_meta(meta_path, meta)

    return record


def read_drop_count(file_path: Path) -> int:
    """
    Real fix: drop_count now lives in the sidecar meta file (see A1 --
    the data file no longer carries a synthetic header line at all,
    since that scheme couldn't coexist with a real O(1) append). Old
    header-line-based files are not specially migrated -- this is
    ephemeral collector-side state, not durable data; a fresh meta file
    with drop_count=0 on first append to a pre-existing legacy file is
    the correct, honest behaviour (the alternative -- guessing a
    historical count from a format this function no longer trusts -- is
    worse).
    """
    meta = _read_meta(_meta_path_for(file_path))
    return meta.get("drop_count", 0)
