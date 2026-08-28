"""
The worker half of the ingestion pipeline (ticket 16, memory-substrate
map). Reads what the collector appended to its local file and does the
actual, durable database write -- this is the boundary ticket 16 calls
out: "the durable step is the local append, and the job table exists to
track compilation work." Everything from here downstream (normalization,
episode assembly) is replayable, per spec.md's own requirement; only the
raw persistence below is treated as the one irreversible step.

Idempotent by design, not by tracking an offset: every collector event
carries a real dedup_key (computed by the collector), and every insert
in process_collector_file() uses INSERT ... ON CONFLICT (dedup_key) DO
NOTHING RETURNING id. Re-running that path against the same file (e.g.
after a crash) re-processes already-seen lines harmlessly.

As of Band 2 this module is also where episode assembly stopped being a
prototype: experiments/episode_assembly/segment.py's three candidate rule
sets were run over 36 real sessions (28,969 lines) and the validated
survivors -- Rule-A genuine-prompt boundaries, the <=2-event merge, >200-
event subdivision, sourceToolAssistantUUID subagent joins, idle-gap
DROPPED -- are promoted below (assemble_episodes /
process_transcript_session), replacing "a session IS the episode" with
real segmentation. See that directory's FINDINGS.md for the numbers.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import asyncpg

from app.services.trace_collector import mark_worker_seen, read_drop_count
from app.services.trace_redaction import redact_event

SCHEMA_VERSION = "1"

#: Above this many JSON-serialized bytes, one tool_input/tool_output field
#: gets written to disk instead of inlined. Ticket 06's raw_payload_ref
#: column (12_trace_ingestion_pipeline.sql) existed for exactly this --
#: "large tool outputs get a pointer, not inlined -- same idiom as
#: episodes.content_ref" -- but had zero references anywhere in Python
#: (confirmed: schema file + the original design doc only) while
#: Chaitanya's dogfooding pilot started producing real hook traces with no
#: limit at all. 32KB is a conservative default for one tool call's I/O --
#: a module constant consulted at call time (None -> lookup), same
#: monkeypatch-retunable discipline as TRIVIAL_MERGE_MAX_EVENTS/
#: OVERSIZE_SUBDIVIDE_EVENTS below.
MAX_INLINE_PAYLOAD_BYTES = 32 * 1024

#: Where overflowing payloads land. Local disk only, gitignored -- these
#: are already-redacted (trace_redaction.py runs at the collector, upstream
#: of every call in this module) but still real transcript content, same
#: posture as .claude/traces/ in .gitignore.
RAW_PAYLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "raw_payloads"


def _parse_timestamp(ts_raw: str | None) -> datetime:
    """
    A4 real fix: datetime.fromisoformat() pre-3.11 rejects a trailing
    'Z' (which JS-origin hook payloads emit natively, e.g.
    '2026-08-19T10:00:00.000Z'). Normalizing 'Z' -> '+00:00' first makes
    this work on any Python version this might run on, not just the one
    in this sandbox.
    """
    if not ts_raw:
        return datetime.now(timezone.utc)
    if ts_raw.endswith("Z"):
        ts_raw = ts_raw[:-1] + "+00:00"
    return datetime.fromisoformat(ts_raw)


def _read_records(file_path: Path) -> tuple[list[dict], list[tuple[int, str, str]]]:
    """
    Returns (good_records, quarantined) where quarantined is a list of
    (line_number, raw_line, error_message) for every line that failed to
    parse. A4 real fix: the old version had no try/except around
    json.loads/fromisoformat at all -- one malformed line (exactly what
    a torn write, or a future format change, produces) raised out of
    this function entirely, so process_collector_file() never processed
    a single record from an otherwise-healthy file. A worker that stalls
    permanently on one bad line is a worse failure mode than skipping
    that one line and quarantining it for inspection.
    """
    if not file_path.exists():
        return [], []
    lines = file_path.read_text().splitlines()
    # A1 fix removed the synthetic header line entirely (drop_count now
    # lives in the sidecar meta.json -- see trace_collector.py's
    # read_drop_count()) -- every line in this file is now a real data
    # record, no special-casing needed.

    good: list[dict] = []
    quarantined: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            # Fail fast on the fields _insert_event/_ensure_trace_header
            # actually require, rather than raising deep inside the
            # per-record loop where a KeyError looks like a different
            # kind of bug.
            _ = record["session_id"], record["sequence"], record["event_type"], record["dedup_key"]
            _parse_timestamp(record.get("event", {}).get("timestamp"))
            good.append(record)
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: any
            # malformed line must be quarantined, not crash the run
            quarantined.append((i, line, repr(exc)))
    return good, quarantined


def _write_quarantine(file_path: Path, quarantined: list[tuple[int, str, str]]) -> None:
    if not quarantined:
        return
    quarantine_path = file_path.with_name(file_path.name + ".quarantine")
    with quarantine_path.open("a") as f:
        for line_no, raw_line, error in quarantined:
            f.write(json.dumps({"line": line_no, "raw": raw_line, "error": error}) + "\n")


def _prepare_payload_columns(
    event: Mapping[str, Any],
    dedup_key: str,
    *,
    max_inline_bytes: Optional[int] = None,
    raw_payload_dir: Optional[Path] = None,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Returns (tool_input_column, tool_output_column, raw_payload_ref) for
    the INSERT below.

    Under the cap (the common case, unchanged from before this fix): each
    field gets the ordinary json.dumps() round-trip, raw_payload_ref stays
    None.

    Over the cap: the schema gives one row exactly one raw_payload_ref
    column, not one per field, so when either or both of tool_input/
    tool_output overflow, their full (already-redacted) values are written
    together to ONE pointer file for this row, keyed by field name --
    named judgment call, since ticket 06's own column comment only ever
    described a single oversized value, not two independent ones on the
    same row. The inline column for an overflowing field gets a small
    {"_overflow": true, "size_bytes": N} marker so a caller reading
    tool_input/tool_output directly sees a real signal instead of
    silently-truncated data with no indication anything was cut.
    """
    max_bytes = MAX_INLINE_PAYLOAD_BYTES if max_inline_bytes is None else max_inline_bytes
    payload_dir = RAW_PAYLOAD_DIR if raw_payload_dir is None else raw_payload_dir

    columns: dict[str, Optional[str]] = {}
    overflow: dict[str, Any] = {}
    for field_name in ("tool_input", "tool_output"):
        value = event.get(field_name)
        if value is None:
            columns[field_name] = None
            continue
        serialized = json.dumps(value)
        size_bytes = len(serialized.encode("utf-8"))
        if size_bytes <= max_bytes:
            columns[field_name] = serialized
        else:
            columns[field_name] = json.dumps({"_overflow": True, "size_bytes": size_bytes})
            overflow[field_name] = value

    raw_payload_ref = None
    if overflow:
        payload_dir.mkdir(parents=True, exist_ok=True)
        file_path = payload_dir / f"{dedup_key}.json"
        file_path.write_text(json.dumps(overflow), encoding="utf-8")
        raw_payload_ref = str(file_path)

    return columns["tool_input"], columns["tool_output"], raw_payload_ref


