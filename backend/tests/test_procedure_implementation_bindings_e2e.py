"""
MCP hardening B23/B24: live-DB half of the Procedure<->Implementation
relation -- idempotent link creation, role-priority resolution,
supported_steps filtering, and the `submit_implementation` MCP tool
(both register-new and link-existing modes).

Same pattern as every other `*_e2e.py` file: skips without a real
DATABASE_URL, self-cleaning by name prefix.
"""
import asyncio
import json
import os
from uuid import uuid4

import pytest

import app.mcp_server.server as srv
from app.db.session import create_pool
from app.execution import implementation_registry
from app.services.access import AccessScope
from app.services.procedure_implementation_bindings import (
    activate_binding,
    get_bindings_for_procedure,
    link_implementation,
    resolve_binding_for_step,
)
from app.services.procedures import capture_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


class _FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class _FakeContext:
    def __init__(self, pool):
        self.request_context = _FakeRequestContext(pool)


async def _cleanup_procedure(pool, name_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM procedures WHERE name LIKE $1 "
        "AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        f"{name_prefix}%",
    )
    await pool.execute(
        "UPDATE procedures SET is_engineering_fixture = true WHERE name LIKE $1", f"{name_prefix}%",
    )


async def _cleanup_implementation(pool, name: str) -> None:
    await pool.execute(
        "DELETE FROM procedure_implementations WHERE implementation_id IN "
        "(SELECT id FROM implementations WHERE name = $1)",
        name,
    )
    await pool.execute("DELETE FROM implementations WHERE name = $1", name)


