"""
Live-database proving tests for V1 Phase 31: "Improve the critical
distinction: INFERRED vs VERIFIED. Do not represent an LLM explanation as
observed fact. A claim should have provenance. A claim that is
contradicted should not continue appearing as current truth."

Real current state, confirmed before writing this file (not re-derived
here -- see claims.py/observations.py's own docstrings, which already
carry this history):

  - `epistemic_status` ('observed' | 'inferred') already exists on
    ClaimProperties (claims.py) and is already stamped honestly by
    promote_observation_to_claim() (observations.py): 'observed' for a
    deterministic trace-event extraction, 'inferred' for anything routed
    through the model extractor. Already proven live in
    test_observations_e2e.py's promote_* tests. NOT re-proven here except
    where this file needs its own fixture claims to exist.
  - `truth_state` ('IN' | 'OUT') and relate_claims() (SUPERSEDES/
    CONTRADICTS) already implement TMS belief revision for a RESOLVED
    contradiction -- already proven live in test_tms_readability_e2e.py.
  - The real gap this file closes: a claim with an OPEN, UNRESOLVED
    knowledge_conflict.py trigger against it is neither truth_state='OUT'
    nor t_invalid-set, so before this change it kept reading as current
    truth while a conflict against it was under active review.
    `claims.list_current_claims()` / `claims.has_open_conflict_trigger()`
    close that gap by excluding a disputed-but-unresolved claim from
    "current truth" queries, without auto-invalidating it (see claims.py's
    own docstring on _DISPUTED_CLAIM_SQL for why not).

Same pattern as the other *_e2e.py files: requires a real DATABASE_URL,
skips (not fails) without one.
"""
import asyncio
import os

import pytest

from app.db.session import create_pool
from app.services.claims import (
    capture_claim,
    has_open_conflict_trigger,
    list_current_claims,
)
from app.services.knowledge_conflict import create_conflict_trigger_for_pair

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "claims-epi-e2e"


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.3] * 1024


async def _cleanup(pool) -> None:
    await pool.execute(
        "DELETE FROM debates WHERE trigger_id IN "
        "(SELECT id FROM triggers WHERE task_node_id IN "
        " (SELECT id FROM task_nodes WHERE name LIKE $1))",
        f"{PREFIX}%",
    )
    await pool.execute(
        "DELETE FROM triggers WHERE task_node_id IN "
        "(SELECT id FROM task_nodes WHERE name LIKE $1)",
        f"{PREFIX}%",
    )
    await pool.execute(
        "DELETE FROM edges WHERE source_id IN "
        "(SELECT id FROM knowledge_nodes WHERE name LIKE $1) "
        "OR source_id IN (SELECT id FROM task_nodes WHERE name LIKE $1) "
        "OR target_id IN (SELECT id FROM knowledge_nodes WHERE name LIKE $1)",
        f"{PREFIX}%",
    )
    await pool.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM task_nodes WHERE name LIKE $1", f"{PREFIX}%")


async def _insert_task_node(pool, name: str) -> str:
    await pool.execute(
        "INSERT INTO task_nodes (name, description, skill_ref) VALUES ($1, $1, $2)",
        name, f"skill_{name}",
    )
    return name


async def _capture(pool, statement: str, task_name: str, *, epistemic_status: str) -> str:
    claim_id = await capture_claim(
        pool, statement=statement, task_ids=[f"skill_{task_name}"],
        epistemic_status=epistemic_status, embedder=FakeEmbedder(),
    )
    assert claim_id, f"fixture failed: claim not written ({statement!r})"
    return claim_id