def read_overflow_payload(raw_payload_ref: str) -> dict:
    """
    Read-back helper: reconstructs the full (still-redacted) field
    value(s) captured for one row whose tool_input/tool_output overflowed
    MAX_INLINE_PAYLOAD_BYTES. Returns the {field_name: original_value}
    dict _prepare_payload_columns() wrote at raw_payload_ref -- callers
    already have that string from their own row read, so this stays a
    small, direct file read rather than a general ref-resolution API.
    """
    return json.loads(Path(raw_payload_ref).read_text(encoding="utf-8"))


async def _ensure_trace_header(conn: asyncpg.Connection, trace_id: str, session_id: str,
                                started_at: datetime, owner_id: str | None = None,
                                visibility: str = "public") -> None:
    await conn.execute(
        "INSERT INTO agent_traces (trace_id, session_id, started_at, schema_version, "
        "owner_id, visibility) "
        "VALUES ($1, $2, $3, $4, $5, $6::visibility_level) ON CONFLICT (trace_id) DO NOTHING",
        trace_id, session_id, started_at, SCHEMA_VERSION, owner_id, visibility,
    )


async def _insert_event(conn: asyncpg.Connection, record: dict, owner_id: str | None = None,
                         visibility: str = "public", *,
                         max_inline_bytes: Optional[int] = None,
                         raw_payload_dir: Optional[Path] = None) -> str | None:
    """Returns the real inserted trace_events.id, or None if this
    dedup_key was already present (a real, confirmed no-op, not assumed).

    A7 real bug fixed: agent_traces/trace_events both carry real
    owner_id/visibility columns (12_trace_ingestion_pipeline.sql), but
    this INSERT never populated either -- every trace row silently
    landed as visibility='public', owner_id=NULL regardless of who
    produced it, the exact tenant_id cautionary case 03_access.sql's own
    docstring warns about. Now real parameters, not decorative columns.
    """
    event = redact_event(record["event"])
    timestamp = _parse_timestamp(event.get("timestamp"))
    tool_input_col, tool_output_col, raw_payload_ref = _prepare_payload_columns(
        event, record["dedup_key"],
        max_inline_bytes=max_inline_bytes, raw_payload_dir=raw_payload_dir,
    )

    return await conn.fetchval(
        """
        INSERT INTO trace_events (
            trace_id, session_id, sequence, event_type, "timestamp",
            actor_id, tool_name, tool_call_id, tool_input, tool_output,
            success, dedup_key, schema_version, owner_id, visibility,
            raw_payload_ref
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::visibility_level,$16)
        ON CONFLICT (dedup_key) DO NOTHING
        RETURNING id
        """,
        record.get("trace_id") or record["session_id"],
        record["session_id"],
        record["sequence"],
        record["event_type"],
        timestamp,
        event.get("actor_id"),
        event.get("tool_name"),
        event.get("tool_call_id"),
        tool_input_col,
        tool_output_col,
        event.get("success"),
        record["dedup_key"],
        SCHEMA_VERSION,
        owner_id,
        visibility,
        raw_payload_ref,
    )


