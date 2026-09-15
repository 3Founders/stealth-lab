"""
Live-database proving test for app/execution/implementation_selection.py +
app/execution/implementation_registry.py::list_implementations_by_goal +
db/81_implementation_goal_index.sql. Requires a real DATABASE_URL, skips
(not fails) without one -- same pattern as every other *_e2e.py file.

Proves the real Sec 8-10 pipeline end to end: structured goal lookup ->
deterministic hard-constraint filter -> explainable rank, against real rows
in the real `implementations` table (register() + activate(), then a direct
UPDATE to set `goal` -- migration 80's own docstring records that no
production writer other than the skill-package script classifier
populates this column yet, so a test needs to set it directly, same as
test_implementation_goals_offline.py's own fixtures do for that writer).
"""
from __future__ import annotations

import json
import os

import pytest

from app.db.session import create_pool
from app.execution.implementation_registry import activate, list_implementations_by_goal, register
from app.execution.implementation_selection import select_implementation_for_goal
from app.services.access import AccessScope

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "impl-selection-e2e"


async def _cleanup(pool) -> None:
    # evidence is append-only (Band 1.9a invariant #19 -- a DB trigger
    # rejects DELETE outright); retract via the t_invalid tombstone, the
    # same real mechanism a production caller would use, not a special
    # test-only bypass.
    await pool.execute(
        "UPDATE evidence SET t_invalid = now() WHERE target_type='implementation' AND target_id IN "
        "(SELECT id FROM implementations WHERE name LIKE $1) AND t_invalid IS NULL", f"{PREFIX}%",
    )
    await pool.execute("DELETE FROM implementations WHERE name LIKE $1", f"{PREFIX}%")


async def _register_active(pool, *, name: str, goal: str, **kwargs) -> dict:
    row = await register(pool, name=name, kind="tool", provider=PREFIX, created_by=PREFIX, **kwargs)
    await activate(pool, row["id"])
    await pool.execute("UPDATE implementations SET goal = $1 WHERE id = $2::uuid", goal, row["id"])
    row["goal"] = goal
    row["status"] = "active"
    return row


def test_list_implementations_by_goal_exact_match_only():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            scope = AccessScope.unrestricted()
            await _register_active(pool, name=f"{PREFIX}-a", goal=f"{PREFIX}-goal-x")
            await _register_active(pool, name=f"{PREFIX}-b", goal=f"{PREFIX}-goal-y")

            hits = await list_implementations_by_goal(pool, f"{PREFIX}-goal-x", scope=scope)
            assert [h["name"] for h in hits] == [f"{PREFIX}-a"]

            none_hits = await list_implementations_by_goal(pool, f"{PREFIX}-goal-nonexistent", scope=scope)
            assert none_hits == []
        finally:
            await _cleanup(pool)
            await pool.close()

    import asyncio
    asyncio.run(_run())


def test_select_implementation_for_goal_no_candidates_is_honest_empty():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            scope = AccessScope.unrestricted()
            result = await select_implementation_for_goal(pool, f"{PREFIX}-goal-nobody-has", scope=scope)
            assert result.chosen is None
            assert result.candidates_considered == []
            assert "no registered" in result.rationale
        finally:
            await pool.close()

    import asyncio
    asyncio.run(_run())


def test_select_implementation_for_goal_filters_hard_incompatibility():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            scope = AccessScope.unrestricted()
            goal = f"{PREFIX}-goal-filter"
            third_party = await _register_active(
                pool, name=f"{PREFIX}-third-party", goal=goal, execution_location="third_party_hosted",
            )
            local = await _register_active(
                pool, name=f"{PREFIX}-local", goal=goal, execution_location="stealth_hosted",
            )

            result = await select_implementation_for_goal(
                pool, goal, scope=scope, context={"privacy_policy": "no_third_party"},
            )
            assert len(result.candidates_considered) == 2
            assert result.chosen is not None
            assert result.chosen["id"] == local["id"]
            rejected_ids = {r.implementation["id"] for r in result.filtered_out}
            assert third_party["id"] in rejected_ids
            rejected = next(r for r in result.filtered_out if r.implementation["id"] == third_party["id"])
            assert any("privacy" in reason for reason in rejected.rejection_reasons)
        finally:
            await _cleanup(pool)
            await pool.close()

    import asyncio
    asyncio.run(_run())


def test_select_implementation_for_goal_ranks_by_real_evidence():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            scope = AccessScope.unrestricted()
            goal = f"{PREFIX}-goal-ranked"
            weak = await _register_active(pool, name=f"{PREFIX}-weak", goal=goal)
            strong = await _register_active(pool, name=f"{PREFIX}-strong", goal=goal)

            # Real evidence rows -- 'strong' has an all-success stream,
            # 'weak' has an all-failure stream, both via the SAME evidence
            # table the applicability cascade's own capability ranking
            # reads (evidence.target_type='implementation', db/24).
            for impl_id, outcome in ((strong["id"], "success"), (strong["id"], "success"),
                                      (weak["id"], "failure"), (weak["id"], "failure")):
                success_criteria = {"predicate": "exit_code == 0"} if outcome == "success" else {}
                await pool.execute(
                    """
                    INSERT INTO evidence (
                        id, evidence_type, target_type, target_id, direction,
                        strength_score, strength_method,
                        outcome_status, context_key, success_criteria, visibility, created_by
                    ) VALUES (
                        gen_random_uuid(), 'execution_result', 'implementation', $1::uuid,
                        $2, 1.0, $3, $4, $5, $6::jsonb, 'public', $7
                    )
                    """,
                    impl_id,
                    "supports" if outcome == "success" else "contradicts",
                    f"{PREFIX}-strength-method",
                    outcome,
                    f"{PREFIX}-context",
                    json.dumps(success_criteria),
                    PREFIX,
                )

            result = await select_implementation_for_goal(pool, goal, scope=scope)
            assert result.chosen is not None
            assert result.chosen["id"] == strong["id"]
            success_component = next(
                c for r in result.survivors if r.implementation["id"] == strong["id"]
                for c in r.components if c.name == "success_rate"
            )
            assert success_component.value > 0.0
        finally:
            await _cleanup(pool)
            await pool.close()

    import asyncio
    asyncio.run(_run())