def test_observed_and_inferred_claims_are_not_indistinguishable_in_a_real_query():
    """A deterministic promotion path is already proven live in
    test_observations_e2e.py; this proves the OTHER half of Phase 31's
    ask directly against claims.py's own writer -- an inferred claim,
    constructed with an explicit epistemic_status (no real LLM call
    needed to prove the writer honors the flag, matching this task's own
    scoping), must be REAL-QUERYABLE as distinct from an observed one,
    not silently defaulted or collapsed together."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task_name = await _insert_task_node(pool, f"{PREFIX} split-task")
            observed_id = await _capture(
                pool, f"{PREFIX} file config.py was touched", task_name,
                epistemic_status="observed",
            )
            inferred_id = await _capture(
                pool, f"{PREFIX} the touch to config.py was necessary for the fix", task_name,
                epistemic_status="inferred",
            )

            rows = await pool.fetch(
                "SELECT id, properties->>'epistemic_status' AS status "
                "FROM knowledge_nodes WHERE id = ANY($1::uuid[])",
                [observed_id, inferred_id],
            )
            by_id = {str(r["id"]): r["status"] for r in rows}
            assert by_id[observed_id] == "observed"
            assert by_id[inferred_id] == "inferred"
            assert by_id[observed_id] != by_id[inferred_id], (
                "an inferred/LLM-shaped claim must not be indistinguishable "
                "from a directly observed one in a real stored-properties query"
            )

            # A real query that only wants directly-observed fact (e.g. a
            # caller that must not represent an LLM explanation as
            # observed fact, per Phase 31's own wording) must be able to
            # filter on this honestly.
            observed_only = await pool.fetch(
                "SELECT id FROM knowledge_nodes WHERE node_type = 'claim' "
                "AND properties->>'epistemic_status' = 'observed' "
                "AND id = ANY($1::uuid[])",
                [observed_id, inferred_id],
            )
            assert {str(r["id"]) for r in observed_only} == {observed_id}
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_inferred_claim_never_silently_defaults_to_observed():
    """capture_claim() must never coerce a missing/None epistemic_status
    into 'observed' -- that would be exactly the failure mode Phase 31
    warns against (an LLM explanation silently presented as observed
    fact). Omitting it entirely must leave it unset, not default-true."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task_name = await _insert_task_node(pool, f"{PREFIX} unset-task")
            claim_id = await capture_claim(
                pool, statement=f"{PREFIX} claim with no epistemic_status given",
                task_ids=[f"skill_{task_name}"], embedder=FakeEmbedder(),
            )
            assert claim_id
            row = await pool.fetchrow(
                "SELECT properties FROM knowledge_nodes WHERE id = $1", claim_id
            )
            assert "epistemic_status" not in dict(row["properties"]), (
                "an unstamped claim must stay unstamped, not silently read as "
                "'observed' by a caller that forgets to pass the flag"
            )
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_claim_under_open_unresolved_conflict_is_excluded_from_current_truth():
    """The real gap: create two claims real-similar enough to trip
    knowledge_conflict.py's own detection band, open a real trigger via
    create_conflict_trigger_for_pair (the same production path
    detect_and_create_conflict_trigger uses), and confirm the disputed
    claim disappears from list_current_claims() / has_open_conflict_trigger()
    flips true -- while truth_state stays 'IN' and t_invalid stays NULL,
    proving this is exclusion, not the auto-invalidation this task's own
    design note says would be wrong to do automatically."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task_name = await _insert_task_node(pool, f"{PREFIX} dispute-task")
            claim_a = await _capture(
                pool, f"{PREFIX} disputeword the deploy script uses port 8080 exclusively",
                task_name, epistemic_status="observed",
            )
            claim_b = await _capture(
                pool, f"{PREFIX} disputeword the deploy script uses port 9090 exclusively",
                task_name, epistemic_status="observed",
            )

            before = await list_current_claims(pool, task_id=f"skill_{task_name}")
            before_ids = {r["id"] for r in before}
            assert claim_a in {str(i) for i in before_ids} or str(claim_a) in {str(i) for i in before_ids}
            assert not await has_open_conflict_trigger(pool, claim_a)
            assert not await has_open_conflict_trigger(pool, claim_b)

            trigger_id = await create_conflict_trigger_for_pair(
                pool, claim_a, claim_b, similarity=0.8,
                approver_id=f"{PREFIX}-detector",
            )
            assert trigger_id is not None

            assert await has_open_conflict_trigger(pool, claim_a), (
                "an open, unresolved trigger must mark the claim as disputed"
            )
            assert await has_open_conflict_trigger(pool, claim_b), (
                "both sides of the conflict are under active review"
            )

            after = await list_current_claims(pool, task_id=f"skill_{task_name}")
            after_ids = {str(r["id"]) for r in after}
            assert claim_a not in after_ids, (
                "a claim with an open unresolved conflict trigger must not "
                "keep appearing as current truth"
            )
            assert claim_b not in after_ids

            # Deliberately NOT auto-invalidated -- this is exclusion from
            # the current-truth view, not a TMS belief flip or bi-temporal
            # invalidation. Confirms the design decision, not just its effect.
            row_a = await pool.fetchrow(
                "SELECT properties->>'truth_state' AS ts, t_invalid FROM knowledge_nodes "
                "WHERE id = $1", claim_a,
            )
            assert row_a["ts"] == "IN", (
                "an open dispute must not flip truth_state -- that is relate_claims()'s "
                "job once a debate actually resolves the contradiction"
            )
            assert row_a["t_invalid"] is None

            # Resolve the debate (approve one side) exactly the way the real
            # debate-approval flow would leave the row -- direct INSERT here
            # (no debate row exists yet: create_conflict_trigger_for_pair only
            # records the trigger, same as detect_and_create_conflict_trigger;
            # opening a debate is LoopOrchestrator's job, out of this file's
            # scope) since driving the full panel/eval loop is out of scope;
            # the state transition machinery itself is state_machine.py's job,
            # already covered elsewhere.
            await pool.execute(
                "INSERT INTO debates (trigger_id, state) VALUES ($1, 'APPROVED')", trigger_id,
            )

            assert not await has_open_conflict_trigger(pool, claim_a), (
                "once the debate resolves, the claim is no longer 'under active "
                "review' -- the same 'unresolved' definition triggers.py's own "
                "TriggerDetector.record() uses"
            )
            resolved = await list_current_claims(pool, task_id=f"skill_{task_name}")
            resolved_ids = {str(r["id"]) for r in resolved}
            assert claim_a in resolved_ids, (
                "a resolved dispute must stop being excluded -- exclusion tracks "
                "'unresolved', not 'was ever disputed'"
            )
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_out_claim_stays_excluded_from_current_truth_even_with_no_open_dispute():
    """Sanity check that list_current_claims() composes correctly with the
    PRE-EXISTING truth_state mechanism (relate_claims), not just the new
    dispute-exclusion path -- a resolved SUPERSEDES must still exclude the
    old claim even though it never had a conflict trigger at all."""
    async def _run():
        from app.services.claims import relate_claims

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task_name = await _insert_task_node(pool, f"{PREFIX} supersede-task")
            old_id = await _capture(
                pool, f"{PREFIX} legacy build step uses ant", task_name,
                epistemic_status="observed",
            )
            new_id = await _capture(
                pool, f"{PREFIX} current build step uses gradle", task_name,
                epistemic_status="observed",
            )
            await relate_claims(
                pool, from_claim_id=new_id, to_claim_id=old_id,
                relation="SUPERSEDES", created_by=f"{PREFIX}-test",
            )

            current = await list_current_claims(pool, task_id=f"skill_{task_name}")
            current_ids = {str(r["id"]) for r in current}
            assert old_id not in current_ids
            assert new_id in current_ids
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