async def process_collector_file(pool: asyncpg.Pool, file_path: Path, *,
                                  owner_id: str | None = None,
                                  visibility: str = "public",
                                  max_inline_bytes: Optional[int] = None,
                                  raw_payload_dir: Optional[Path] = None) -> dict:
    """
    Real, testable entry point. Processes every record currently in the
    collector file: ensures a trace header exists, inserts the event
    (idempotently), and queues a downstream job for each real (not
    duplicate) insert. Returns real counts, not estimates.

    A3 real fix: the old version called pool.acquire() and
    _ensure_trace_header() once PER RECORD -- at the 50k-line default
    that's 50k pool acquisitions and (since almost every event shares
    one session's trace_id) ~50k redundant header upserts for what is
    really one distinct trace per run in the common case. Now one
    connection is acquired for the whole run, and the header is only
    upserted once per distinct trace_id actually seen. Per-event INSERTs
    are still individual round trips (real bulk/executemany batching
    with per-row ON CONFLICT...RETURNING is a further optimization, not
    attempted here -- flagging that honestly rather than claiming this
    is fully batched).
    """
    good_records, quarantined = _read_records(file_path)
    _write_quarantine(file_path, quarantined)

    inserted = 0
    skipped_duplicate = 0
    headers_ensured: set[str] = set()

    async with pool.acquire() as conn:
        for record in good_records:
            trace_id = record.get("trace_id") or record["session_id"]
            session_id = record["session_id"]
            event = record["event"]
            started_at = _parse_timestamp(event.get("timestamp"))

            async with conn.transaction():
                if trace_id not in headers_ensured:
                    await _ensure_trace_header(
                        conn, trace_id, session_id, started_at,
                        owner_id=owner_id, visibility=visibility,
                    )
                    headers_ensured.add(trace_id)
                new_id = await _insert_event(
                    conn, record, owner_id=owner_id, visibility=visibility,
                    max_inline_bytes=max_inline_bytes, raw_payload_dir=raw_payload_dir,
                )
                if new_id is not None:
                    inserted += 1
                    await conn.execute(
                        "INSERT INTO ingestion_jobs (job_type, payload) VALUES ($1, $2)",
                        "normalize_trace_event",
                        json.dumps({"trace_event_id": str(new_id), "dedup_key": record["dedup_key"]}),
                    )
                else:
                    skipped_duplicate += 1

    # A2 real fix: mark every currently-read line as seen, so the
    # collector's compaction (see trace_collector.py's mark_worker_seen())
    # is now allowed to trim up to this many lines -- BEFORE this call,
    # trimming had zero knowledge of worker progress and could discard
    # events the worker had never read. This call is what makes that
    # guarantee real, not just documented.
    mark_worker_seen(file_path, len(good_records))

    # "Smaller, worth knowing" fix from the code review: drop_count was
    # "computed, stored in the file, never read by the worker, no column
    # to land in" -- migration 17 added agent_traces.collector_drop_count
    # for exactly this. Surfaced here, once per run, for every trace
    # this run touched -- a lossy collector period is now visible in the
    # database, not just in a local sidecar file nobody queries.
    drop_count = read_drop_count(file_path)
    if drop_count > 0 and headers_ensured:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE agent_traces SET collector_drop_count = $1 WHERE trace_id = ANY($2::text[])",
                drop_count, list(headers_ensured),
            )

    return {
        "records_seen": len(good_records) + len(quarantined),
        "inserted": inserted,
        "skipped_duplicate": skipped_duplicate,
        "quarantined": len(quarantined),
    }


