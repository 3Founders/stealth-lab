"""
G13 P2 -- `.stealth/events.jsonl` journal + single-writer lock
(`app.stealth.journal`), pure filesystem, no database.
"""
from __future__ import annotations

import json
import os
import threading
import time

import pytest

from app.stealth.journal import (
    SingleWriterLock,
    StealthLockError,
    append_event,
    append_events,
    journal_path,
    latest_seq,
    read_events,
)


def test_seq_is_monotonic_across_separate_append_calls(tmp_path):
    ws = str(tmp_path)
    s1 = append_event(ws, "a")
    s2 = append_event(ws, "b", detail="x")
    s3 = append_events(ws, [("c", {}), ("d", {"n": 1})])
    assert [s1, s2] == [1, 2]
    assert s3 == [3, 4]
    assert latest_seq(ws) == 4
    types = [e["type"] for e in read_events(ws)]
    assert types == ["a", "b", "c", "d"]
    assert read_events(ws)[3]["n"] == 1


def test_since_seq_filters(tmp_path):
    ws = str(tmp_path)
    append_events(ws, [("e", {}) for _ in range(5)])
    assert [e["seq"] for e in read_events(ws, since_seq=3)] == [4, 5]
    assert read_events(ws, since_seq=99) == []


def test_corrupt_trailing_line_is_tolerated(tmp_path):
    ws = str(tmp_path)
    append_event(ws, "ok")
    with open(journal_path(ws), "a", encoding="utf-8") as f:
        f.write('{"seq": 2, "type": "half')          # truncated / no newline close
    assert latest_seq(ws) == 1                         # last *parseable* seq
    assert [e["type"] for e in read_events(ws)] == ["ok"]
    # a subsequent append still advances from the last good seq
    assert append_event(ws, "next") == 2


def test_lock_is_exclusive_and_raises_on_contention(tmp_path, monkeypatch):
    ws = str(tmp_path)
    os.makedirs(os.path.join(ws, ".stealth"), exist_ok=True)
    # make the retry window short so the test is fast
    monkeypatch.setattr("app.stealth.journal._LOCK_RETRIES", 3)
    monkeypatch.setattr("app.stealth.journal._LOCK_SLEEP", 0.01)
    held = SingleWriterLock(ws)
    held.__enter__()
    try:
        with pytest.raises(StealthLockError):
            with SingleWriterLock(ws):
                pass
    finally:
        held.__exit__(None, None, None)
    # released -> acquirable again
    with SingleWriterLock(ws):
        pass


def test_stale_lock_is_stolen(tmp_path, monkeypatch):
    ws = str(tmp_path)
    lockdir = os.path.join(ws, ".stealth")
    os.makedirs(lockdir, exist_ok=True)
    lockfile = os.path.join(lockdir, ".lock")
    with open(lockfile, "w") as f:
        f.write(json.dumps({"pid": 999999, "ts": 0}))
    old = time.time() - 10_000
    os.utime(lockfile, (old, old))
    monkeypatch.setattr("app.stealth.journal.LOCK_STALE_SECONDS", 1.0)
    monkeypatch.setattr("app.stealth.journal._LOCK_RETRIES", 40)
    monkeypatch.setattr("app.stealth.journal._LOCK_SLEEP", 0.005)
    with SingleWriterLock(ws):        # steals the stale lock rather than raising
        pass


def test_concurrent_appenders_never_collide_on_seq(tmp_path):
    ws = str(tmp_path)
    os.makedirs(os.path.join(ws, ".stealth"), exist_ok=True)
    errors: list[BaseException] = []

    def worker():
        try:
            for _ in range(10):
                append_event(ws, "concurrent")
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    seqs = [e["seq"] for e in read_events(ws)]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs)) == 40
