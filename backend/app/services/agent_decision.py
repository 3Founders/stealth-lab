"""
Human decision on an agent sitting in pending_human_approval
(AGENT_STORE_PLAN.md, Section 3).

For graph_workflow agents specifically, approval also computes
`runnable` for real, by checking whether every constituent task node's
skill_ref actually resolves in the given SkillRegistry, rather than
defaulting it to a guess in either direction.

This is a genuinely different risk category from Section 3b/Stage 6's
sandboxing gate, worth being precise about why. A graph_workflow only
ever invokes skills already in the closed, hand-written SkillRegistry
(see app/services/execution.py) -- running one is running already-
trusted internal code in a different order, not running untrusted
third-party code. The Stage 6 gate is specifically about the latter and
doesn't apply here.
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

import asyncpg

from app.services.agent_review_state_machine import AgentReviewStateMachine
from app.services.execution import SkillRegistry

log = logging.getLogger(__name__)


class AgentNotPendingApproval(Exception):
    pass


async def _compute_runnable(
    pool: asyncpg.Pool, agent_id: UUID, registry: SkillRegistry,
    acknowledge_sandbox_limitations: bool = False,
) -> bool:
    row = await pool.fetchrow(
        "SELECT source, execution_mode, skill_ref, workflow_task_ids, source_detail "
        "FROM agents WHERE id = $1",
        agent_id,
    )
    if row["source"] in ("user_submitted", "external_marketplace"):
        # Passing code_review.py's independent critique and a clean
        # bandit scan is real signal but not sufficient on its own -- see
        # AGENT_STORE_PLAN.md Section 3b and app/services/sandbox.py's
        # own docstring for exactly what is and isn't actually verified
        # (network isolation and resource limits are; filesystem
        # isolation is not built; non-root behavior in production is
        # unconfirmed). A human approving this specific agent must
        # explicitly acknowledge those gaps via
        # acknowledge_sandbox_limitations=True -- an automated pass
        # alone does not flip this, on purpose.
        if not acknowledge_sandbox_limitations:
            return False

        source_detail = row["source_detail"] or {}
        code = source_detail.get("code")
        if not code:
            # A structured request with no actual code (the common
            # user_submitted case, deliberately, per Section 4) has
            # nothing to sandbox-test at all.
            return False

        from app.services.sandbox import run_sandboxed
        result = run_sandboxed(code, input_data={})
        if result.isolation_failed:
            log.error(
                "sandbox isolation mechanism unavailable for agent %s -- "
                "failing closed, not falling back to unsandboxed execution",
                agent_id,
            )
            return False
        # A clean run (isolation engaged, didn't crash or hang) is the
        # bar here -- this is a smoke test that the code executes inside
        # the sandbox without incident, not a correctness or security
        # proof, that distinction is the whole point of requiring
        # explicit acknowledgment above.
        return not result.timed_out and result.exit_code == 0

    if row["execution_mode"] == "local_skill":
        return row["skill_ref"] in registry
    if row["execution_mode"] == "remote_http":
        # A remote endpoint's availability can't be verified by checking
        # a local registry -- treated as runnable once approved; the
        # SSRF-shaped concern flagged in AGENT_STORE_PLAN.md Section 6
        # belongs at call time, not here.
        return True
    if row["execution_mode"] == "graph_workflow":
        task_ids = row["workflow_task_ids"] or []
        if not task_ids:
            return False
        skill_refs = await pool.fetch(
            "SELECT skill_ref FROM task_nodes WHERE id = ANY($1::uuid[])",
            [UUID(t) for t in task_ids],
        )
        # Every step needs a real skill_ref that actually resolves --
        # one unregistered or empty step means the workflow can't
        # actually run end to end, even if the graph structure itself
        # was approved.
        return len(skill_refs) == len(task_ids) and all(
            r["skill_ref"] and r["skill_ref"] in registry for r in skill_refs
        )
    return False


async def decide_agent(
    pool: asyncpg.Pool,
    agent_id: UUID,
    decision: str,
    registry: SkillRegistry,
    actor: Optional[str] = None,
    reason: Optional[str] = None,
    acknowledge_sandbox_limitations: bool = False,
) -> dict:
    """
    decision is 'approved' or 'rejected'.

    `acknowledge_sandbox_limitations` only matters for code-sourced
    agents (user_submitted, external_marketplace) -- see
    _compute_runnable's docstring and app/services/sandbox.py for
    exactly what this is acknowledging. Ignored, harmlessly, for
    graph_derived and internal agents, which don't go through the
    sandbox at all.
    """
    machine = AgentReviewStateMachine(pool)
    current = await machine.current_state(agent_id)
    if current != "pending_human_approval":
        raise AgentNotPendingApproval(
            f"agent {agent_id} is in review_state={current!r}, not "
            "pending_human_approval -- nothing to decide yet."
        )

    await machine.transition(agent_id, decision, actor=actor, reason=reason)

    runnable = False
    if decision == "approved":
        runnable = await _compute_runnable(
            pool, agent_id, registry,
            acknowledge_sandbox_limitations=acknowledge_sandbox_limitations,
        )
        await pool.execute("UPDATE agents SET runnable = $2 WHERE id = $1", agent_id, runnable)

    return {"agent_id": agent_id, "review_state": decision, "runnable": runnable}