# ---------------------------------------------------------------------------
# Episode assembly (Band 2 promotion of experiments/episode_assembly).
#
# Every rule below is an empirically validated survivor from FINDINGS.md
# (36 real sessions, 28,969 lines, 69 subagent transcripts) -- none is
# invented here:
#
#   PRIMARY BOUNDARY .... Rule-A genuine human prompts only. The reference
#                         predicate (isMeta/isCompactSummary/isSidechain/
#                         leading tool_result plus the four auto-continuation
#                         prefixes) is adopted verbatim; without it every
#                         background-agent notification opens a spurious
#                         episode.
#   MERGE ............... episodes of <= TRIVIAL_MERGE_MAX_EVENTS events fold
#                         into their SUCCESSOR (a trailing trivial one into
#                         its predecessor). Prompt-only segmentation produced
#                         18% trivial episodes on real data; this is the fix.
#   SUBDIVISION ......... a single prompt can also under-segment (22 prompts
#                         spawned >200 events each), so episodes over
#                         OVERSIZE_SUBDIVIDE_EVENTS are subdivided at their
#                         INTERNAL commit/test completions. Findings measured
#                         commit/test boundaries as ~97% disjoint from prompt
#                         boundaries (Jaccard 0.028): "metadata attached to an
#                         episode, or a sub-boundary within one -- never a
#                         top-level cut." An oversize episode with no
#                         internal commit/test signal stays whole and is
#                         flagged; no arbitrary cuts are invented.
#   SUBAGENTS ........... subagent work lives in sibling files joined by
#                         sourceToolAssistantUUID -> the parent assistant
#                         line's uuid. Each joining file becomes nested child
#                         episodes under whichever top-level episode contains
#                         its spawn line. Nested, never co-equal.
#   IDLE GAPS ........... DROPPED entirely. The prescribed GMM-over-log-gaps
#                         fit was implemented in the prototype and measured:
#                         neither raw inter-event nor prompt-to-prompt gaps
#                         are bimodal (fitted valleys 0.8s / 2.3min are EM
#                         splitting one skewed long tail, three orders of
#                         magnitude below the ~1h anticipated). There is NO
#                         temporal threshold anywhere below; timestamps are
#                         used only to report start_ts/end_ts.
#
# Schema realities baked in (FINDINGS "Schema facts"): 16 distinct line types
# across 11 CLI versions with INTRA-file drift -- unknown types are tolerated
# as plain non-boundary events; 9 of the 16 carry no timestamp -- missing
# timestamps are skipped for range reporting, never fatal; sessions are
# FORESTS (compaction/resume create extra parentUuid:null roots) -- so this
# deliberately does NOT walk chains: file order across all roots is kept,
# which is exactly what a single-chain walk silently truncates.
#
# assemble_episodes()/load_transcript() are pure and offline-testable;
# write_session_episodes()/process_transcript_session() are the only IO.
# ---------------------------------------------------------------------------

#: an episode this small is a trivial prompt ("ok", "continue") and folds
#: into its successor. FINDINGS: 147 of 823 Rule-A episodes (18%) were this
#: small. A module constant consulted at call time -- provably retunable by
#: monkeypatch, like every threshold in this repo, never an inlined literal.
TRIVIAL_MERGE_MAX_EVENTS = 2

#: episodes larger than this many events get subdivided at internal
#: commit/test completions. FINDINGS: 22 prompts spawned >200 events each.
OVERSIZE_SUBDIVIDE_EVENTS = 200

