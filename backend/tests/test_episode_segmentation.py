"""
Proving tests for the Band 2 promotion of the episode segmenter
(trace_worker.py's episode-assembly section) -- fully OFFLINE: no
database, no clock, no network. Every fixture is a synthetic transcript
shaped like the REAL Claude Code schema documented in
experiments/episode_assembly/FINDINGS.md:

  - 16 distinct line types (the 10 timestampless ones FINDINGS names --
    ai-title, file-history-snapshot, permission-mode, agent-name,
    agent-setting, relocated, worktree-state, agent-color, last-prompt,
    mode -- plus user/assistant/system/summary/attachment/
    queue-operation), with schema drift tolerated intra-file;
  - missing timestamps on boundary lines (9+ of 16 types carry none);
  - FOREST sessions (>1 parentUuid: null root -- compaction/resume),
    which a single-chain walk would silently truncate;
  - sibling subagent files joined by sourceToolAssistantUUID.

Every promoted rule is pinned to its FINDINGS.md evidence:
  Rule-A genuine prompts .... only top-level boundary
  <=2-event episodes ........ fold into successor (18% pathology)
  >200-event episodes ....... subdivide at internal commit/test ONLY
  sourceToolAssistantUUID ... nested child episodes, strict join
  idle gaps ................. DROPPED: no temporal cut exists at all

Writer tests follow this repo's offline house style (sync tests driving
coroutines via asyncio.run; a FakePool captures SQL -- see
test_band1_9c_changesets.py).
"""
import asyncio
import json
from contextlib import asynccontextmanager

import pytest

import app.services.trace_worker as tw
from app.services.trace_worker import (
    OVERSIZE_SUBDIVIDE_EVENTS,
    TRIVIAL_MERGE_MAX_EVENTS,
    assemble_episodes,
    load_transcript,
)


# ----------------------------------------------------------- line builders


def ts(minute: int, second: int = 0) -> str:
    # Real transcripts emit trailing-Z JS timestamps.
    return f"2026-08-01T10:{minute:02d}:{second:02d}.000Z"


def line(kind: str, **kw) -> dict:
    """One transcript line of any of the 16 observed types."""
    rec = {"type": kind}
    rec.update(kw)
    return rec


def user_prompt(text="do the thing", i=None, timestamp=None, uuid=None) -> dict:
    kw = {}
    if timestamp is not None:
        kw["timestamp"] = timestamp
    if uuid is not None:
        kw["uuid"] = uuid
    if i is not None:
        text = f"{text} #{i}"
    return line("user", message={"content": text}, **kw)


def assistant(n=1, cmd=None, spawn=False, timestamp=None, uuid=None):
    out = []
    for k in range(n):
        content = []
        if k == n - 1:
            if cmd is not None:
                content.append({"type": "tool_use", "name": "Bash",
                                "input": {"command": cmd}})
            if spawn:
                content.append({"type": "tool_use", "name": "Agent",
                                "input": {"prompt": "..."}})
        out.append(line("assistant", message={"content": content},
                        timestamp=timestamp,
                        **({"uuid": uuid} if uuid and k == n - 1 else {})))
        timestamp = None
        uuid = None
    return out[0] if len(out) == 1 else out


def noise(kind: str) -> dict:
    """Timestampless non-conversational line (9-10 of 16 real types)."""
    return line(kind)


def episode_bounds(assembly):
    return [(e.start, e.end) for e in assembly.episodes]


def flags_of(assembly):
    return [sorted(e.flags) for e in assembly.episodes]


# ---------------------------------------------------------------- fixtures

ALL_16_TYPES = [
    "ai-title", "file-history-snapshot", "permission-mode", "agent-name",
    "agent-setting", "relocated", "worktree-state", "agent-color",
    "last-prompt", "mode",
    "user", "assistant", "system", "summary", "attachment",
    "queue-operation",
]