async def _capture(pool, name: str, **kwargs) -> dict:
    kwargs.setdefault("goal", name)
    result = await capture_procedure(
        pool, name=name, provenance="system_pending_review", scope_type="global", **kwargs,
    )
    return dict(await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", result["id"]))


def test_link_implementation_is_idempotent_create_or_return():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        proc_name = f"proc-test-pib-idem-{run_id}"
        impl_name = f"impl-test-pib-idem-{run_id}"
        try:
            procedure = await _capture(pool, proc_name)
            impl = await implementation_registry.register(
                pool, name=impl_name, kind="tool", provider="test", created_by="tester",
            )
            first = await link_implementation(
                pool, procedure_id=procedure["procedure_id"], implementation_id=impl["id"],
                role="primary", created_by="tester",
            )
            second = await link_implementation(
                pool, procedure_id=procedure["procedure_id"], implementation_id=impl["id"],
                role="primary", created_by="tester",
            )
            assert first["id"] == second["id"]
            count = await pool.fetchval(
                "SELECT count(*) FROM procedure_implementations WHERE implementation_id = $1 AND t_invalid IS NULL",
                impl["id"],
            )
            assert count == 1
        finally:
            await _cleanup_implementation(pool, impl_name)
            await _cleanup_procedure(pool, proc_name)
            await pool.close()

    asyncio.run(_run())


def test_resolve_binding_for_step_prefers_primary_over_supporting_and_respects_supported_steps():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        proc_name = f"proc-test-pib-resolve-{run_id}"
        supporting_name = f"impl-test-pib-supporting-{run_id}"
        primary_name = f"impl-test-pib-primary-{run_id}"
        narrow_name = f"impl-test-pib-narrow-{run_id}"
        try:
            procedure = await _capture(pool, proc_name)

            supporting = await implementation_registry.register(
                pool, name=supporting_name, kind="tool", provider="test", created_by="tester",
            )
            primary = await implementation_registry.register(
                pool, name=primary_name, kind="tool", provider="test", created_by="tester",
            )
            narrow = await implementation_registry.register(
                pool, name=narrow_name, kind="tool", provider="test", created_by="tester",
            )

            b_support = await link_implementation(
                pool, procedure_id=procedure["procedure_id"], implementation_id=supporting["id"],
                role="supporting", created_by="tester",
            )
            b_primary = await link_implementation(
                pool, procedure_id=procedure["procedure_id"], implementation_id=primary["id"],
                role="primary", created_by="tester",
            )
            b_narrow = await link_implementation(
                pool, procedure_id=procedure["procedure_id"], implementation_id=narrow["id"],
                role="primary", supported_steps=[5], created_by="tester",
            )
            for b in (b_support, b_primary, b_narrow):
                await activate_binding(pool, b["id"])

            # Step 0: both unrestricted bindings (support, primary) are
            # eligible; primary must win.
            best = await resolve_binding_for_step(
                pool, procedure_id=procedure["procedure_id"], step_order=0,
            )
            assert str(best["implementation_id"]) == str(primary["id"])

            # Step 5: the narrow, step-specific primary binding is ALSO
            # eligible here, same role as the unrestricted primary --
            # both are real candidates; assert the narrow one is among
            # the resolvable set by checking it resolves at all (role tie
            # is not itself required to break a specific way).
            all_bindings = await get_bindings_for_procedure(
                pool, procedure_id=procedure["procedure_id"], status="active",
            )
            ids_at_step_5 = {
                str(b["implementation_id"]) for b in all_bindings
                if not b["supported_steps"] or 5 in b["supported_steps"]
            }
            assert str(narrow["id"]) in ids_at_step_5
            assert str(primary["id"]) in ids_at_step_5

            # Step 99: only the two unrestricted bindings apply -- the
            # narrow one (supported_steps=[5]) must NOT be offered.
            best_99 = await resolve_binding_for_step(
                pool, procedure_id=procedure["procedure_id"], step_order=99,
            )
            assert str(best_99["implementation_id"]) != str(narrow["id"])

            # No procedure at all -> no candidates, never a fabricated pick.
            none_result = await resolve_binding_for_step(
                pool, procedure_id=str(uuid4()), step_order=0,
            )
            assert none_result is None
        finally:
            for name in (supporting_name, primary_name, narrow_name):
                await _cleanup_implementation(pool, name)
            await _cleanup_procedure(pool, proc_name)
            await pool.close()

    asyncio.run(_run())


def test_resolve_binding_for_step_returns_none_when_only_candidate_status_is_candidate():
    """A binding still in status='candidate' (never activated) must not
    be offered as a resolution -- 'active' is the real gate, matching
    implementation_registry's own status semantics."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        proc_name = f"proc-test-pib-candidateonly-{run_id}"
        impl_name = f"impl-test-pib-candidateonly-{run_id}"
        try:
            procedure = await _capture(pool, proc_name)
            impl = await implementation_registry.register(
                pool, name=impl_name, kind="tool", provider="test", created_by="tester",
            )
            await link_implementation(
                pool, procedure_id=procedure["procedure_id"], implementation_id=impl["id"],
                role="primary", created_by="tester",
            )
            # Deliberately NOT activated.
            result = await resolve_binding_for_step(
                pool, procedure_id=procedure["procedure_id"], step_order=0,
            )
            assert result is None
        finally:
            await _cleanup_implementation(pool, impl_name)
            await _cleanup_procedure(pool, proc_name)
            await pool.close()

    asyncio.run(_run())


def test_submit_implementation_registers_new_and_links():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        proc_name = f"proc-test-submitimpl-new-{run_id}"
        impl_name = f"impl-test-submitimpl-new-{run_id}"
        try:
            procedure = await _capture(pool, proc_name)
            ctx = _FakeContext(pool)

            result = await srv.submit_implementation(
                procedure_id=str(procedure["procedure_id"]), role="primary", ctx=ctx,
                name=impl_name, kind="tool", provider="test",
            )
            payload = json.loads(result)
            assert payload["implementation_id"]
            assert payload["binding"]["role"] == "primary"

            impl_row = await implementation_registry.get(
                pool, payload["implementation_id"], scope=AccessScope.unrestricted(),
            )
            assert impl_row is not None
            assert impl_row["name"] == impl_name
            assert impl_row["status"] == "candidate"  # never born active

            # Calling again with the SAME name/provider/version must
            # REFUSE (not silently create a duplicate implementation
            # identity) -- register()'s own unique-index guarantee.
            duplicate = await srv.submit_implementation(
                procedure_id=str(procedure["procedure_id"]), role="primary", ctx=ctx,
                name=impl_name, kind="tool", provider="test",
            )
            assert duplicate.startswith("REFUSED:")
        finally:
            await _cleanup_implementation(pool, impl_name)
            await _cleanup_procedure(pool, proc_name)
            await pool.close()

    asyncio.run(_run())


def test_submit_implementation_links_an_existing_implementation():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        proc_name = f"proc-test-submitimpl-existing-{run_id}"
        impl_name = f"impl-test-submitimpl-existing-{run_id}"
        try:
            procedure = await _capture(pool, proc_name)
            impl = await implementation_registry.register(
                pool, name=impl_name, kind="tool", provider="test", created_by="tester",
            )
            ctx = _FakeContext(pool)

            result = await srv.submit_implementation(
                procedure_id=str(procedure["procedure_id"]), role="verification", ctx=ctx,
                implementation_id=impl["id"],
            )
            payload = json.loads(result)
            assert payload["implementation_id"] == impl["id"]
            assert payload["binding"]["role"] == "verification"

            missing_fields = await srv.submit_implementation(
                procedure_id=str(procedure["procedure_id"]), role="primary", ctx=ctx,
            )
            assert missing_fields.startswith("REFUSED:")

            bad_role = await srv.submit_implementation(
                procedure_id=str(procedure["procedure_id"]), role="not_a_role", ctx=ctx,
                implementation_id=impl["id"],
            )
            assert bad_role.startswith("REFUSED:")
        finally:
            await _cleanup_implementation(pool, impl_name)
            await _cleanup_procedure(pool, proc_name)
            await pool.close()

    asyncio.run(_run())