#: Auto-continuations arriving as `type:"user"` lines that are NOT new human
#: prompts (reference predicate via segment.py). FINDINGS: these background
#: notifications are pervasive in real sessions.
NON_PROMPT_PREFIXES = (
    "<task-notification",
    "<scheduled-wakeup",
    "<background-task",
    "[Request interrupted",
)

_COMMIT_COMMAND_RE = re.compile(r"\bgit\s+commit\b")
_TEST_COMMAND_RE = re.compile(
    r"\b(pytest|npm\s+(run\s+)?test|yarn\s+test|go\s+test|cargo\s+test|"
    r"ansible-test|jest|vitest|tox|unittest)\b"
)
_AGENT_TOOL_NAME = "Agent"


def parse_transcript_timestamp(raw: Any) -> Optional[datetime]:
    """Tolerant ISO-8601 -> aware datetime, else None.

    Unlike _parse_timestamp() above (the collector-record path, which may
    fall back to now()), transcript lines legitimately lack timestamps --
    9 of 16 observed line types carry none -- so "absent" stays a
    representable answer rather than silently becoming the wall clock.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _is_human_prompt(rec: Mapping[str, Any]) -> bool:
    """Genuine human prompt, per the validated reference predicate."""
    if rec.get("type") != "user":
        return False
    if rec.get("isMeta") or rec.get("isCompactSummary") or rec.get("isSidechain"):
        return False
    content = (rec.get("message") or {}).get("content")
    text: Optional[str] = None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, Mapping):
            if first.get("type") == "tool_result":
                return False
            if first.get("type") == "text":
                text = first.get("text") or ""
    if text is None:
        return False
    return not text.startswith(NON_PROMPT_PREFIXES)


def _line_commands(rec: Mapping[str, Any]) -> Sequence[str]:
    """Command strings from Bash/PowerShell tool_use blocks. Used only for
    regex classification; never persisted or logged."""
    if rec.get("type") != "assistant":
        return ()
    content = (rec.get("message") or {}).get("content")
    if not isinstance(content, list):
        return ()
    out = []
    for block in content:
        if (
            isinstance(block, Mapping)
            and block.get("type") == "tool_use"
            and block.get("name") in ("Bash", "PowerShell")
        ):
            cmd = (block.get("input") or {}).get("command")
            if isinstance(cmd, str):
                out.append(cmd)
    return out


@dataclass
class _Line:
    """Structural view of one transcript line -- the only things the
    segmentation rules may look at."""

    ts: Optional[datetime]
    is_prompt: bool
    is_commit: bool
    is_test: bool
    spawns_agent: bool
    uuid: Optional[str]


def _classify(rec: Mapping[str, Any]) -> _Line:
    commands = _line_commands(rec)
    content = (rec.get("message") or {}).get("content")
    spawns = isinstance(content, list) and any(
        isinstance(b, Mapping)
        and b.get("type") == "tool_use"
        and b.get("name") == _AGENT_TOOL_NAME
        for b in content
    )
    uuid_raw = rec.get("uuid")
    return _Line(
        ts=parse_transcript_timestamp(rec.get("timestamp")),
        is_prompt=_is_human_prompt(rec),
        is_commit=any(_COMMIT_COMMAND_RE.search(c) for c in commands),
        is_test=any(_TEST_COMMAND_RE.search(c) for c in commands),
        spawns_agent=bool(spawns),
        uuid=uuid_raw if isinstance(uuid_raw, str) else None,
    )


@dataclass
class Episode:
    """One assembled episode. Main-session episodes index into the main line
    list; children (nested subagent transcripts) index into their own source
    file's line list, named in `source`. Half-open [start, end) spans."""

    start: int
    end: int
    start_ts: Optional[datetime] = None
    end_ts: Optional[datetime] = None
    flags: frozenset = frozenset()
    source: str = "main"
    spawned_by: Optional[str] = None
    children: list = field(default_factory=list)

    @property
    def n_events(self) -> int:
        return self.end - self.start

    def fingerprint(self, session_id: str) -> str:
        """Deterministic digest of this episode's SHAPE -- session, source,
        boundaries, spawn join -- never message content. Rides in
        episodes.metadata JSONB and matched on replay: this is what makes
        write_session_episodes() idempotent without a schema change."""
        payload = json.dumps(
            {
                "session_id": session_id,
                "source": self.source,
                "start": self.start,
                "end": self.end,
                "spawned_by": self.spawned_by,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class EpisodeAssembly:
    """Segmentation result for one session, with honest counters for
    everything the rules did -- including what they could NOT do
    (unjoined subagent lines are counted, never silently dropped)."""

    episodes: list
    main_lines: int
    unparsed_main_lines: int
    subagent_files_seen: int
    subagent_files_joined: int
    unjoined_subagent_lines: int
    trivial_folds: int
    subdivided_episodes: int


def _spans(cuts: Sequence[int], n: int) -> list:
    """Half-open spans from boundary indices. Index 0 always opens the
    first span; a cut opens a new one."""
    starts = sorted(set(cuts) | {0})
    return [
        (s, starts[i + 1] if i + 1 < len(starts) else n)
        for i, s in enumerate(starts)
    ]


def assemble_episodes(
    main_lines: Sequence,
    subagent_files: Optional[Mapping] = None,
    *,
    trivial_merge_max_events: Optional[int] = None,
    oversize_subdivide_events: Optional[int] = None,
) -> EpisodeAssembly:
    """Segment one parsed session transcript into episodes using only the
    empirically validated rules (see the section banner above).

    Thresholds default to the module constants AT CALL TIME (None ->
    lookup), so retunability is provable by monkeypatch rather than trusted
    to an inlined literal.
    """
    trivial_max = (
        TRIVIAL_MERGE_MAX_EVENTS
        if trivial_merge_max_events is None
        else trivial_merge_max_events
    )
    oversize_at = (
        OVERSIZE_SUBDIVIDE_EVENTS
        if oversize_subdivide_events is None
        else oversize_subdivide_events
    )

    classified: list = []
    unparsed = 0
    for rec in main_lines:
        if isinstance(rec, Mapping):
            classified.append(_classify(rec))
        else:
            # A JSONL line that parsed to a non-object (or a caller passing
            # raw junk): counted honestly, treated as a plain event so span
            # arithmetic still covers every line of the file.
            unparsed += 1
            classified.append(_classify({}))
    n = len(classified)
    if n == 0:
        return EpisodeAssembly([], 0, unparsed, 0, 0, 0, 0, 0)

    prompt_cuts = [i for i, ln in enumerate(classified) if ln.is_prompt]

    # -- MERGE pass. One left-to-right sweep, equivalent to "repeat: fold any
    # <=trivial_max-events episode into its successor until stable", with a
    # trailing trivial folded backward when it has no successor. O(n), no
    # fixpoint loop needed: absorbing forward composes transitively.
    folds = 0
    pieces: list = []  # (start, end, absorbed_a_trivial)
    all_spans = _spans(prompt_cuts, n)
    run_s, run_e = all_spans[0]
    run_absorbed = False
    for s, e in all_spans[1:]:
        if run_e - run_s <= trivial_max:
            run_e = e
            run_absorbed = True
            folds += 1
        else:
            pieces.append((run_s, run_e, run_absorbed))
            run_s, run_e, run_absorbed = s, e, False
    if run_e - run_s <= trivial_max and pieces:
        ps, _, p_absorbed = pieces[-1]
        pieces[-1] = (ps, run_e, p_absorbed)
        folds += 1
    else:
        pieces.append((run_s, run_e, run_absorbed))

    # -- SUBDIVISION pass: oversize episodes split at internal commit/test
    # completions only; the completing event closes its sub-episode.
    subdivided_count = 0
    episodes: list = []

    def build(s: int, e: int, flags: set) -> Episode:
        tss = [ln.ts for ln in classified[s:e] if ln.ts is not None]
        return Episode(
            s, e,
            min(tss) if tss else None,
            max(tss) if tss else None,
            frozenset(flags),
        )

    base_flags = set()
    if not prompt_cuts:
        base_flags.add("zero_prompts")

    for s, e, absorbed in pieces:
        if e - s <= oversize_at:
            flags = set(base_flags)
            if absorbed:
                flags.add("folded_trivial")
            episodes.append(build(s, e, flags))
            continue
        internal = [
            j for j in range(s + 1, e)
            if classified[j].is_commit or classified[j].is_test
        ]
        if not internal:
            episodes.append(build(s, e, base_flags | {"oversize_unsubdivided"}))
            continue
        subdivided_count += 1
        bounds = [s] + [j + 1 for j in internal if j + 1 < e] + [e]
        for k in range(len(bounds) - 1):
            ps, pe = bounds[k], bounds[k + 1]
            if pe - ps > oversize_at:
                episodes.append(
                    build(ps, pe, base_flags | {"subdivided", "oversize_unsubdivided"})
                )
            else:
                episodes.append(build(ps, pe, base_flags | {"subdivided"}))

    # -- SUBAGENT joins: sibling-file lines attach via
    # sourceToolAssistantUUID -> this session's assistant-line uuid, as
    # nested children of whichever top-level episode holds the spawn line.
    uuid_to_idx: dict = {}
    for i, ln in enumerate(classified):
        if ln.uuid and ln.uuid not in uuid_to_idx:
            uuid_to_idx[ln.uuid] = i

    def locate(idx: int) -> Optional[Episode]:
        for ep in episodes:
            if ep.start <= idx < ep.end:
                return ep
        return None

    files_seen = files_joined = unjoined = 0
    for fname in sorted(subagent_files or {}):
        lines = list(subagent_files[fname])
        files_seen += 1
        groups: dict = {}
        for i, rec in enumerate(lines):
            key = rec.get("sourceToolAssistantUUID") if isinstance(rec, Mapping) else None
            if not isinstance(key, str):
                continue
            if key in uuid_to_idx:
                groups.setdefault(key, []).append(i)
            else:
                unjoined += 1
        if not groups:
            continue
        files_joined += 1
        for key, idxs in sorted(groups.items()):
            parent = locate(uuid_to_idx[key])
            if parent is None:  # unreachable while episodes partition [0,n);
                # defensive only -- never lose the count either way
                unjoined += len(idxs)
                continue
            tss = []
            for i in idxs:
                rec = lines[i]
                if isinstance(rec, Mapping):
                    ts = parse_transcript_timestamp(rec.get("timestamp"))
                    if ts is not None:
                        tss.append(ts)
            # Real agent-<hex>.jsonl files are single-spawn (one uuid per
            # file), so min..max+1 covers exactly the group's lines; a
            # hypothetical multi-spawn file would still join correctly per
            # group, just with a coarser span.
            parent.children.append(
                Episode(
                    min(idxs), max(idxs) + 1,
                    min(tss) if tss else None,
                    max(tss) if tss else None,
                    frozenset({"subagent"}),
                    source=fname,
                    spawned_by=key,
                )
            )

    return EpisodeAssembly(
        episodes=episodes,
        main_lines=n,
        unparsed_main_lines=unparsed,
        subagent_files_seen=files_seen,
        subagent_files_joined=files_joined,
        unjoined_subagent_lines=unjoined,
        trivial_folds=folds,
        subdivided_episodes=subdivided_count,
    )


def load_transcript(path: Path) -> tuple:
    """Read one Claude Code session JSONL: returns (records, bad_line_count).
    Malformed lines (torn writes, future format drift) are skipped and
    counted, mirroring _read_records()' quarantine posture: one bad line
    must never stall a healthy file."""
    records: list = []
    bad = 0
    if not path.exists():
        return records, bad
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            records.append(json.loads(stripped))
        except (json.JSONDecodeError, ValueError):
            bad += 1
    return records, bad


def _episode_metadata(ep: Episode, session_id: str, fingerprint: str) -> dict:
    return {
        "assembly_fingerprint": fingerprint,
        "segmenter": "trace_worker/episode_assembly.v1",
        "rules": {
            "primary_boundary": "rule_a_genuine_prompts",
            "idle_gap_signal": "dropped_not_tuned",
            "trivial_merge_max_events": TRIVIAL_MERGE_MAX_EVENTS,
            "oversize_subdivide_events": OVERSIZE_SUBDIVIDE_EVENTS,
        },
        "flags": sorted(ep.flags),
        "n_events": ep.n_events,
        "source": ep.source,
        "spawned_by": ep.spawned_by,
    }


async def write_session_episodes(
    pool: asyncpg.Pool,
    *,
    session_id: str,
    assembly: EpisodeAssembly,
    owner_id: Optional[str] = None,
    visibility: str = "public",
    project_id: Optional[str] = None,
    content_ref_prefix: str = "transcript",
) -> dict:
    """Persist assembled episodes into the EXISTING episodes table
    (01_ontology.sql + migration 17's columns) -- no new migration, by lane
    rule.

    Idempotency without a unique constraint: each episode carries a
    deterministic shape fingerprint in metadata; rows whose fingerprint is
    already present for this session_id are skipped. Check-then-insert is
    race-free enough under this worker's single-writer design (same
    assumption process_collector_file()'s header upserts already rely on);
    a DB-level constraint would need CORE-A and is flagged honestly rather
    than pretended away.

    content_ref is a LOCATOR ("file#start:end"), never message content --
    same privacy posture as the redaction module and the prototype.
    """
    now = datetime.now(timezone.utc)
    flat: list = []
    for ep in assembly.episodes:
        flat.append((ep, None))
        for child in ep.children:
            flat.append((child, ep))

    async with pool.acquire() as conn:
        existing = {
            row["fp"]: row["id"]
            for row in await conn.fetch(
                "SELECT id, metadata->>'assembly_fingerprint' AS fp FROM episodes "
                "WHERE session_id = $1",
                session_id,
            )
        }
        parents_inserted = children_inserted = skipped = 0
        row_ids: dict = {}
        for ep, parent in flat:
            fingerprint = ep.fingerprint(session_id)
            if fingerprint in existing:
                # Already persisted by a previous run. Keep its real row id:
                # a child arriving after a crash between its parent's insert
                # and its own must still link to the EXISTING parent row.
                skipped += 1
                row_ids[id(ep)] = existing[fingerprint]
                continue
            row_id = await conn.fetchval(
                """
                INSERT INTO episodes (
                    episode_type, content_ref, timestamp, metadata,
                    session_id, project_id, start_ts, end_ts,
                    owner_id, visibility, parent_episode_id
                ) VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9,
                          $10::visibility_level, $11)
                RETURNING id
                """,
                "trace",
                f"{content_ref_prefix}#{ep.source}:{ep.start}:{ep.end}",
                ep.start_ts or now,
                json.dumps(_episode_metadata(ep, session_id, fingerprint)),
                session_id,
                project_id,
                ep.start_ts,
                ep.end_ts,
                owner_id,
                visibility,
                row_ids.get(id(parent)) if parent is not None else None,
            )
            row_ids[id(ep)] = row_id
            if parent is None:
                parents_inserted += 1
            else:
                children_inserted += 1

    return {
        "parents_inserted": parents_inserted,
        "children_inserted": children_inserted,
        "skipped_existing": skipped,
    }


def discover_subagent_files(session_file: Path) -> dict:
    """Sibling subagent transcripts for one session file, per FINDINGS'
    documented `<session>/subagents/agent-<hex>.jsonl` layout. Both observed
    placements are probed (subagents/ beside the file, or under a directory
    named for the file's stem); the join in assemble_episodes() is strict --
    only lines whose sourceToolAssistantUUID resolves INSIDE this session
    attach -- so over-globbing cannot misattribute another session's
    subagents."""
    files: dict = {}
    for cand in (
        session_file.parent / "subagents",
        session_file.parent / session_file.stem / "subagents",
    ):
        if cand.is_dir():
            for f in sorted(cand.glob("*.jsonl")):
                files[f.name] = load_transcript(f)[0]
    return files


async def process_transcript_session(
    pool: asyncpg.Pool,
    session_file: Path,
    *,
    owner_id: Optional[str] = None,
    visibility: str = "public",
    project_id: Optional[str] = None,
) -> dict:
    """Full production path for one raw session transcript file: load main
    JSONL + sibling subagent files, assemble episodes with the validated
    rules, persist idempotently. Replayable end to end -- rerunning against
    the same files inserts nothing new (fingerprints match), which is the
    same replay contract process_collector_file() offers upstream."""
    main_lines, bad = load_transcript(session_file)
    subagent_files = discover_subagent_files(session_file)
    assembly = assemble_episodes(main_lines, subagent_files)
    write_stats = await write_session_episodes(
        pool,
        session_id=session_file.stem,
        assembly=assembly,
        owner_id=owner_id,
        visibility=visibility,
        project_id=project_id,
        content_ref_prefix=session_file.name,
    )
    return {
        "episodes": len(assembly.episodes),
        "child_episodes": sum(len(e.children) for e in assembly.episodes),
        "main_lines": assembly.main_lines,
        "bad_lines": bad + assembly.unparsed_main_lines,
        "subagent_files_seen": assembly.subagent_files_seen,
        "subagent_files_joined": assembly.subagent_files_joined,
        "unjoined_subagent_lines": assembly.unjoined_subagent_lines,
        **write_stats,
    }
