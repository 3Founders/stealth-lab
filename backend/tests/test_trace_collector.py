import json
from pathlib import Path

import pytest

from app.services.trace_collector import append_event, compute_dedup_key, mark_worker_seen, read_drop_count


def test_dedup_key_is_deterministic_for_identical_inputs():
    key1 = compute_dedup_key("sess1", "PostToolUse", 5, {"a": 1})
    key2 = compute_dedup_key("sess1", "PostToolUse", 5, {"a": 1})
    assert key1 == key2


def test_dedup_key_differs_for_different_sequence():
    key1 = compute_dedup_key("sess1", "PostToolUse", 5, {"a": 1})
    key2 = compute_dedup_key("sess1", "PostToolUse", 6, {"a": 1})
    assert key1 != key2


def test_dedup_key_differs_for_different_session():
    key1 = compute_dedup_key("sess1", "PostToolUse", 5, {"a": 1})
    key2 = compute_dedup_key("sess2", "PostToolUse", 5, {"a": 1})
    assert key1 != key2


def test_dedup_key_is_stable_regardless_of_dict_key_order():
    """Real, meaningful case: json.dumps with sort_keys must make this
    true, or the same logical payload could produce two different keys
    depending on how the caller happened to construct the dict."""
    key1 = compute_dedup_key("sess1", "PostToolUse", 5, {"a": 1, "b": 2})
    key2 = compute_dedup_key("sess1", "PostToolUse", 5, {"b": 2, "a": 1})
    assert key1 == key2


def test_append_writes_a_real_readable_line(tmp_path: Path):
    f = tmp_path / "events.jsonl"
    record = append_event(
        {"event_type": "PostToolUse", "tool_output": {"stdout": "ok"}},
        f, session_id="sess1", event_type="PostToolUse", sequence=1,
    )
    assert f.exists()
    lines = f.read_text().splitlines()
    assert len(lines) == 2  # header + 1 record
    parsed = json.loads(lines[1])
    assert parsed["dedup_key"] == record["dedup_key"]
    assert parsed["session_id"] == "sess1"


def test_append_redacts_before_writing_to_disk(tmp_path: Path):
    """Real, important case: the file on disk must never contain the raw
    secret -- confirms the collector actually calls redaction, not just
    that redaction exists as a separately-tested unit."""
    f = tmp_path / "events.jsonl"
    append_event(
        {"event_type": "PostToolUse", "tool_output": {"stdout": "AKIAIOSFODNN7EXAMPLE"}},
        f, session_id="sess1", event_type="PostToolUse", sequence=1,
    )
    raw_file_content = f.read_text()
    assert "AKIAIOSFODNN7EXAMPLE" not in raw_file_content
    assert "[REDACTED:aws_access_key]" in raw_file_content


def test_multiple_appends_accumulate_in_order(tmp_path: Path):
    f = tmp_path / "events.jsonl"
    for i in range(5):
        append_event(
            {"event_type": "PostToolUse", "n": i},
            f, session_id="sess1", event_type="PostToolUse", sequence=i,
        )
    lines = f.read_text().splitlines()
    assert len(lines) == 6  # header + 5 records
    seqs = [json.loads(l)["sequence"] for l in lines[1:]]
    assert seqs == [0, 1, 2, 3, 4]


def test_bounded_file_drops_oldest_when_over_budget(tmp_path: Path):
    """A2 real update: trimming is now gated on mark_worker_seen() -- a
    worker must have confirmed reading a line before it can be trimmed
    (see the dedicated no-trim-without-worker-progress test below).
    This test simulates a worker that has already seen everything
    written so far, which is the case trimming is meant to cover."""
    f = tmp_path / "events.jsonl"
    max_lines = 10
    for i in range(15):
        append_event(
            {"event_type": "PostToolUse", "n": i},
            f, session_id="sess1", event_type="PostToolUse", sequence=i,
            max_lines=max_lines,
        )
        mark_worker_seen(f, i + 1)  # simulate the worker tailing in real time
    lines = f.read_text().splitlines()
    records = [json.loads(l) for l in lines[1:]]
    # Real check: the SURVIVING records are the most RECENT ones, not an
    # arbitrary subset -- confirms "drop oldest" is actually oldest-first,
    # not just "drop something."
    seqs = [r["sequence"] for r in records]
    assert seqs == sorted(seqs)  # still in order
    assert seqs[-1] == 14  # the most recent event always survives
    assert 0 not in seqs  # the very first event was dropped


def test_drop_count_is_recorded_and_readable(tmp_path: Path):
    f = tmp_path / "events.jsonl"
    max_lines = 10
    for i in range(15):
        append_event(
            {"event_type": "PostToolUse", "n": i},
            f, session_id="sess1", event_type="PostToolUse", sequence=i,
            max_lines=max_lines,
        )
        mark_worker_seen(f, i + 1)
    drop_count = read_drop_count(f)
    assert drop_count > 0, "expected some real drops given 15 events into a 10-line budget"


