"""
MCP hardening B29: Implementation lifecycle
(REGISTERED -> RESOLVABLE -> AVAILABLE -> VERIFIED_IN_CONTEXT -> REUSED,
plus UNAVAILABLE/RETIRED flags and real recorded failure classes),
derived (not separately mutated) from real facts on `implementations` /
`procedure_implementations` / `evidence` -- see
app/execution/implementation_lifecycle.py for why a derived view, not a
second lifecycle column.

Drives a real Implementation through every one of those facts and
asserts the chain progresses through every state in order, then wires
one real recorded failure and confirms `status_flags`/
`failure_classes_seen` reflect it -- through the real MCP tool
(inspect_implementation) where the spec asks for it, and the service
layer directly for setup.

Skips itself when DATABASE_URL is unset, self-cleaning by id.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="e2e: DATABASE_URL unset")

import app.mcp_server.server as srv  # noqa: E402
from app.db.session import create_pool  # noqa: E402
from app.execution import implementation_registry  # noqa: E402
from app.services.access import AccessScope  # noqa: E402
from app.execution.implementation_lifecycle import CHAIN, compute_implementation_lifecycle_state  # noqa: E402
from app.services.procedure_implementation_bindings import link_implementation  # noqa: E402
from app.services.procedures import capture_procedure  # noqa: E402


class _FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class _FakeContext:
    def __init__(self, pool):
        self.request_context = _FakeRequestContext(pool)


async def _cleanup_procedure(pool, row_id) -> None:
    deleted = await pool.execute(
        "DELETE FROM procedures WHERE id=$1 AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        row_id,
    )
    if deleted == "DELETE 0":
        await pool.execute("UPDATE procedures SET is_engineering_fixture = true WHERE id=$1", row_id)


def test_execution_location_defaults_and_accepts_third_party_hosted():
    """B27: externally hosted implementations are first-class --
    execution_location is a real, storable, CHECK-constrained column
    (migration 71), defaulting to stealth_hosted, surfaced on the real
    row (get()/inspect_implementation), rejecting a bogus value rather
    than silently accepting it."""
    async def _run():
        pool = await create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
        suffix = uuid.uuid4().hex[:8]
        default_impl_id = None
        hosted_impl_id = None
        try:
            default_impl = await implementation_registry.register(
                pool, name=f"execloc-default-{suffix}", kind="tool",
                provider="execloc-e2e", created_by="execloc_e2e",
            )
            default_impl_id = default_impl["id"]
            assert default_impl["execution_location"] == "stealth_hosted"

            hosted_impl = await implementation_registry.register(
                pool, name=f"execloc-hosted-{suffix}", kind="api",
                provider="execloc-e2e", created_by="execloc_e2e",
                execution_location="third_party_hosted",
            )
            hosted_impl_id = hosted_impl["id"]
            assert hosted_impl["execution_location"] == "third_party_hosted"

            fetched = await implementation_registry.get(pool, hosted_impl_id, scope=AccessScope.anonymous())
            assert fetched["execution_location"] == "third_party_hosted"

            with pytest.raises(ValueError, match="execution_location"):
                await implementation_registry.register(
                    pool, name=f"execloc-bad-{suffix}", kind="tool",
                    provider="execloc-e2e", created_by="execloc_e2e",
                    execution_location="on_the_moon",
                )
        finally:
            if default_impl_id is not None:
                await pool.execute("DELETE FROM implementations WHERE id=$1", default_impl_id)
            if hosted_impl_id is not None:
                await pool.execute("DELETE FROM implementations WHERE id=$1", hosted_impl_id)
            await pool.close()

    asyncio.run(_run())


def test_implementation_lifecycle_progresses_and_records_failure():
    async def _run():
        pool = await create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
        suffix = uuid.uuid4().hex[:8]
        impl_id = None
        row_ids: list = []
        try:
            # REGISTERED only: no locator/invocation yet.
            impl = await implementation_registry.register(
                pool, name=f"lifecycle-impl-{suffix}", kind="tool",
                provider="lifecycle-e2e-provider", created_by="lifecycle_e2e",
            )
            impl_id = impl["id"]

            state = await compute_implementation_lifecycle_state(pool, impl_id)
            assert state["reached"] == ["REGISTERED"]
            assert state["status_flags"] == {"unavailable": False, "retired": False}
            assert state["failure_classes_seen"] == []

            missing = await compute_implementation_lifecycle_state(pool, str(uuid.uuid4()))
            assert missing is None

            # RESOLVABLE: give it a real invocation.
            await pool.execute(
                "UPDATE implementations SET invocation = $2::jsonb WHERE id = $1",
                impl_id, json.dumps({"entrypoint": "run"}),
            )
            state = await compute_implementation_lifecycle_state(pool, impl_id)
            assert state["reached"] == ["REGISTERED", "RESOLVABLE"]

            # AVAILABLE: real activate() transition.
            await implementation_registry.activate(pool, impl_id)
            state = await compute_implementation_lifecycle_state(pool, impl_id)
            assert state["current_state"] == "AVAILABLE"

            # VERIFIED_IN_CONTEXT: real verify() transition.
            await implementation_registry.verify(pool, impl_id)
            state = await compute_implementation_lifecycle_state(pool, impl_id)
            assert state["current_state"] == "VERIFIED_IN_CONTEXT"

            # REUSED: bind to TWO distinct real procedures.
            for i in range(2):
                res = await capture_procedure(
                    pool, name=f"proc-test-lifecycle-{suffix}-{i}", goal=f"lifecycle probe {i}",
                    steps=[{"order": 0, "goal": "do a thing"}],
                    provenance="prior_library", scope_type="global", created_by="lifecycle_e2e",
                    embedding=[0.01] * 1024,
                )
                row_ids.append(res["id"])
                await link_implementation(
                    pool, procedure_id=res["procedure_id"], implementation_id=impl_id,
                    role="primary", created_by="lifecycle_e2e",
                )
                await pool.execute(
                    "UPDATE procedure_implementations SET status='active' "
                    "WHERE procedure_id=$1 AND implementation_id=$2",
                    res["procedure_id"], impl_id,
                )

            state = await compute_implementation_lifecycle_state(pool, impl_id)
            assert state["reached"] == list(CHAIN)
            assert state["current_state"] == "REUSED"
            assert state["skipped_optional"] == []

            # A real recorded failure: evidence, outcome_status='failure'.
            await pool.execute(
                """
                INSERT INTO evidence (
                    evidence_type, target_type, target_id, direction, strength_score,
                    strength_method, context_key, outcome_status, failure_class,
                    created_by, scope_type
                ) VALUES ('execution_result', 'implementation', $1, 'contradicts', 1.0,
                          'deterministic', $2, 'failure', 'external_failure', 'lifecycle_e2e', 'global')
                """,
                impl_id, f"lifecycle-e2e-{suffix}",
            )
            state = await compute_implementation_lifecycle_state(pool, impl_id)
            assert state["failure_classes_seen"] == ["external_failure"]
            # A recorded failure does not retroactively un-reach REUSED --
            # historical evidence, honestly reported alongside, never
            # overwriting real progress already made.
            assert state["current_state"] == "REUSED"

            # RETIRED / UNAVAILABLE flags, through the real MCP tool.
            await implementation_registry.deprecate(pool, impl_id)
            ctx = _FakeContext(pool)
            inspected = json.loads(await srv.inspect_implementation(impl_id, ctx))
            assert inspected["lifecycle"]["status_flags"]["retired"] is True
            assert inspected["lifecycle"]["failure_classes_seen"] == ["external_failure"]

            missing_tool = await srv.inspect_implementation(str(uuid.uuid4()), ctx)
            assert missing_tool.startswith("REFUSED:")
        finally:
            for rid in row_ids:
                await _cleanup_procedure(pool, rid)
            if impl_id is not None:
                await pool.execute("DELETE FROM procedure_implementations WHERE implementation_id=$1", impl_id)
                # evidence is append-only (Band 1.9a, invariant #19) --
                # retract via the t_invalid tombstone, never DELETE.
                await pool.execute(
                    "UPDATE evidence SET t_invalid = now() "
                    "WHERE target_type='implementation' AND target_id=$1 AND t_invalid IS NULL",
                    impl_id,
                )
                await pool.execute("DELETE FROM implementations WHERE id=$1", impl_id)
            await pool.close()

    asyncio.run(_run())