def sixteen_type_session():
    """A session touching all 16 observed line types. Genuine prompts sit
    between stretches of every other type; several lines carry NO
    timestamp at all (as in the real corpus)."""
    lines = []
    idx = 0

    def add(*recs):
        nonlocal idx
        for r in recs:
            r.setdefault("parentUuid", None if idx == 0 else f"u{idx - 1}")
            r.setdefault("uuid", f"u{idx}")
            lines.append(r)
            idx += 1

    add(noise("ai-title"), noise("file-history-snapshot"),
        noise("permission-mode"), noise("agent-name"),
        noise("agent-setting"))
    add(user_prompt(timestamp=ts(0)))          # prompt 1
    add(noise("relocated"), noise("worktree-state"), noise("agent-color"),
        noise("last-prompt"), noise("mode"))
    add(assistant(cmd="pytest -q"), noise("attachment"),
        line("system", content="hook ran"), line("summary", summary="..."),
        line("queue-operation"))
    add(user_prompt(i=2))                       # prompt 2 -- NO timestamp
    add(assistant(), noise("mode"), noise("permission-mode"),
        line("unknown-future-type-v12"))        # drift beyond the known 16
    add(user_prompt(i=3, timestamp=ts(9)))      # prompt 3
    add(assistant(), assistant())               # keep last episode non-trivial
    assert {ln.get("type") for ln in lines} >= set(ALL_16_TYPES) | {
        "unknown-future-type-v12"}
    return lines


@pytest.fixture(name="sixteen_types")
def _sixteen():
    return sixteen_type_session()


# ------------------------------------------------- Rule-A primary boundary


def test_rule_a_prompts_are_the_only_top_level_boundary(sixteen_types):
    a = assemble_episodes(sixteen_types)
    # Prompts sit at indices 5, 16, 21 (see builder); nothing else cuts.
    assert episode_bounds(a) == [(0, 5), (5, 16), (16, 21), (21, len(sixteen_types))]
    assert a.trivial_folds == 0 and a.subdivided_episodes == 0

def test_non_prompt_prefixes_never_open_episodes():
    for prefix in ("<task-notification", "<scheduled-wakeup",
                   "<background-task", "[Request interrupted"):
        lines = [user_prompt(timestamp=ts(0)), assistant(),
                 line("user", message={"content":
                      f"{prefix}\nsomething finished"}),
                 assistant()]
        a = assemble_episodes(lines)
        assert episode_bounds(a) == [(0, 4)], prefix
        assert a.trivial_folds == 0


def test_meta_compact_sidechain_and_tool_result_user_lines_not_prompts():
    not_prompts = [
        line("user", isMeta=True, message={"content": "meta"}),
        line("user", isCompactSummary=True, message={"content": "compact"}),
        line("user", isSidechain=True, message={"content": "side"}),
        line("user", message={"content": [
            {"type": "tool_result", "content": "out"}]}),
        line("assistant", message={"content": "not even user type"}),
    ]
    lines = [user_prompt(timestamp=ts(0))]
    for np in not_prompts:
        lines += [np, assistant()]
    a = assemble_episodes(lines)
    assert episode_bounds(a) == [(0, len(lines))]


# --------------------------------------------- missing timestamps / ranges


def test_missing_timestamp_boundary_still_opens_and_reports_none_range():
    lines = [user_prompt(timestamp=ts(1)), assistant(), assistant(),
             user_prompt(),                     # NO timestamp anywhere after
             assistant(), assistant()]
    a = assemble_episodes(lines)
    assert episode_bounds(a) == [(0, 3), (3, 6)]
    assert a.episodes[0].start_ts is not None
    assert a.episodes[0].end_ts is not None
    assert a.episodes[1].start_ts is None and a.episodes[1].end_ts is None