def test_a2_trimming_never_discards_events_the_worker_has_not_seen(tmp_path: Path):
    """The actual A2 guarantee, tested directly: with NO mark_worker_seen()
    call at all (the worker is down, or hasn't run yet -- the exact
    scenario the handoff flagged: 'if the worker is down during one 50k
    burst, those events are gone'), the file must be allowed to exceed
    max_lines rather than silently drop unread data."""
    f = tmp_path / "events.jsonl"
    max_lines = 10
    for i in range(15):
        append_event(
            {"event_type": "PostToolUse", "n": i},
            f, session_id="sess1", event_type="PostToolUse", sequence=i,
            max_lines=max_lines,
        )
    lines = f.read_text().splitlines()
    records = [json.loads(l) for l in lines[1:]]
    seqs = [r["sequence"] for r in records]
    assert seqs == list(range(15)), "every event must survive -- nothing was ever confirmed read"
    assert read_drop_count(f) == 0


def test_a2_trimming_only_removes_up_to_the_workers_high_water_mark(tmp_path: Path):
    """Partial-progress case: the worker saw the first 5 events (of 15
    written, max_lines=10), so compaction may trim at most 5 lines --
    even though the naive 'drop oldest 20%' math might want to trim
    more, it must never trim past what's been confirmed."""
    f = tmp_path / "events.jsonl"
    max_lines = 10
    for i in range(15):
        append_event(
            {"event_type": "PostToolUse", "n": i},
            f, session_id="sess1", event_type="PostToolUse", sequence=i,
            max_lines=max_lines,
        )
        if i == 4:
            mark_worker_seen(f, 5)  # worker confirmed only the first 5

    lines = f.read_text().splitlines()
    records = [json.loads(l) for l in lines[1:]]
    seqs = [r["sequence"] for r in records]
    assert min(seqs) == 5, "exactly the worker-confirmed prefix (0-4) should have been trimmed, no more"
    assert 14 in seqs


def test_no_drop_count_when_never_over_budget(tmp_path: Path):
    f = tmp_path / "events.jsonl"
    for i in range(3):
        append_event(
            {"event_type": "PostToolUse", "n": i},
            f, session_id="sess1", event_type="PostToolUse", sequence=i,
            max_lines=100,
        )
    assert read_drop_count(f) == 0


def test_read_drop_count_on_nonexistent_file_is_zero(tmp_path: Path):
    assert read_drop_count(tmp_path / "does_not_exist.jsonl") == 0


def test_concurrent_appends_do_not_lose_updates(tmp_path: Path):
    """Real, live confirmation of the fix for the race condition flagged
    in the last handoff: append_event() used to do a full
    read-modify-write with no locking, so two writers racing on the same
    file could silently clobber each other (a classic lost update) --
    "fine for one hook firing at a time, unsafe the moment two fire close
    together (a normal scenario, not an edge case)". Fire many real
    concurrent writers at the same file from real OS threads (not
    asyncio -- flock is what's actually being exercised, and threads
    give genuinely overlapping syscalls, unlike cooperative asyncio
    tasks) and confirm every single append survives."""
    import threading

    f = tmp_path / "concurrent_events.jsonl"
    n_writers = 40
    errors: list[Exception] = []

    def _write(i: int) -> None:
        try:
            append_event(
                {"event_type": "PostToolUse", "tool_output": {"i": i}},
                f, session_id="sess-concurrent", event_type="PostToolUse", sequence=i,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_write, args=(i,)) for i in range(n_writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"writer(s) raised: {errors}"

    lines = f.read_text().splitlines()
    assert lines[0].startswith('{"_drop_count"')
    records = [json.loads(l) for l in lines[1:]]
    assert len(records) == n_writers, (
        f"expected {n_writers} surviving records, got {len(records)} -- "
        "a lost update under concurrent writers"
    )
    sequences = {r["sequence"] for r in records}
    assert sequences == set(range(n_writers)), "some writer's record was overwritten, not just delayed"


def test_a_stale_lock_file_left_by_a_crashed_writer_does_not_deadlock_future_writers(tmp_path: Path):
    """A crashed process that held the lock would have released it on
    process exit (flock is tied to the open file description, not the
    lock *file* on disk) -- so a leftover .lock file itself must not
    block a fresh writer from acquiring the lock again."""
    f = tmp_path / "events.jsonl"
    lock_path = f.with_name(f.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("")  # simulate a stale lock file with no active holder

    record = append_event(
        {"event_type": "PostToolUse"}, f, session_id="sess1", event_type="PostToolUse", sequence=1,
    )
    assert record["session_id"] == "sess1"
