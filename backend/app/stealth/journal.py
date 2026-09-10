"""
`.stealth/events.jsonl` -- the durable, append-only local runtime journal,
plus the single-writer lock that serialises every mutation of `.stealth/`.

One JSON object per line, each with a strictly monotonic integer `seq`
(1-based). The journal is the local source of truth for:

  * `meta.json.projection_revision` -- the latest `seq` (monotonic, unlike
    a wall-clock stamp), so "is this projection newer than that one" is a
    plain integer compare;
  * exploration state (P4) -- `exploration_opened` / `exploration_closed`
    events are folded to rebuild `exploration.md` on every regeneration;
  * an audit trail of `projection_regenerated` / `knowledge_fault` events.

`SingleWriterLock` is an advisory `.stealth/.lock` file (`O_CREAT|O_EXCL`,
cross-platform -- no `fcntl`). It records the holder pid + a timestamp and
is stolen if older than `LOCK_STALE_SECONDS` (a crashed writer must not
wedge the workspace forever). Contention past `_LOCK_RETRIES` raises
`StealthLockError`.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Iterable

from app.stealth.legacy_context import STEALTH_DIRNAME

JOURNAL_NAME = "events.jsonl"
LOCK_NAME = ".lock"
LOCK_STALE_SECONDS = 60.0
_LOCK_RETRIES = 100
_LOCK_SLEEP = 0.05


class StealthLockError(RuntimeError):
    """Raised when the single-writer lock cannot be acquired."""


def _stealth_dir(workspace_root: str) -> str:
    return os.path.join(workspace_root, STEALTH_DIRNAME)


def journal_path(workspace_root: str) -> str:
    return os.path.join(_stealth_dir(workspace_root), JOURNAL_NAME)


def _lock_path(workspace_root: str) -> str:
    return os.path.join(_stealth_dir(workspace_root), LOCK_NAME)


class SingleWriterLock:
    """Context manager. `with SingleWriterLock(workspace_root): ...`."""

    def __init__(self, workspace_root: str):
        self._path = _lock_path(workspace_root)
        self._dir = os.path.dirname(self._path)
        self._acquired = False

    def _try_create(self) -> bool:
        try:
            fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        try:
            os.write(fd, json.dumps({"pid": os.getpid(), "ts": time.time()}).encode("utf-8"))
        finally:
            os.close(fd)
        return True

    def _steal_if_stale(self) -> None:
        try:
            age = time.time() - os.path.getmtime(self._path)
        except OSError:
            return
        if age > LOCK_STALE_SECONDS:
            try:
                os.unlink(self._path)
            except OSError:
                pass

    def __enter__(self) -> "SingleWriterLock":
        os.makedirs(self._dir, exist_ok=True)
        for attempt in range(_LOCK_RETRIES):
            if self._try_create():
                self._acquired = True
                return self
            if attempt % 20 == 19:
                self._steal_if_stale()
            time.sleep(_LOCK_SLEEP)
        raise StealthLockError(
            f"could not acquire {self._path} after {_LOCK_RETRIES} tries -- another writer holds it"
        )

    def __exit__(self, *exc: Any) -> None:
        if self._acquired:
            try:
                os.unlink(self._path)
            except OSError:
                pass
            self._acquired = False


def _iter_lines(path: str) -> Iterable[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield line
    except FileNotFoundError:
        return


def latest_seq(workspace_root: str) -> int:
    """Highest `seq` on record, 0 if the journal is empty/absent. A
    corrupt trailing line is tolerated -- the last *parseable* seq wins."""
    best = 0
    for line in _iter_lines(journal_path(workspace_root)):
        try:
            seq = int(json.loads(line)["seq"])
        except (ValueError, KeyError, TypeError):
            continue
        if seq > best:
            best = seq
    return best


def read_events(workspace_root: str, *, since_seq: int = 0) -> list[dict]:
    """Every event with `seq > since_seq`, in order. Unparseable lines
    are skipped, not raised."""
    out: list[dict] = []
    for line in _iter_lines(journal_path(workspace_root)):
        try:
            evt = json.loads(line)
            seq = int(evt["seq"])
        except (ValueError, KeyError, TypeError):
            continue
        if seq > since_seq:
            out.append(evt)
    out.sort(key=lambda e: e["seq"])
    return out


def _append_locked(workspace_root: str, records: list[tuple[str, dict]]) -> list[int]:
    path = journal_path(workspace_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    seq = latest_seq(workspace_root)
    assigned: list[int] = []
    lines: list[str] = []
    for event_type, payload in records:
        seq += 1
        rec = {"seq": seq, "ts": time.time(), "type": event_type, **payload}
        lines.append(json.dumps(rec, default=str))
        assigned.append(seq)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return assigned


def append_event(
    workspace_root: str, event_type: str, *, _lock_held: bool = False, **payload: Any
) -> int:
    """Append one event, return its `seq`. Takes the single-writer lock
    unless `_lock_held` says the caller already holds it."""
    if _lock_held:
        return _append_locked(workspace_root, [(event_type, payload)])[0]
    with SingleWriterLock(workspace_root):
        return _append_locked(workspace_root, [(event_type, payload)])[0]


def append_events(
    workspace_root: str, events: list[tuple[str, dict]], *, _lock_held: bool = False
) -> list[int]:
    """Append many events under one lock; return their seqs in order."""
    if not events:
        return []
    if _lock_held:
        return _append_locked(workspace_root, events)
    with SingleWriterLock(workspace_root):
        return _append_locked(workspace_root, events)