def test_partial_timestamps_report_min_max_over_available():
    lines = [user_prompt(timestamp=ts(5)), assistant(timestamp=ts(6)),
             assistant(),
             user_prompt(timestamp=ts(2)), assistant(timestamp=ts(3)),
             assistant()]
    a = assemble_episodes(lines)
    first, second = a.episodes
    assert first.start_ts.minute == 5 and first.end_ts.minute == 6
    assert second.start_ts.minute == 2 and second.end_ts.minute == 3


# ------------------------------------------------------- forest sessions


def test_forest_session_two_roots_is_not_truncated():
    """Compaction/resume creates extra parentUuid:null roots mid-file
    (FINDINGS: 46 roots across 36 sessions). File order must keep flowing;
    a chain walk would drop everything after the second root."""
    root_one = user_prompt(timestamp=ts(0))
    root_one["parentUuid"] = None
    mid = [root_one, assistant(), assistant()]
    root_two = user_prompt(timestamp=ts(30))
    root_two["parentUuid"] = None           # new root, same file
    tail = [root_two, assistant(), assistant()]
    a = assemble_episodes(mid + tail)
    assert episode_bounds(a) == [(0, 3), (3, 6)]
    roots = [i for i, ln in enumerate(mid + tail)
             if "parentUuid" in ln and ln["parentUuid"] is None]
    assert roots == [0, 3]


# ------------------------------------------------------------ merge rule


def _session_with_sizes(sizes):
    """Prompts separated by exactly sizes[i]-1 filler events."""
    lines = []
    for size in sizes:
        lines.append(user_prompt())
        lines += [assistant() for _ in range(size - 1)]
    return lines


def test_trivial_episode_folds_into_successor():
    a = assemble_episodes(_session_with_sizes([1, 10]))
    assert episode_bounds(a) == [(0, 11)]
    assert a.trivial_folds == 1
    assert "folded_trivial" in a.episodes[0].flags


def test_merge_threshold_is_inclusive_le_2():
    small = assemble_episodes(_session_with_sizes([TRIVIAL_MERGE_MAX_EVENTS, 10]))
    assert episode_bounds(small) == [(0, 12)]
    big = assemble_episodes(
        _session_with_sizes([TRIVIAL_MERGE_MAX_EVENTS + 1, 10]))
    assert episode_bounds(big) == [(0, 3), (3, 13)]
    assert big.trivial_folds == 0


def test_trailing_trivial_folds_into_predecessor():
    a = assemble_episodes(_session_with_sizes([10, 1]))
    assert episode_bounds(a) == [(0, 11)]
    assert a.trivial_folds == 1


def test_chained_trivials_collapse_transitively():
    a = assemble_episodes(_session_with_sizes([1, 1, 6]))
    assert episode_bounds(a) == [(0, 8)]
    assert a.trivial_folds == 2


def test_whole_session_single_trivial_event_stays_one_episode():
    a = assemble_episodes([user_prompt()])
    assert episode_bounds(a) == [(0, 1)]


# ------------------------------------------------------ subdivision rule


def _oversize_session(total, commit_at=None, test_at=None):
    lines = [user_prompt(timestamp=ts(0))]
    for i in range(total - 1):
        if i == commit_at:
            lines.append(assistant(cmd="git commit -m wip"))
        elif i == test_at:
            lines.append(assistant(cmd="npm run test"))
        else:
            lines.append(assistant())
    return lines


def test_oversize_subdivides_at_internal_commit_and_test_completions():
    a = assemble_episodes(_oversize_session(OVERSIZE_SUBDIVIDE_EVENTS + 50,
                                            commit_at=100, test_at=200))
    assert a.subdivided_episodes == 1
    # Each completing event CLOSES its sub-episode (left piece includes it).
    assert episode_bounds(a) == [(0, 102), (102, 202),
                                 (202, OVERSIZE_SUBDIVIDE_EVENTS + 50)]
    assert all("subdivided" in f for f in flags_of(a))


