"""
MCP hardening B36: multi-agent coordination expressed on the EXISTING
execution graph (`execution_run_nodes`, migration 56's new columns) --
never a second coordination/lock table.

File declarations are advisory coordination leases, not OS filesystem
locks (B36's own words): nothing here prevents a process from actually
writing a file another node declared. This module's job is narrower and
real: before a node starts substantial work, detect whether ANOTHER
still-live declaration (any run, any owner) already claims an
overlapping write scope, and return a typed, exact conflict rather than
silently allowing two nodes to claim the same files.

B36's own literal "before assigning/starting a node, detect" list has
FIVE items; this module now covers all five for real:

  1. exact write/write overlap           -- `_paths_overlap`, exact set
  2. write/read overlap when ordering
     matters                              -- `_ordering_matters` +
                                            `_paths_overlap` against the
                                            OTHER declaration's read scope
                                            (and vice versa)
  3. overlapping write globs              -- `_paths_overlap`'s glob arm
  4. dependency violations                -- `_unmet_dependencies`
  5. expired/stale leases                 -- `find_stale_leases`

HONEST SCOPE, still real limitations, not glossed over:
  - Glob-vs-glob overlap detection is a conservative, deliberately
    OVER-inclusive approximation (shared literal prefix up to the first
    wildcard character) -- true glob-pattern intersection is a much
    harder problem this pass does not attempt to solve exactly. Over-
    flagging a false conflict is the safe direction to be wrong in for a
    coordination gate; silently missing a real one is not.
  - `symbols_expected_to_modify` is now used for conflict detection, but
    only as an EXACT set-membership overlap -- "two live declarations
    both name the literal string `User.save`" -- never resolved against
    real source (no symbol-level static analysis/AST resolution exists
    in this codebase, and inventing one would be exactly the fabricated-
    signal pattern B38 forbids). This is the same honesty level
    `_paths_overlap`'s own exact-path arm already uses for files -- a
    real, narrow, literal-string check, not semantic understanding.
  - "write/read overlap when ordering matters": `_ordering_matters`
    treats two declarations in DIFFERENT execution runs as always
    unordered (a `deps` edge can only exist within one run's own
    compiled `task_graphs.nodes`, so cross-run nodes have no possible
    ordering relationship at all); within the SAME run, it walks the
    real `deps` graph (the same source `_unmet_dependencies` already
    reads) to check whether one node transitively depends on the other
    -- if so, durable_run's own execution-order enforcement already
    serializes them safely and this is not flagged as a hazard.
  - "expired/stale leases": `find_stale_leases` is a real, separate read
    -- a non-terminal node whose `file_intent_lease_expires_at` has
    already passed is a real operational signal (an agent that crashed
    or forgot to release), surfaced to `declare_file_intent`'s own
    caller as `stale_leases_observed` (informational, per B36's own
    "advisory... not OS filesystem locks" framing -- someone else's
    abandoned lease is not itself a conflict with THIS declaration
    unless it also overlaps, which the write/write and write/read checks
    above already catch for real).
  - Claims (`knowledge_nodes` where `node_type='claim'`) are NEVER
    touched by this module -- B36's own rule: "Claims remain separate
    from coordination."
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg

DEFAULT_FILE_INTENT_LEASE_SECONDS = 3600


class DependencyViolation(Exception):
    """Raised when a node declares intent to start substantial work while
    a node it depends on (per the compiled plan's own `deps`) has not
    yet succeeded. Detection only -- durable_run's own `_ready`/
    `_blocked` gating is the REAL enforcement that a node cannot execute
    out of order; this is an early, honest warning at declaration time,
    not a second scheduler re-implementing that rule."""


class FileIntentConflict(Exception):
    """Raised when a NEW declaration's write scope overlaps a still-live
    declaration from a DIFFERENT (run, node). Carries the exact
    conflicting run/node/owner/files so a caller can report a typed
    conflict, never a generic refusal."""

    def __init__(self, conflicts: list["ConflictEntry"]):
        self.conflicts = conflicts
        super().__init__(
            f"{len(conflicts)} conflicting file-intent declaration(s): "
            + "; ".join(
                f"run={c.execution_run_id} node={c.node_order} owner={c.owner_agent_id!r} "
                f"kind={c.kind} files={c.overlapping_files} symbols={c.overlapping_symbols}"
                for c in conflicts
            )
        )


@dataclass
class ConflictEntry:
    execution_run_id: str
    node_order: int
    owner_agent_id: Optional[str]
    overlapping_files: list[str] = field(default_factory=list)
    overlapping_symbols: list[str] = field(default_factory=list)
    kind: str = "write_write"


@dataclass
class StaleLeaseEntry:
    execution_run_id: str
    node_order: int
    owner_agent_id: Optional[str]
    lease_expired_at: datetime


def _glob_prefix(pattern: str) -> str:
    """Literal prefix of a glob pattern up to its first wildcard char."""
    for i, ch in enumerate(pattern):
        if ch in "*?[":
            return pattern[:i]
    return pattern


def _paths_overlap(a_exact: list[str], a_globs: list[str], b_exact: list[str], b_globs: list[str]) -> list[str]:
    """Every path/pattern from side A that overlaps something on side B,
    by the honest rules this module's own docstring documents."""
    overlaps: set[str] = set()

    a_exact_set, b_exact_set = set(a_exact), set(b_exact)
    overlaps |= a_exact_set & b_exact_set

    for path in a_exact:
        if any(fnmatch.fnmatch(path, g) for g in b_globs):
            overlaps.add(path)
    for path in b_exact:
        if any(fnmatch.fnmatch(path, g) for g in a_globs):
            overlaps.add(path)

    for ga in a_globs:
        pa = _glob_prefix(ga)
        for gb in b_globs:
            pb = _glob_prefix(gb)
            if ga == gb or pa.startswith(pb) or pb.startswith(pa):
                overlaps.add(f"{ga} ~ {gb}")

    return sorted(overlaps)


async def _load_plan_nodes(pool: asyncpg.Pool, execution_run_id: str) -> list[dict]:
    """The compiled plan's own `task_graphs.nodes` JSON for this run --
    the single real source of `deps` this module (and durable_run/
    get_run_context) already reads. `[]` for "no such run/graph",
    never raises -- callers treat that as "nothing known", not an
    error."""
    run_row = await pool.fetchrow(
        "SELECT task_graph_id FROM execution_runs WHERE id = $1::uuid", execution_run_id,
    )
    if run_row is None:
        return []
    graph_row = await pool.fetchrow("SELECT nodes FROM task_graphs WHERE id = $1", run_row["task_graph_id"])
    if graph_row is None:
        return []
    import json as _json
    raw = graph_row["nodes"]
    return _json.loads(raw) if isinstance(raw, str) else (raw or [])


async def _unmet_dependencies(pool: asyncpg.Pool, *, execution_run_id: str, node_order: int) -> list[int]:
    """This node's `deps` (from the compiled plan's task_graphs.nodes
    JSON -- the SAME structure durable_run/get_run_context already
    read) whose own execution_run_nodes row is not yet 'succeeded'."""
    plan_nodes = await _load_plan_nodes(pool, execution_run_id)
    deps = next((gn.get("deps") or [] for gn in plan_nodes if gn.get("order") == node_order), [])
    if not deps:
        return []
    rows = await pool.fetch(
        "SELECT node_order, status FROM execution_run_nodes "
        "WHERE execution_run_id = $1::uuid AND node_order = ANY($2::int[])",
        execution_run_id, deps,
    )
    succeeded = {r["node_order"] for r in rows if r["status"] == "succeeded"}
    return sorted(set(deps) - succeeded)


def _is_transitively_ordered(plan_nodes: list[dict], a_order: int, b_order: int) -> bool:
    """Does `a_order` transitively depend on `b_order`, or vice versa,
    per this SAME run's own compiled `deps` graph? A real BFS over the
    exact structure `_unmet_dependencies` already reads -- when true,
    durable_run's own `_ready`/`_blocked` execution-order enforcement
    already guarantees these two nodes never run concurrently, so a
    write/read overlap between them is not a real coordination hazard."""
    deps_by_order = {gn.get("order"): (gn.get("deps") or []) for gn in plan_nodes}

    def _reaches(start: int, target: int) -> bool:
        seen: set[int] = set()
        stack = list(deps_by_order.get(start, []))
        while stack:
            n = stack.pop()
            if n == target:
                return True
            if n in seen:
                continue
            seen.add(n)
            stack.extend(deps_by_order.get(n, []))
        return False

    return _reaches(a_order, b_order) or _reaches(b_order, a_order)


async def _ordering_matters(
    pool: asyncpg.Pool, *,
    a_execution_run_id: str, a_node_order: int,
    b_execution_run_id: str, b_node_order: int,
) -> bool:
    """B36's own phrase, made literal: is there NO real execution-order
    guarantee between these two declarations, such that a write/read
    overlap between them is a genuine hazard? A `deps` edge can only
    ever exist WITHIN one run's own compiled task graph -- two
    declarations from DIFFERENT execution runs have no possible ordering
    relationship at all, so ordering always "matters" across runs.
    Within the SAME run, real transitive dependency (`_is_transitively_
    ordered`) means ordering is already guaranteed -- not a hazard."""
    if str(a_execution_run_id) != str(b_execution_run_id):
        return True
    plan_nodes = await _load_plan_nodes(pool, a_execution_run_id)
    return not _is_transitively_ordered(plan_nodes, a_node_order, b_node_order)


async def check_file_intent_conflicts(
    pool: asyncpg.Pool, *, write_exact: list[str], write_globs: list[str],
    read_exact: Optional[list[str]] = None, read_globs: Optional[list[str]] = None,
    symbols_expected_to_modify: Optional[list[str]] = None,
    exclude_execution_run_id: Optional[str] = None, exclude_node_order: Optional[int] = None,
) -> list[ConflictEntry]:
    """
    Pure read: every OTHER live (non-expired-lease, non-terminal-status)
    declaration that conflicts with this one, by B36's own 5-item
    "detect" list (minus dependency violations and stale leases, each
    checked separately -- `_unmet_dependencies`/`find_stale_leases`).
    Never writes anything -- `declare_file_intent` is the only writer,
    and calls this first.

    Three real conflict KINDS, each returned as its own `ConflictEntry`
    (never merged into one ambiguous entry):
      - `write_write`: this declaration's write scope overlaps another
        live declaration's write scope (exact or glob) -- always a
        conflict, regardless of ordering.
      - `write_read`: this declaration's write scope overlaps another's
        READ scope, or vice versa -- only when `_ordering_matters`
        (B36's own phrase) says nothing already guarantees these two
        nodes run in a fixed order relative to each other.
      - `symbol`: this declaration's `symbols_expected_to_modify` shares
        a literal entry with another live declaration's -- exact
        string-set overlap, independent of any file overlap (two nodes
        editing DIFFERENT files but the SAME named symbol, e.g. via a
        shared generated stub, is a real hazard files alone would miss).
    """
    read_exact = read_exact or []
    read_globs = read_globs or []
    symbols_expected_to_modify = symbols_expected_to_modify or []
    if not write_exact and not write_globs and not read_exact and not read_globs and not symbols_expected_to_modify:
        return []
    rows = await pool.fetch(
        """
        SELECT execution_run_id, node_order, owner_agent_id,
               write_exact, write_globs, read_exact, read_globs, symbols_expected_to_modify
        FROM execution_run_nodes
        WHERE file_intent_lease_expires_at IS NOT NULL
          AND file_intent_lease_expires_at > now()
          AND status NOT IN ('succeeded', 'cancelled')
          AND (write_exact != '[]' OR write_globs != '[]'
               OR read_exact != '[]' OR read_globs != '[]'
               OR symbols_expected_to_modify != '[]')
        """
    )
    conflicts: list[ConflictEntry] = []
    for row in rows:
        if (
            exclude_execution_run_id is not None
            and str(row["execution_run_id"]) == str(exclude_execution_run_id)
            and exclude_node_order is not None
            and row["node_order"] == exclude_node_order
        ):
            continue

        write_write = _paths_overlap(write_exact, write_globs, row["write_exact"], row["write_globs"])
        if write_write:
            conflicts.append(ConflictEntry(
                execution_run_id=str(row["execution_run_id"]), node_order=row["node_order"],
                owner_agent_id=row["owner_agent_id"], overlapping_files=write_write, kind="write_write",
            ))

        symbol_overlap = sorted(set(symbols_expected_to_modify) & set(row["symbols_expected_to_modify"] or []))
        if symbol_overlap:
            conflicts.append(ConflictEntry(
                execution_run_id=str(row["execution_run_id"]), node_order=row["node_order"],
                owner_agent_id=row["owner_agent_id"], overlapping_symbols=symbol_overlap, kind="symbol",
            ))

        write_read = _paths_overlap(write_exact, write_globs, row["read_exact"], row["read_globs"])
        read_write = _paths_overlap(read_exact, read_globs, row["write_exact"], row["write_globs"])
        write_read_overlap = sorted(set(write_read) | set(read_write))
        if write_read_overlap and exclude_execution_run_id is not None:
            ordering_matters = await _ordering_matters(
                pool,
                a_execution_run_id=exclude_execution_run_id, a_node_order=exclude_node_order,
                b_execution_run_id=str(row["execution_run_id"]), b_node_order=row["node_order"],
            )
            if ordering_matters:
                conflicts.append(ConflictEntry(
                    execution_run_id=str(row["execution_run_id"]), node_order=row["node_order"],
                    owner_agent_id=row["owner_agent_id"], overlapping_files=write_read_overlap,
                    kind="write_read",
                ))
    return conflicts


async def find_stale_leases(
    pool: asyncpg.Pool, *,
    exclude_execution_run_id: Optional[str] = None, exclude_node_order: Optional[int] = None,
) -> list[StaleLeaseEntry]:
    """B36's 5th "detect" item, made real: every node that declared a
    file intent, whose lease has already EXPIRED, but whose own run
    status is still non-terminal -- the real signature of an agent that
    crashed or simply forgot to release (`release_file_intent`) before
    its lease ran out. Informational (this module's own "advisory, not
    an OS lock" posture) -- surfaced to a caller of `declare_file_intent`
    alongside a clean declaration, never itself blocking one, since an
    abandoned lease that does NOT overlap the new declaration's own
    scope is not, by itself, a conflict with it."""
    rows = await pool.fetch(
        """
        SELECT execution_run_id, node_order, owner_agent_id, file_intent_lease_expires_at
        FROM execution_run_nodes
        WHERE file_intent_lease_expires_at IS NOT NULL
          AND file_intent_lease_expires_at <= now()
          AND status NOT IN ('succeeded', 'failed', 'cancelled')
          AND (write_exact != '[]' OR write_globs != '[]'
               OR read_exact != '[]' OR read_globs != '[]')
        """
    )
    stale: list[StaleLeaseEntry] = []
    for row in rows:
        if (
            exclude_execution_run_id is not None
            and str(row["execution_run_id"]) == str(exclude_execution_run_id)
            and exclude_node_order is not None
            and row["node_order"] == exclude_node_order
        ):
            continue
        stale.append(StaleLeaseEntry(
            execution_run_id=str(row["execution_run_id"]), node_order=row["node_order"],
            owner_agent_id=row["owner_agent_id"], lease_expired_at=row["file_intent_lease_expires_at"],
        ))
    return stale


async def declare_file_intent(
    pool: asyncpg.Pool, *, execution_run_id: str, node_order: int, owner_agent_id: str,
    read_exact: Optional[list[str]] = None, read_globs: Optional[list[str]] = None,
    write_exact: Optional[list[str]] = None, write_globs: Optional[list[str]] = None,
    symbols_expected_to_modify: Optional[list[str]] = None,
    lease_seconds: int = DEFAULT_FILE_INTENT_LEASE_SECONDS,
) -> dict:
    """
    B36: "Before assigning/starting a node, detect [conflicts]." Checks
    FIRST, writes only if clean -- never declares, then discovers a
    conflict after the fact. Raises `FileIntentConflict` (never silently
    overwrites another node's live claim) if the requested write scope
    overlaps a still-live declaration belonging to a DIFFERENT
    (execution_run_id, node_order).

    Re-declaring the SAME (execution_run_id, node_order)'s own intent is
    always allowed (excluded from its own conflict check) -- a node
    updating/renewing its own declaration is not a conflict with itself.
    """
    unmet = await _unmet_dependencies(pool, execution_run_id=execution_run_id, node_order=node_order)
    if unmet:
        raise DependencyViolation(
            f"node {node_order} of run {execution_run_id} depends on node(s) {unmet} "
            "which have not yet succeeded"
        )

    write_exact = write_exact or []
    write_globs = write_globs or []
    read_exact = read_exact or []
    read_globs = read_globs or []
    symbols_expected_to_modify = symbols_expected_to_modify or []
    conflicts = await check_file_intent_conflicts(
        pool, write_exact=write_exact, write_globs=write_globs,
        read_exact=read_exact, read_globs=read_globs,
        symbols_expected_to_modify=symbols_expected_to_modify,
        exclude_execution_run_id=execution_run_id, exclude_node_order=node_order,
    )
    if conflicts:
        raise FileIntentConflict(conflicts)

    row = await pool.fetchrow(
        """
        UPDATE execution_run_nodes SET
            owner_agent_id = $3, read_exact = $4::jsonb, read_globs = $5::jsonb,
            write_exact = $6::jsonb, write_globs = $7::jsonb,
            symbols_expected_to_modify = $8::jsonb,
            file_intent_lease_expires_at = now() + make_interval(secs => $9)
        WHERE execution_run_id = $1::uuid AND node_order = $2
        RETURNING *
        """,
        execution_run_id, node_order, owner_agent_id,
        read_exact, read_globs, write_exact, write_globs,
        symbols_expected_to_modify, lease_seconds,
    )
    if row is None:
        raise ValueError(f"no node at order {node_order} for execution_run_id {execution_run_id}")

    stale_leases = await find_stale_leases(
        pool, exclude_execution_run_id=execution_run_id, exclude_node_order=node_order,
    )
    result = dict(row)
    result["stale_leases_observed"] = [
        {
            "execution_run_id": s.execution_run_id, "node_order": s.node_order,
            "owner_agent_id": s.owner_agent_id, "lease_expired_at": s.lease_expired_at,
        }
        for s in stale_leases
    ]
    return result


async def release_file_intent(pool: asyncpg.Pool, *, execution_run_id: str, node_order: int) -> None:
    """Explicit early release -- clears the lease so this node's
    declaration immediately stops participating in conflict checks
    (rather than waiting out its own expiry)."""
    await pool.execute(
        "UPDATE execution_run_nodes SET file_intent_lease_expires_at = NULL "
        "WHERE execution_run_id = $1::uuid AND node_order = $2",
        execution_run_id, node_order,
    )
