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

HONEST SCOPE:
  - Exact-path and exact-vs-glob overlap detection is real (`fnmatch`).
  - Glob-vs-glob overlap detection is a conservative, deliberately
    OVER-inclusive approximation (shared literal prefix up to the first
    wildcard character) -- true glob-pattern intersection is a much
    harder problem this pass does not attempt to solve exactly. Over-
    flagging a false conflict is the safe direction to be wrong in for a
    coordination gate; silently missing a real one is not.
  - `symbols_expected_to_modify` IS used in conflict detection now, on
    the exact same honest terms every other declaration here is checked
    on: an EXACT-NAME overlap between two live declarations' own
    self-reported symbol lists is a conflict, checked regardless of
    whether their write-path scopes also overlap (the same symbol name
    declared in two different files can still mean two agents are about
    to touch the same shared interface/contract). This is NOT static
    analysis -- nothing here parses source code to confirm a symbol is
    actually present or actually gets modified; it is the declarative
    layer B36 already is, extended to one more self-reported field,
    exactly like `write_exact`/`write_globs` were. Inventing real
    per-language AST-based symbol analysis remains a separate, much
    larger, and still-open piece of work.
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
                f"files={c.overlapping_files} symbols={c.overlapping_symbols}"
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


def _symbols_overlap(a_symbols: list[str], b_symbols: list[str]) -> list[str]:
    """Exact-name overlap between two declarations' own self-reported
    `symbols_expected_to_modify` lists. Same honest discipline as
    `_paths_overlap`'s exact-path leg: no fuzzy matching, no attempt to
    resolve aliases/qualified names -- a caller that declares
    `"process_payment"` and another that declares
    `"payments.process_payment"` are NOT flagged as the same symbol here;
    over-approximating THAT would risk false conflicts between genuinely
    unrelated same-named-but-different symbols across a large codebase,
    which is the wrong direction to be wrong in for a purely advisory,
    high-volume signal like this one. Case-sensitive, exact string match
    only."""
    return sorted(set(a_symbols) & set(b_symbols))


async def _unmet_dependencies(pool: asyncpg.Pool, *, execution_run_id: str, node_order: int) -> list[int]:
    """This node's `deps` (from the compiled plan's task_graphs.nodes
    JSON -- the SAME structure durable_run/get_run_context already
    read) whose own execution_run_nodes row is not yet 'succeeded'."""
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
    plan_nodes = _json.loads(raw) if isinstance(raw, str) else (raw or [])
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


async def check_file_intent_conflicts(
    pool: asyncpg.Pool, *, write_exact: list[str], write_globs: list[str],
    symbols: Optional[list[str]] = None,
    exclude_execution_run_id: Optional[str] = None, exclude_node_order: Optional[int] = None,
) -> list[ConflictEntry]:
    """
    Pure read: every OTHER live (non-expired-lease, non-terminal-status)
    declaration whose write scope overlaps `write_exact`/`write_globs`,
    OR whose declared `symbols_expected_to_modify` shares an exact name
    with `symbols` -- checked independently, so a symbol-name collision
    is flagged even when the two declarations' file scopes don't overlap
    at all (see `_symbols_overlap`'s own docstring for exactly what "exact
    name" means here). Never writes anything -- `declare_file_intent` is
    the only writer, and calls this first.
    """
    symbols = symbols or []
    if not write_exact and not write_globs and not symbols:
        return []
    rows = await pool.fetch(
        """
        SELECT execution_run_id, node_order, owner_agent_id, write_exact, write_globs,
               symbols_expected_to_modify
        FROM execution_run_nodes
        WHERE file_intent_lease_expires_at IS NOT NULL
          AND file_intent_lease_expires_at > now()
          AND status NOT IN ('succeeded', 'cancelled')
          AND (write_exact != '[]' OR write_globs != '[]' OR symbols_expected_to_modify != '[]')
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
        overlap = _paths_overlap(write_exact, write_globs, row["write_exact"], row["write_globs"])
        symbol_overlap = _symbols_overlap(symbols, row["symbols_expected_to_modify"] or [])
        if overlap or symbol_overlap:
            conflicts.append(ConflictEntry(
                execution_run_id=str(row["execution_run_id"]), node_order=row["node_order"],
                owner_agent_id=row["owner_agent_id"], overlapping_files=overlap,
                overlapping_symbols=symbol_overlap,
            ))
    return conflicts


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
    symbols_expected_to_modify = symbols_expected_to_modify or []
    conflicts = await check_file_intent_conflicts(
        pool, write_exact=write_exact, write_globs=write_globs,
        symbols=symbols_expected_to_modify,
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
        read_exact or [], read_globs or [], write_exact, write_globs,
        symbols_expected_to_modify or [], lease_seconds,
    )
    if row is None:
        raise ValueError(f"no node at order {node_order} for execution_run_id {execution_run_id}")
    return dict(row)


async def release_file_intent(pool: asyncpg.Pool, *, execution_run_id: str, node_order: int) -> None:
    """Explicit early release -- clears the lease so this node's
    declaration immediately stops participating in conflict checks
    (rather than waiting out its own expiry)."""
    await pool.execute(
        "UPDATE execution_run_nodes SET file_intent_lease_expires_at = NULL "
        "WHERE execution_run_id = $1::uuid AND node_order = $2",
        execution_run_id, node_order,
    )
