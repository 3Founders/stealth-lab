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
    AmbiguousBindingResolutionError,
    activate_binding,
    get_bindings_for_procedure,
    link_implementation,
    resolve_binding_for_step,
    resolve_binding_for_step_with_reason,
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


def test_resolve_binding_for_step_excludes_a_disabled_implementation_despite_an_active_binding():
    """B24 availability: the earlier version of this function only
    checked the BINDING's own status='active' -- never the bound
    IMPLEMENTATION's own lifecycle status. A disabled implementation
    must never be resolved just because its binding row is still
    active."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        proc_name = f"proc-test-pib-avail-{run_id}"
        disabled_name = f"impl-test-pib-avail-disabled-{run_id}"
        healthy_name = f"impl-test-pib-avail-healthy-{run_id}"
        try:
            procedure = await _capture(pool, proc_name)
            disabled_impl = await implementation_registry.register(
                pool, name=disabled_name, kind="tool", provider="test", created_by="tester",
            )
            healthy_impl = await implementation_registry.register(
                pool, name=healthy_name, kind="tool", provider="test", created_by="tester",
            )
            await implementation_registry.activate(pool, disabled_impl["id"])
            await implementation_registry.disable(pool, disabled_impl["id"])
            await implementation_registry.activate(pool, healthy_impl["id"])

            b_disabled = await link_implementation(
                pool, procedure_id=procedure["procedure_id"], implementation_id=disabled_impl["id"],
                role="primary", created_by="tester",
            )
            await activate_binding(pool, b_disabled["id"])

            # Only the disabled implementation is bound so far -- its
            # binding is active, but the implementation itself is not
            # resolvable at all.
            result = await resolve_binding_for_step(
                pool, procedure_id=procedure["procedure_id"], step_order=0,
            )
            assert result is None, (
                "a disabled implementation must never be resolved even "
                "though its own binding row is status='active'"
            )

            # Now bind the healthy one too -- it must be the one resolved.
            b_healthy = await link_implementation(
                pool, procedure_id=procedure["procedure_id"], implementation_id=healthy_impl["id"],
                role="primary", created_by="tester",
            )
            await activate_binding(pool, b_healthy["id"])
            result2 = await resolve_binding_for_step(
                pool, procedure_id=procedure["procedure_id"], step_order=0,
            )
            assert str(result2["implementation_id"]) == str(healthy_impl["id"])
        finally:
            for name in (disabled_name, healthy_name):
                await _cleanup_implementation(pool, name)
            await _cleanup_procedure(pool, proc_name)
            await pool.close()

    asyncio.run(_run())


def test_resolve_binding_for_step_with_reason_reports_missing_when_no_candidate_exists():
    """MCP hardening B38 STRICT CLOSURE: V4's typed-state vocabulary
    distinguishes MISSING_IMPLEMENTATION ("no candidate implementation
    names this role/step at all") from IMPLEMENTATION_UNAVAILABLE
    ("candidates exist but are all disabled/quarantined/deprecated or
    fail a real requirement") -- this proves the "missing" half:
    a procedure with zero bindings at all."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        proc_name = f"proc-test-pib-reason-missing-{run_id}"
        try:
            procedure = await _capture(pool, proc_name)
            result, reason = await resolve_binding_for_step_with_reason(
                pool, procedure_id=procedure["procedure_id"], step_order=0,
            )
            assert result is None
            assert reason == "missing"
        finally:
            await _cleanup_procedure(pool, proc_name)
            await pool.close()

    asyncio.run(_run())


def test_resolve_binding_for_step_with_reason_reports_unavailable_when_every_candidate_is_disabled():
    """The "unavailable" half of the same distinction: a real binding
    exists, but the only implementation it names is disabled -- a
    genuinely different real state from "missing" above, not a
    fabricated one (the cascade already computes it at a later stage)."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        proc_name = f"proc-test-pib-reason-unavail-{run_id}"
        disabled_name = f"impl-test-pib-reason-unavail-{run_id}"
        try:
            procedure = await _capture(pool, proc_name)
            disabled_impl = await implementation_registry.register(
                pool, name=disabled_name, kind="tool", provider="test", created_by="tester",
            )
            await implementation_registry.activate(pool, disabled_impl["id"])
            await implementation_registry.disable(pool, disabled_impl["id"])

            b_disabled = await link_implementation(
                pool, procedure_id=procedure["procedure_id"], implementation_id=disabled_impl["id"],
                role="primary", created_by="tester",
            )
            await activate_binding(pool, b_disabled["id"])

            result, reason = await resolve_binding_for_step_with_reason(
                pool, procedure_id=procedure["procedure_id"], step_order=0,
            )
            assert result is None
            assert reason == "unavailable"
        finally:
            await _cleanup_implementation(pool, disabled_name)
            await _cleanup_procedure(pool, proc_name)
            await pool.close()

    asyncio.run(_run())


def test_resolve_binding_for_step_prefers_a_verified_implementation_among_tied_roles():
    """B24 verification/evidence tiebreak: two candidates tied on role
    (both 'primary', both unrestricted) must resolve to the VERIFIED one,
    never an arbitrary pick."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        proc_name = f"proc-test-pib-verify-{run_id}"
        unverified_name = f"impl-test-pib-verify-unverified-{run_id}"
        verified_name = f"impl-test-pib-verify-verified-{run_id}"
        try:
            procedure = await _capture(pool, proc_name)
            unverified_impl = await implementation_registry.register(
                pool, name=unverified_name, kind="tool", provider="test", created_by="tester",
            )
            verified_impl = await implementation_registry.register(
                pool, name=verified_name, kind="tool", provider="test", created_by="tester",
            )
            await implementation_registry.activate(pool, unverified_impl["id"])
            await implementation_registry.activate(pool, verified_impl["id"])
            await implementation_registry.verify(pool, verified_impl["id"])

            for impl in (unverified_impl, verified_impl):
                b = await link_implementation(
                    pool, procedure_id=procedure["procedure_id"], implementation_id=impl["id"],
                    role="primary", created_by="tester",
                )
                await activate_binding(pool, b["id"])

            result = await resolve_binding_for_step(
                pool, procedure_id=procedure["procedure_id"], step_order=0,
            )
            assert str(result["implementation_id"]) == str(verified_impl["id"]), (
                "the verified candidate must win the tie over the unverified one"
            )
        finally:
            for name in (unverified_name, verified_name):
                await _cleanup_implementation(pool, name)
            await _cleanup_procedure(pool, proc_name)
            await pool.close()

    asyncio.run(_run())


def test_resolve_binding_for_step_raises_ambiguous_when_nothing_real_breaks_the_tie():
    """B24: "If resolution is ambiguous... route to ask, plan, or
    refuse rather than silently selecting an unsuitable mechanism." Two
    candidates, same role, both unrestricted, both active, neither
    verified -- nothing REAL distinguishes them, so this must raise
    rather than silently pick one by insertion order."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        proc_name = f"proc-test-pib-ambiguous-{run_id}"
        impl_a_name = f"impl-test-pib-ambiguous-a-{run_id}"
        impl_b_name = f"impl-test-pib-ambiguous-b-{run_id}"
        try:
            procedure = await _capture(pool, proc_name)
            impl_a = await implementation_registry.register(
                pool, name=impl_a_name, kind="tool", provider="test", created_by="tester",
            )
            impl_b = await implementation_registry.register(
                pool, name=impl_b_name, kind="tool", provider="test", created_by="tester",
            )
            await implementation_registry.activate(pool, impl_a["id"])
            await implementation_registry.activate(pool, impl_b["id"])

            for impl in (impl_a, impl_b):
                b = await link_implementation(
                    pool, procedure_id=procedure["procedure_id"], implementation_id=impl["id"],
                    role="primary", created_by="tester",
                )
                await activate_binding(pool, b["id"])

            with pytest.raises(AmbiguousBindingResolutionError) as exc_info:
                await resolve_binding_for_step(
                    pool, procedure_id=procedure["procedure_id"], step_order=0,
                )
            tied_ids = set(exc_info.value.tied_implementation_ids)
            assert tied_ids == {str(impl_a["id"]), str(impl_b["id"])}
        finally:
            for name in (impl_a_name, impl_b_name):
                await _cleanup_implementation(pool, name)
            await _cleanup_procedure(pool, proc_name)
            await pool.close()

    asyncio.run(_run())


def test_resolve_binding_for_step_excludes_a_candidate_failing_real_requirements():
    """B24 requirements/environment: a candidate declaring
    `requirements={"network": True}` must be excluded when the caller's
    `available_context` honestly reports no network access -- reusing
    `implementation_executor.check_requirements`, not a second copy of
    the same check."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        proc_name = f"proc-test-pib-requirements-{run_id}"
        needs_net_name = f"impl-test-pib-requirements-net-{run_id}"
        no_reqs_name = f"impl-test-pib-requirements-none-{run_id}"
        try:
            procedure = await _capture(pool, proc_name)
            needs_net = await implementation_registry.register(
                pool, name=needs_net_name, kind="tool", provider="test", created_by="tester",
                requirements={"network": True},
            )
            no_reqs = await implementation_registry.register(
                pool, name=no_reqs_name, kind="tool", provider="test", created_by="tester",
            )
            await implementation_registry.activate(pool, needs_net["id"])
            await implementation_registry.activate(pool, no_reqs["id"])

            b_net = await link_implementation(
                pool, procedure_id=procedure["procedure_id"], implementation_id=needs_net["id"],
                role="primary", created_by="tester",
            )
            await activate_binding(pool, b_net["id"])

            # Only the network-requiring candidate exists so far -- no
            # network in the real context means nothing resolves.
            # `check_requirements` compares `available[key]` directly
            # against `requirements[key]` (its own real convention,
            # distinct from `validate_invocation`'s `network_access`
            # key) -- reused here, not reimplemented.
            result = await resolve_binding_for_step(
                pool, procedure_id=procedure["procedure_id"], step_order=0,
                available_context={"network": False},
            )
            assert result is None

            # With network available, it resolves fine.
            result_with_net = await resolve_binding_for_step(
                pool, procedure_id=procedure["procedure_id"], step_order=0,
                available_context={"network": True},
            )
            assert str(result_with_net["implementation_id"]) == str(needs_net["id"])

            # Bind the no-requirements candidate too -- without network,
            # it is the only one that resolves (real filtering, not a
            # blanket refusal).
            b_none = await link_implementation(
                pool, procedure_id=procedure["procedure_id"], implementation_id=no_reqs["id"],
                role="primary", created_by="tester",
            )
            await activate_binding(pool, b_none["id"])
            result_no_net = await resolve_binding_for_step(
                pool, procedure_id=procedure["procedure_id"], step_order=0,
                available_context={"network": False},
            )
            assert str(result_no_net["implementation_id"]) == str(no_reqs["id"])
        finally:
            for name in (needs_net_name, no_reqs_name):
                await _cleanup_implementation(pool, name)
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
