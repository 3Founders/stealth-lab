"""
MCP hardening B9-B13: recursive child ProcedureRuns.

Runtime recursion is represented ONLY as a parent/child EXECUTION
relationship (`execution_runs.parent_run_id`/`parent_node_id`, migration
51) -- never a canonical Procedure dependency (B15). This module is the
one place that decides whether a NEW child run may be created at all:
cycle detection over the real ancestor chain, a max-depth bound, and a
max-child-count budget over the chain's real root -- all configurable via
`app.config.settings`, never hardcoded (the hardening spec's own
instruction). No new scheduler: this only gates `durable_run.start_run()`
being called with a `parent_run_id`; the actual run still goes through
the SAME durable substrate every other run does.

"WAITING_CHILD" (B10) is derived, not stored -- see migration 52's own
comment for why. `describe_child_status()` below is the read-side of
that: given a parent run + node, is there a live (non-terminal) child
run pointing at it right now.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg

from app.config import settings
from app.execution import durable_run as _dr


class RecursionCycleDetected(_dr.DurableRunError):
    """A candidate child Procedure already appears among the run's own
    ancestors -- A invoking B invoking A. Never silently allowed; the
    caller must pick a different Procedure, ask, or refuse."""


class RecursionDepthExceeded(_dr.DurableRunError):
    """The ancestor chain is already at (or would exceed)
    `settings.procedure_run_max_recursion_depth`."""


class ChildExecutionBudgetExceeded(_dr.DurableRunError):
    """The chain rooted at this run's `root_run_id` already has
    `settings.procedure_run_max_child_executions` runs."""


class WallClockBudgetExceeded(_dr.DurableRunError):
    """The chain's root run started more than
    `settings.procedure_run_max_wall_clock_seconds` ago."""


@dataclass
class AncestorChain:
    """One entry per ancestor, ROOT FIRST (`chain[0]` is the root run)."""
    run_ids: list[str]
    procedure_ids: list[str]
    root_run_id: str
    root_started_at: Optional[datetime]

    @property
    def depth(self) -> int:
        """Depth of a NEW child appended after this chain -- i.e. the
        chain's own length + 1, matching how `expand_composed_nodes`'s
        `max_depth` already counts (a root run has depth 1)."""
        return len(self.run_ids) + 1


async def _load_ancestor_chain(pool: asyncpg.Pool, run_id: str) -> AncestorChain:
    rows = await pool.fetch(
        """
        WITH RECURSIVE ancestors AS (
            SELECT id, parent_run_id, procedure_id, root_run_id, started_at, created_at, 0 AS depth
            FROM execution_runs WHERE id = $1
            UNION ALL
            SELECT er.id, er.parent_run_id, er.procedure_id, er.root_run_id, er.started_at, er.created_at,
                   a.depth + 1
            FROM execution_runs er JOIN ancestors a ON er.id = a.parent_run_id
        )
        SELECT * FROM ancestors ORDER BY depth DESC
        """,
        run_id,
    )
    if not rows:
        raise _dr.DurableRunError(f"execution_run {run_id} not found")
    root = rows[0]
    return AncestorChain(
        run_ids=[str(r["id"]) for r in rows],
        procedure_ids=[str(r["procedure_id"]) for r in rows],
        root_run_id=str(root["root_run_id"] or root["id"]),
        root_started_at=root["started_at"] or root["created_at"],
    )


def assert_no_cycle(chain: AncestorChain, candidate_procedure_id: str) -> None:
    """
    Pure (no DB): raises iff `candidate_procedure_id` already appears
    among `chain`'s own ancestors -- A invoking B invoking A. Split out
    from `check_recursion_limits` because the CANDIDATE procedure is
    typically only known after `find_best_way`'s own routing/applicability
    step has already run (the depth/budget/wall-clock checks below do
    not depend on which procedure ends up selected, so they can and
    should run BEFORE that costlier work; the cycle check can only run
    after it, once there is a real candidate to check).
    """
    if str(candidate_procedure_id) in chain.procedure_ids:
        raise RecursionCycleDetected(
            f"procedure {candidate_procedure_id} already appears in this run's own "
            f"ancestor chain ({chain.run_ids}) -- refusing to create a cyclic child run"
        )


async def check_recursion_limits(
    pool: asyncpg.Pool, *, parent_run_id: str, parent_node_id: Optional[str] = None,
    candidate_procedure_id: Optional[str] = None,
) -> AncestorChain:
    """
    Call BEFORE creating a child run (i.e. before `start_run(...,
    parent_run_id=parent_run_id, ...)`). Raises a typed error and creates
    nothing on any violation; returns the parent's ancestor chain (for
    the caller to pass `root_run_id`/depth-derived bookkeeping through,
    and to later call `assert_no_cycle` once a candidate procedure is
    chosen) on success.

    `candidate_procedure_id`: optional convenience -- when already known,
    this also runs `assert_no_cycle` immediately rather than requiring a
    separate call.
    """
    chain = await _load_ancestor_chain(pool, parent_run_id)

    if candidate_procedure_id is not None:
        assert_no_cycle(chain, candidate_procedure_id)

    max_depth = settings.procedure_run_max_recursion_depth
    if chain.depth > max_depth:
        raise RecursionDepthExceeded(
            f"ancestor chain depth {chain.depth} exceeds configured "
            f"procedure_run_max_recursion_depth={max_depth}"
        )

    max_children = settings.procedure_run_max_child_executions
    chain_size = await pool.fetchval(
        "SELECT count(*) FROM execution_runs WHERE root_run_id = $1", chain.root_run_id,
    )
    if chain_size >= max_children:
        raise ChildExecutionBudgetExceeded(
            f"chain rooted at {chain.root_run_id} already has {chain_size} runs, "
            f"at configured procedure_run_max_child_executions={max_children}"
        )

    max_wall_clock = settings.procedure_run_max_wall_clock_seconds
    if max_wall_clock is not None and chain.root_started_at is not None:
        elapsed = (datetime.now(timezone.utc) - chain.root_started_at).total_seconds()
        if elapsed > max_wall_clock:
            raise WallClockBudgetExceeded(
                f"chain rooted at {chain.root_run_id} started {elapsed:.0f}s ago, "
                f"exceeding configured procedure_run_max_wall_clock_seconds={max_wall_clock}"
            )

    return chain


async def describe_child_status(
    pool: asyncpg.Pool, *, run_id: str, node_row_id: str,
) -> Optional[dict[str, Any]]:
    """
    The read side of B10's "WAITING_CHILD": is there a live (non-terminal)
    child run whose parent_node_id is THIS node right now? Returns
    `{child_run_id, child_procedure_id, child_procedure_version, child_status}`
    or `None` when no such child exists (including when the only children
    ever created are already terminal -- a terminal child is not
    "waiting" on, it is history).
    """
    row = await pool.fetchrow(
        "SELECT id, procedure_id, procedure_version, status FROM execution_runs "
        "WHERE parent_run_id = $1 AND parent_node_id = $2 "
        "  AND status NOT IN ('succeeded','failed','cancelled') "
        "ORDER BY created_at DESC LIMIT 1",
        run_id, node_row_id,
    )
    if row is None:
        return None
    return {
        "child_run_id": str(row["id"]),
        "child_procedure_id": str(row["procedure_id"]),
        "child_procedure_version": row["procedure_version"],
        "child_status": row["status"],
    }