def test_commit_test_never_cut_a_normal_sized_episode():
    lines = [user_prompt(timestamp=ts(0)), assistant(cmd="git commit -m x"),
             assistant(cmd="pytest -q")]
    a = assemble_episodes(lines)
    assert episode_bounds(a) == [(0, 3)]          # metadata role, not a cut
    assert a.subdivided_episodes == 0


def test_oversize_without_internal_signal_flagged_never_invented_cuts():
    a = assemble_episodes(_oversize_session(OVERSIZE_SUBDIVIDE_EVENTS + 1))
    assert episode_bounds(a) == [(0, OVERSIZE_SUBDIVIDE_EVENTS + 1)]
    assert flags_of(a) == [["oversize_unsubdivided"]]
    assert a.subdivided_episodes == 0


def test_subdivided_pieces_still_over_threshold_keep_honest_flag():
    # One commit at 10, then >OVERSIZE events to the end: the LAST piece is
    # still oversize and must say so rather than pretend it was fixed.
    total = OVERSIZE_SUBDIVIDE_EVENTS * 2 + 20
    a = assemble_episodes(_oversize_session(total, commit_at=10))
    bounds = episode_bounds(a)
    assert bounds[0] == (0, 12)
    assert "oversize_unsubdivided" in a.episodes[-1].flags


# ------------------------------------------------------------ subagents


def test_subagent_sibling_file_joins_via_source_tool_assistant_uuid():
    main = [user_prompt(timestamp=ts(0)),
            assistant(spawn=True, uuid="spawn-uuid-1", timestamp=ts(1)),
            assistant(),
            user_prompt(timestamp=ts(5)),
            assistant(),
            assistant()]
    siblings = {"agent-abc123.jsonl": [
        line("user", isSidechain=True,
             sourceToolAssistantUUID="spawn-uuid-1", timestamp=ts(2)),
        line("assistant", sourceToolAssistantUUID="spawn-uuid-1",
             timestamp=ts(3)),
    ]}
    a = assemble_episodes(main, siblings)
    assert episode_bounds(a) == [(0, 3), (3, 6)]     # spawn is NOT a cut
    host = a.episodes[0]                              # contains index 1
    assert len(host.children) == 1
    child = host.children[0]
    assert child.source == "agent-abc123.jsonl"
    assert child.spawned_by == "spawn-uuid-1"
    assert "subagent" in child.flags
    assert child.n_events == 2
    assert child.start_ts.minute == 2 and child.end_ts.minute == 3
    assert a.subagent_files_seen == 1 and a.subagent_files_joined == 1


def test_subagent_lines_that_do_not_join_are_counted_not_attached():
    main = [user_prompt(timestamp=ts(0)), assistant()]
    siblings = {"agent-deadbe.jsonl": [
        line("user", sourceToolAssistantUUID="no-such-parent-uuid"),
    ]}
    a = assemble_episodes(main, siblings)
    assert all(not e.children for e in a.episodes)
    assert a.subagent_files_seen == 1 and a.subagent_files_joined == 0
    assert a.unjoined_subagent_lines == 1


def test_multiple_spawn_groups_in_one_file_each_get_a_child():
    main = [user_prompt(timestamp=ts(0)),
            assistant(spawn=True, uuid="s1"),
            assistant(spawn=True, uuid="s2")]
    siblings = {"agent-multi.jsonl": [
        line("user", sourceToolAssistantUUID="s2"),
        line("user", sourceToolAssistantUUID="s1"),
    ]}
    a = assemble_episodes(main, siblings)
    children = a.episodes[0].children
    assert [(c.spawned_by, c.start, c.end) for c in children] == [
        ("s1", 1, 2), ("s2", 0, 1)]              # deterministic by uuid


# --------------------------------------------------- idle-gap DROPPED


