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
from app.execution import recorder as _rec


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


class TokenBudgetExceeded(_dr.DurableRunError):
    """The chain's real, accumulated token usage (SUM across every run
    sharing this chain's root_run_id -- `durable_run.record_run_usage`,
    never estimated) already meets or exceeds
    `settings.procedure_run_max_tokens`."""


class ToolCallBudgetExceeded(_dr.DurableRunError):
    """The chain's real, accumulated tool-call usage already meets or
    exceeds `settings.procedure_run_max_tool_calls`."""


class CostBudgetExceeded(_dr.DurableRunError):
    """The chain's real, accumulated cost usage (USD) already meets or
    exceeds `settings.procedure_run_max_cost_usd`."""


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

    # B12's remaining three named budgets -- real, ATOMIC accumulation
    # (durable_run.record_run_usage), never estimated, summed across
    # every run in the chain (same "root_run_id" pattern as
    # max_child_executions above). Each check is skipped when its
    # setting is None (an explicit opt-out, matching wall-clock's own
    # discipline) -- no silent, hardcoded default.
    max_tokens = settings.procedure_run_max_tokens
    max_tool_calls = settings.procedure_run_max_tool_calls
    max_cost_usd = settings.procedure_run_max_cost_usd
    if max_tokens is not None or max_tool_calls is not None or max_cost_usd is not None:
        usage = await pool.fetchrow(
            "SELECT COALESCE(SUM(tokens_used), 0) AS tokens, "
            " COALESCE(SUM(tool_calls_used), 0) AS tool_calls, "
            " COALESCE(SUM(cost_usd_used), 0) AS cost_usd "
            "FROM execution_runs WHERE root_run_id = $1",
            chain.root_run_id,
        )
        if max_tokens is not None and usage["tokens"] >= max_tokens:
            raise TokenBudgetExceeded(
                f"chain rooted at {chain.root_run_id} has used {usage['tokens']} tokens, "
                f"at or over configured procedure_run_max_tokens={max_tokens}"
            )
        if max_tool_calls is not None and usage["tool_calls"] >= max_tool_calls:
            raise ToolCallBudgetExceeded(
                f"chain rooted at {chain.root_run_id} has used {usage['tool_calls']} tool calls, "
                f"at or over configured procedure_run_max_tool_calls={max_tool_calls}"
            )
        if max_cost_usd is not None and float(usage["cost_usd"]) >= max_cost_usd:
            raise CostBudgetExceeded(
                f"chain rooted at {chain.root_run_id} has used ${usage['cost_usd']:.4f}, "
                f"at or over configured procedure_run_max_cost_usd={max_cost_usd}"
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


# ---------------------------------------------------------------------------
# B11: recursive failure semantics.
#
# "If B fails: A receives structured outcome -> retry B OR search
# alternative OR branch OR ask user OR fail A. Never leave the parent
# permanently RUNNING after a terminal child."
#
# `describe_child_status` (above) treats every terminal child (succeeded
# OR failed) identically -- "nothing waiting, proceed normally" -- which
# is exactly the gap B11 warns about: nothing ever looked at a FAILED
# child specifically, so a parent whose child failed and whose host never
# happened to call `retry_run_node`/`report_execution` on the PARENT's
# own node could stay `running` forever. The two functions below close
# that: `describe_terminal_child_failure` finds the real, unhandled
# failure; `decide_child_failure_strategy` picks ONE of the five named
# strategies from REAL signals (never a coin flip) and, for `fail_parent`
# specifically, actually performs the real transition -- not merely a
# recommendation nobody applies.
# ---------------------------------------------------------------------------
FAILURE_STRATEGIES: tuple[str, ...] = ("retry", "search_alternative", "branch", "ask_user", "fail_parent")


async def describe_terminal_child_failure(
    pool: asyncpg.Pool, *, run_id: str, node_row_id: str,
) -> Optional[dict[str, Any]]:
    """The most recent child for this parent node, if it is TERMINALLY
    FAILED and the parent's own node has not yet been marked failed
    itself (i.e. this failure is real and still unhandled). `None` when
    there is no child, the most recent child succeeded, or the parent's
    own node already reflects the failure (nothing new to decide)."""
    parent_node = await pool.fetchrow(
        "SELECT status FROM execution_run_nodes WHERE id = $1", node_row_id,
    )
    if parent_node is None or parent_node["status"] == "failed":
        return None
    child = await pool.fetchrow(
        "SELECT id, procedure_id, procedure_version, status FROM execution_runs "
        "WHERE parent_run_id = $1 AND parent_node_id = $2 "
        "ORDER BY created_at DESC LIMIT 1",
        run_id, node_row_id,
    )
    if child is None or child["status"] != "failed":
        return None
    return {
        "child_run_id": str(child["id"]),
        "child_procedure_id": str(child["procedure_id"]),
        "child_procedure_version": child["procedure_version"],
    }


async def decide_child_failure_strategy(
    pool: asyncpg.Pool, *, parent_run_id: str, parent_node_id: str, child_run_id: str,
) -> dict[str, Any]:
    """B11's real decision, from real signals only:

      1. `retry`             -- the child's own failed node has a
                                 RETRYABLE error_class (durable_run.py's
                                 own real classifier) and attempts remain.
      2. `search_alternative` -- not retryable (or exhausted), but a REAL
                                 alternative exists: another ACTIVE
                                 Procedure<->Implementation binding for the
                                 same child procedure (B23/B24), or another
                                 REAL applicable procedure for the same
                                 goal text (diagnose_candidates, excluding
                                 the one that just failed) -- AND recursion
                                 budgets would still allow trying it.
      3. `ask_user`           -- no automated alternative exists, but
                                 budgets allow further work -- a human
                                 decision is the honest next step (never
                                 silently picked FOR the human).
      4. `fail_parent`        -- budgets are already exhausted (a new
                                 child could not be created anyway) --
                                 REALLY applied here: the parent's own
                                 node is marked failed
                                 (error_class='downstream_child_failed'),
                                 satisfying "never leave the parent
                                 permanently RUNNING after a terminal
                                 child" as an enforced fact, not a
                                 recommendation nobody acts on.

    `branch` is never auto-selected: stored Procedures have no branching
    field (db/18's own schema) -- an honest, currently-unreachable member
    of `FAILURE_STRATEGIES`, not a fabricated capability. A host may still
    branch explicitly (start a different child run itself); this
    function only decides what the SYSTEM can automate.
    """
    child_node = await pool.fetchrow(
        "SELECT error_class, attempt_count, max_attempts FROM execution_run_nodes "
        "WHERE execution_run_id = $1 AND status = 'failed' ORDER BY node_order LIMIT 1",
        child_run_id,
    )
    child_run = await pool.fetchrow(
        "SELECT procedure_id FROM execution_runs WHERE id = $1", child_run_id,
    )
    child_procedure_id = str(child_run["procedure_id"]) if child_run else None

    if child_node is not None and _dr._is_retryable(child_node["error_class"]) \
            and child_node["attempt_count"] < child_node["max_attempts"]:
        return {
            "strategy": "retry", "reason": f"error_class {child_node['error_class']!r} is retryable "
            f"and attempt {child_node['attempt_count']}/{child_node['max_attempts']} remains",
        }

    try:
        await check_recursion_limits(pool, parent_run_id=parent_run_id, parent_node_id=parent_node_id)
        budget_allows_more = True
    except (RecursionDepthExceeded, ChildExecutionBudgetExceeded, WallClockBudgetExceeded) as exc:
        budget_allows_more = False
        budget_reason = str(exc)

    if budget_allows_more and child_procedure_id is not None:
        from app.services.procedure_implementation_bindings import get_bindings_for_procedure

        bindings = await get_bindings_for_procedure(pool, procedure_id=child_procedure_id, status="active")
        if len(bindings) > 1:
            return {
                "strategy": "search_alternative",
                "reason": f"{len(bindings)} active implementation bindings exist for the failed "
                f"procedure -- a different one may succeed",
            }

        plan_row = await pool.fetchrow(
            "SELECT ep.task_description FROM execution_runs er "
            "JOIN execution_plans ep ON ep.id = er.execution_plan_id WHERE er.id = $1",
            child_run_id,
        )
        if plan_row is not None and plan_row["task_description"]:
            from app.services.applicability import diagnose_candidates
            from app.services.embeddings import Embedder

            embedder = Embedder()
            goal_vec = await embedder.embed_one(plan_row["task_description"], input_type="query")
            candidates = await diagnose_candidates(
                pool, goal_embedding=goal_vec, embedding_model_id=embedder.embedding_model_id(),
                goal_text=plan_row["task_description"], limit=3,
            )
            alternative = next(
                (c for c in candidates if c.applicable and str((c.procedure or {}).get("procedure_id")) != child_procedure_id),
                None,
            )
            if alternative is not None:
                return {
                    "strategy": "search_alternative",
                    "reason": f"a different applicable procedure "
                    f"{(alternative.procedure or {}).get('procedure_id')!r} exists for the same goal",
                }

    if budget_allows_more:
        return {
            "strategy": "ask_user",
            "reason": "the failed child has no automated retry/alternative, but recursion budgets "
            "still allow further work -- a human decision is the honest next step",
        }

    # fail_parent: REALLY applied, not just recommended. A direct
    # transition (not _node_finish, which requires the CALLER to already
    # hold the node's worker-id lease -- this is a SYSTEM-initiated
    # transition on a node nobody currently holds, not a worker
    # self-reporting its own claimed work). Still respects the terminal
    # fence (migration 36): only fires from a genuinely non-terminal
    # status, via the same WHERE guard every other real transition here
    # uses.
    async with pool.acquire() as conn, conn.transaction():
        node_order = await conn.fetchval(
            "SELECT node_order FROM execution_run_nodes WHERE id = $1", parent_node_id,
        )
        tag = await conn.execute(
            "UPDATE execution_run_nodes SET status='failed', ended_at=now(), "
            " error_class='downstream_child_failed', "
            " error_ref=$2::jsonb, verification_state='failed', worker_id=NULL, lease_expires_at=NULL "
            "WHERE id=$1 AND status NOT IN ('succeeded','failed','cancelled')",
            parent_node_id, {"reason": budget_reason, "child_run_id": child_run_id},
        )
        if tag != "UPDATE 0":
            await _rec.record_node_failed(
                conn, parent_run_id, node_order=node_order, error_class="downstream_child_failed",
            )
    return {
        "strategy": "fail_parent",
        "reason": f"no retry/alternative available and recursion budgets are exhausted ({budget_reason}) "
        "-- the parent's own node has been marked failed",
    }