def test_idle_gap_creates_no_boundary_and_only_informs_range():
    """The dropped signal, proven absent: TWELVE HOURS of silence inside a
    single-prompt episode must NOT cut anything (FINDINGS: the gap
    distribution has no bimodality to threshold)."""
    lines = [user_prompt(timestamp=ts(0)),
             assistant(timestamp="2026-08-01T22:00:00.000Z"),
             assistant(timestamp="2026-08-01T23:59:00.000Z")]
    a = assemble_episodes(lines)
    assert episode_bounds(a) == [(0, 3)]
    assert a.episodes[0].start_ts.hour == 10
    assert a.episodes[0].end_ts.hour == 23
    # Non-negotiable formality: no temporal knob exists to retune.
    assert not hasattr(tw, "IDLE_GAP_THRESHOLD")


# ------------------------------------------------ zero prompts / empty


def test_zero_prompt_session_is_one_flagged_episode():
    lines = [assistant(), line("system", content="x")]
    a = assemble_episodes(lines)
    assert episode_bounds(a) == [(0, 2)]
    assert "zero_prompts" in a.episodes[0].flags


def test_empty_session_yields_no_episodes():
    a = assemble_episodes([])
    assert a.episodes == [] and a.main_lines == 0


def test_non_dict_records_counted_as_unparsed_but_span_covered():
    a = assemble_episodes([user_prompt(), "garbage-not-a-dict",
                           assistant()])
    assert episode_bounds(a) == [(0, 3)]
    assert a.unparsed_main_lines == 1


# -------------------------------------------------- load_transcript IO


def test_load_transcript_skips_torn_writes_and_blank_lines(tmp_path):
    good1 = json.dumps(user_prompt(timestamp=ts(0)))
    torn = '{"type":"user","message":{"cont'      # torn write, no close
    good2 = json.dumps(assistant())
    f = tmp_path / "sess.jsonl"
    f.write_text("\n".join([good1, "", torn, good2, "   "]), encoding="utf-8")
    records, bad = load_transcript(f)
    assert bad == 1
    assert len(records) == 2 and records[0]["type"] == "user"


# ------------------------------------------------ named-config retunability


def test_thresholds_are_named_config_monkeypatch_moves_behaviour(monkeypatch):
    monkeypatch.setattr(tw, "TRIVIAL_MERGE_MAX_EVENTS", 3)
    folded = assemble_episodes(_session_with_sizes([3, 10]))
    assert episode_bounds(folded) == [(0, 13)]

    monkeypatch.setattr(tw, "OVERSIZE_SUBDIVIDE_EVENTS", 5)
    lines = _session_with_sizes([7])            # one 7-event episode
    a = assemble_episodes(lines)
    assert episode_bounds(a) == [(0, 7)]
    assert flags_of(a) == [["oversize_unsubdivided"]]   # no internal signal


# ---------------------------------------------------------- fingerprints


def test_fingerprint_tracks_shape_not_message_content():
    shape_a = [user_prompt("write tests"), assistant(), user_prompt("run them"),
               assistant()]
    shape_b = [user_prompt("TOTALLY DIFFERENT WORDS"), assistant(),
               user_prompt("other words entirely"), assistant()]
    changed = [user_prompt("write tests"), assistant()]

    fa = [e.fingerprint("sess") for e in assemble_episodes(shape_a).episodes]
    fb = [e.fingerprint("sess") for e in assemble_episodes(shape_b).episodes]
    fc = [e.fingerprint("sess") for e in assemble_episodes(changed).episodes]

    assert fa == fb                    # same boundaries => same fingerprint
    assert fa != fc                    # different shape => different fp
    blob = json.dumps(fa)
    assert "write tests" not in blob.lower()


# ---------------------------------------------- writer (offline FakePool)


class FakeConn:
    def __init__(self, existing_rows=()):
        self.existing_rows = list(existing_rows)
        self.statements = []

    async def fetch(self, sql, *args):
        self.statements.append(("fetch", " ".join(sql.split()), args))
        return self.existing_rows

    async def fetchval(self, sql, *args):
        self.statements.append(("insert", " ".join(sql.split()), args))
        return f"generated-id-{len(self.statements)}"


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        @asynccontextmanager
        async def _cm():
            yield conn

        return _cm()


def _run_writer(assembly, session_id="sess-1", existing=()):
    conn = FakeConn(existing_rows=existing)
    stats = asyncio.run(tw.write_session_episodes(
        FakePool(conn), session_id=session_id, assembly=assembly))
    return conn, stats


INSERT_SQL_FRAGMENT = "INSERT INTO episodes"
SELECT_SQL_FRAGMENT = "metadata->>'assembly_fingerprint'"


def test_writer_preselects_fingerprints_then_inserts_parents_before_children():
    main = [user_prompt(timestamp=ts(0)),
            assistant(spawn=True, uuid="sp"),
            assistant(),
            user_prompt(timestamp=ts(1)),
            assistant(),
            assistant()]
    siblings = {"agent-x.jsonl": [
        line("assistant", sourceToolAssistantUUID="sp")]}
    asm = assemble_episodes(main, siblings)

    conn, stats = _run_writer(asm)
    kinds = [k for k, _, _ in conn.statements]
    assert kinds[0] == "fetch" and SELECT_SQL_FRAGMENT in conn.statements[0][1]
    inserts = [s for s in conn.statements if s[0] == "insert"]
    assert len(inserts) == 3                       # 2 parents + 1 child
    sql = inserts[0][1]
    for col in ("episode_type", "content_ref", "metadata", "session_id",
                "start_ts", "end_ts", "owner_id", "visibility",
                "parent_episode_id"):
        assert col in sql, col
    assert "::visibility_level" in sql
    # Exactly one insert carries a non-null parent_episode_id (the child);
    # it must reference a parent row id generated earlier in THIS run.
    linked = [i for i in inserts if i[2][-1] is not None]
    assert len(linked) == 1

    def _rid(ins):
        return f"generated-id-{conn.statements.index(ins) + 1}"

    child = linked[0]
    others = {_rid(i) for i in inserts if i is not child}
    assert child[2][-1] in others
    assert stats == {"parents_inserted": 2, "children_inserted": 1,
                     "skipped_existing": 0}


def test_writer_replay_skips_everything_already_persisted():
    asm = assemble_episodes(_session_with_sizes([5, 5]))
    fps = [{"id": f"row-{i}", "fp": e.fingerprint("sess-1")}
           for i, e in enumerate(asm.episodes)]
    conn, stats = _run_writer(asm, existing=fps)
    assert not [s for s in conn.statements if s[0] == "insert"]
    assert stats["skipped_existing"] == 2


def test_writer_child_links_to_existing_parent_after_partial_crash():
    """Crash-between-inserts replay: the parent row already exists but the
    child's insert never landed. The retry must link the child to the
    EXISTING parent row id, fetched from the fingerprint preselect."""
    main = [user_prompt(timestamp=ts(0)),
            assistant(spawn=True, uuid="sp"),
            assistant()]
    siblings = {"agent-y.jsonl": [
        line("assistant", sourceToolAssistantUUID="sp")]}
    asm = assemble_episodes(main, siblings)
    parent_fp = asm.episodes[0].fingerprint("sess-1")
    conn, stats = _run_writer(
        asm, existing=[{"id": "existing-parent-row", "fp": parent_fp}])
    child_insert = [s for s in conn.statements if s[0] == "insert"]
    assert len(child_insert) == 1
    assert child_insert[0][2][-1] == "existing-parent-row"
    assert stats == {"parents_inserted": 0, "children_inserted": 1,
                     "skipped_existing": 1}


def test_writer_content_ref_is_locator_never_message_text():
    secret = "SUPER-SECRET-PROMPT-WORDS"
    asm = assemble_episodes([user_prompt(secret), assistant()])
    conn, _ = _run_writer(asm)
    insert = [s for s in conn.statements if s[0] == "insert"][0]
    args = insert[2]
    assert "#main:0:" in args[1]           # file#source:start:end locator
    assert secret not in str(args)
