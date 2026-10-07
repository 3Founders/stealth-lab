"""
Live-database proving tests for organisation governance (db/136): strict row-level security, the settle-once ledger,
budgets held under concurrency, the kill switch and allowlists taking effect on the next call, the audit hash chain, and
the two-person erasure with its manifest.

Requires DATABASE_URL (a migrated Postgres + pgvector); skips without one. Each test uses organisations it creates
itself. The ledger and audit log are append-only by design, so those rows are NOT cleaned up afterwards -- they belong
to throwaway organisation ids. A test that needs the database role to differ from the owner (row-level security does not
apply to a superuser) switches to a plain role with SET LOCAL ROLE, and skips if it cannot create one.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from decimal import Decimal
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from app.db.session import create_pool
from app.services import org_governance as gov
from app.services.access import TenantScope, tenant_transaction

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test",
)

OWNER = {"owner"}
ALLOW = dict(allowed_providers=["example"], allowed_models=["m1"], allowed_tools=["call_model"],
             allowed_data_classes=["USER_PRIVATE"])


def run(coro):
    return asyncio.run(coro)


async def new_org(pool) -> str:
    org = str(uuid.uuid4())
    await pool.execute("INSERT INTO organizations (id, name, slug) VALUES ($1::uuid, $2, $3)", org, f"gov-{org[:8]}",
                       f"gov-{org}")
    return org


async def set_policy(pool, org, *, monthly="10", daily="10", **over):
    changes = {**ALLOW, "monthly_budget_usd": monthly, "per_user_daily_budget_usd": daily, **over}
    return await gov.put_policy(pool, actor_subject="admin-1", actor_user_id=None, actor_roles={"admin"}, org_id=org,
                                changes=changes, expected_version=None)


def call_args(org, actor="u1", worst=0.3, **over):
    base = dict(org_id=org, actor_subject=actor, tool="call_model", unit="m1|direct", connection_id="c1",
                provider="example", model="m1", scaffold="direct", data_class="USER_PRIVATE", instance_key=None,
                worst_case_usd=worst)
    base.update(over)
    return base


async def ledger(pool, org):
    async with tenant_transaction(pool, TenantScope.for_tenant(org)) as conn:
        return [dict(r) for r in await conn.fetch(
            "SELECT * FROM provider_call_ledger WHERE organization_id = $1::uuid ORDER BY created_at", org)]


def window():
    now = datetime.now(timezone.utc)
    return now - timedelta(days=1), now + timedelta(days=1)


# ------------------------------------------------------------------ policy: default deny, explicit, versioned

def test_no_policy_means_denied_and_a_new_policy_must_be_complete():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            with pytest.raises(gov.PolicyMissing):
                await gov.reserve_call(pool, **call_args(org))
            with pytest.raises(gov.PolicyMissing):
                await gov.load_policy(pool, org)
            with pytest.raises(gov.Invalid, match="every field explicitly"):
                await gov.put_policy(pool, actor_subject="a", actor_user_id=None, actor_roles={"owner"}, org_id=org,
                                     changes={"allowed_models": ["m1"]}, expected_version=None)
            created = await set_policy(pool, org)
            assert created.version == 1 and created.kill_switch is False and created.allowed_models == ("m1",)
        finally:
            await pool.close()
    run(main())


def test_a_stale_version_is_a_conflict_and_every_change_bumps_the_version():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            await set_policy(pool, org)
            common = dict(actor_subject="a", actor_user_id=None, actor_roles={"admin"}, org_id=org)
            v2 = await gov.put_policy(pool, changes={"allowed_models": ["m1", "m2"]}, expected_version=1, **common)
            assert v2.version == 2 and v2.allowed_models == ("m1", "m2")
            with pytest.raises(gov.Conflict, match="version 2, not 1"):
                await gov.put_policy(pool, changes={"allowed_models": ["m9"]}, expected_version=1, **common)
            with pytest.raises(gov.Conflict):                       # a second create, with no version, is refused
                await gov.put_policy(pool, changes={"allowed_models": ["m9"]}, expected_version=None, **common)
            assert (await gov.load_policy(pool, org)).allowed_models == ("m1", "m2")
        finally:
            await pool.close()
    run(main())


def test_the_kill_switch_and_allowlists_apply_to_the_very_next_call():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            await set_policy(pool, org)
            first = await gov.reserve_call(pool, **call_args(org))
            await gov.settle_call(pool, first, tokens_input_fresh=1, tokens_cache_read=0, tokens_cache_write=0,
                                  tokens_output=1, cost_usd=0.01, cost_source="declared", latency_ms=5)
            await gov.set_kill_switch(pool, actor_subject="a", actor_user_id=None, actor_roles={"admin"}, org_id=org,
                                      on=True, reason="incident 7")
            with pytest.raises(gov.PolicyDenied, match="incident 7"):
                await gov.reserve_call(pool, **call_args(org))
            await gov.set_kill_switch(pool, actor_subject="a", actor_user_id=None, actor_roles={"admin"}, org_id=org,
                                      on=False, reason="resolved")
            await gov.reserve_call(pool, **call_args(org))                                  # resumes
            for over, needle in ((dict(provider="other"), "provider"), (dict(model="m2"), "model"),
                                 (dict(tool="submit_way"), "tool"), (dict(data_class="CONFIDENTIAL_DATA"), "data class")):
                with pytest.raises(gov.PolicyDenied, match=needle):
                    await gov.reserve_call(pool, **call_args(org, **over))
        finally:
            await pool.close()
    run(main())


# ------------------------------------------------------------------ the ledger and the budgets

def test_the_ledger_settles_once_and_cannot_be_rewritten_or_deleted():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            await set_policy(pool, org)
            res = await gov.reserve_call(pool, **call_args(org, worst=0.5))
            assert [r["status"] for r in await ledger(pool, org)] == ["reserved"]
            await gov.settle_call(pool, res, tokens_input_fresh=200, tokens_cache_read=800, tokens_cache_write=0,
                                  tokens_output=100, cost_usd=0.12, cost_source="declared", latency_ms=40)
            row = (await ledger(pool, org))[0]
            assert (row["status"], row["tokens_input_fresh"], row["tokens_cache_read"], row["tokens_cache_write"],
                    row["tokens_output"], row["cost_usd"], row["cost_source"]) == (
                "settled", 200, 800, 0, 100, Decimal("0.120000"), "declared")
            with pytest.raises(gov.GovernanceError, match="could not be settled"):
                await gov.settle_call(pool, res, tokens_input_fresh=1, tokens_cache_read=0, tokens_cache_write=0,
                                      tokens_output=1, cost_usd=0.0, cost_source="declared", latency_ms=1)
            async with tenant_transaction(pool, TenantScope.for_tenant(org)) as conn:
                with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="already settled"):
                    await conn.execute("UPDATE provider_call_ledger SET cost_usd = 0 WHERE id = $1::uuid", res.ledger_id)
            async with tenant_transaction(pool, TenantScope.for_tenant(org)) as conn:
                with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="append-only"):
                    await conn.execute("DELETE FROM provider_call_ledger WHERE id = $1::uuid", res.ledger_id)
        finally:
            await pool.close()
    run(main())


def test_an_identity_column_cannot_change_while_a_row_is_reserved():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            await set_policy(pool, org)
            res = await gov.reserve_call(pool, **call_args(org, worst=0.5))
            async with tenant_transaction(pool, TenantScope.for_tenant(org)) as conn:
                with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="immutable"):
                    await conn.execute("UPDATE provider_call_ledger SET status = 'settled', cost_usd = 0, "
                                       "cost_source = 'declared', settled_at = now(), reserved_usd = 0 "
                                       "WHERE id = $1::uuid", res.ledger_id)
        finally:
            await pool.close()
    run(main())


def test_unknown_cost_is_recorded_as_the_worst_case_never_as_zero():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            await set_policy(pool, org)
            res = await gov.reserve_call(pool, **call_args(org, worst=0.4))
            await gov.settle_call(pool, res, tokens_input_fresh=None, tokens_cache_read=None, tokens_cache_write=None,
                                  tokens_output=None, cost_usd=None, cost_source=None, latency_ms=None)
            row = (await ledger(pool, org))[0]
            assert row["cost_usd"] == Decimal("0.400000") and row["cost_source"] == "upper_bound"
            usage = await gov.usage_daily(pool, actor_roles={"admin"}, org_id=org, since=window()[0], until=window()[1])
            assert usage[0]["cost_usd"] == Decimal("0.400000") and usage[0]["upper_bound_calls"] == 1
        finally:
            await pool.close()
    run(main())


def test_budgets_count_holds_and_settled_spend_and_a_failed_call_frees_its_hold():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            await set_policy(pool, org, monthly="1.00", daily="1.00")
            a = await gov.reserve_call(pool, **call_args(org, worst=0.6))
            with pytest.raises(gov.BudgetExceeded, match="monthly budget"):
                await gov.reserve_call(pool, **call_args(org, worst=0.6))                    # the hold counts
            await gov.fail_call(pool, a, error_type="ProviderCallFailed")                    # released
            b = await gov.reserve_call(pool, **call_args(org, worst=0.6))
            await gov.settle_call(pool, b, tokens_input_fresh=1, tokens_cache_read=0, tokens_cache_write=0,
                                  tokens_output=1, cost_usd=0.1, cost_source="declared", latency_ms=1)
            await gov.reserve_call(pool, **call_args(org, worst=0.6))                        # 0.1 settled + 0.6 held
            with pytest.raises(gov.BudgetExceeded):
                await gov.reserve_call(pool, **call_args(org, worst=0.6))
            failed = [r for r in await ledger(pool, org) if r["status"] == "failed"][0]
            assert failed["cost_usd"] is None and failed["error_type"] == "ProviderCallFailed"
        finally:
            await pool.close()
    run(main())


def test_a_per_user_daily_budget_is_per_person():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            await set_policy(pool, org, monthly="100", daily="0.5")
            await gov.reserve_call(pool, **call_args(org, actor="alice", worst=0.4))
            with pytest.raises(gov.BudgetExceeded, match="daily budget"):
                await gov.reserve_call(pool, **call_args(org, actor="alice", worst=0.4))
            await gov.reserve_call(pool, **call_args(org, actor="bob", worst=0.4))
        finally:
            await pool.close()
    run(main())


def test_concurrent_calls_cannot_spend_past_the_budget_together():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=2, max_size=8)
        try:
            org = await new_org(pool)
            await set_policy(pool, org, monthly="1.00", daily="100")
            results = await asyncio.gather(
                *[gov.reserve_call(pool, **call_args(org, actor=f"u{i}", worst=0.3)) for i in range(8)],
                return_exceptions=True)
            ok = [r for r in results if isinstance(r, gov.Reservation)]
            denied = [r for r in results if isinstance(r, gov.BudgetExceeded)]
            assert len(ok) == 3 and len(denied) == 5, results                      # 3 x 0.3 = 0.9 fits, a 4th would not
            assert sum(r.reserved_usd for r in ok) <= Decimal("1.00")
        finally:
            await pool.close()
    run(main())


def test_zero_budgets_and_unpriced_units_cannot_spend():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            await set_policy(pool, org, monthly="0", daily="5")
            with pytest.raises(gov.BudgetExceeded, match="monthly budget is 0"):
                await gov.reserve_call(pool, **call_args(org))
            await gov.put_policy(pool, actor_subject="a", actor_user_id=None, actor_roles={"admin"}, org_id=org,
                                 changes={"monthly_budget_usd": "5", "per_user_daily_budget_usd": "0"}, expected_version=1)
            with pytest.raises(gov.BudgetExceeded, match="per-user daily budget is 0"):
                await gov.reserve_call(pool, **call_args(org))
            await gov.put_policy(pool, actor_subject="a", actor_user_id=None, actor_roles={"admin"}, org_id=org,
                                 changes={"per_user_daily_budget_usd": "5"}, expected_version=2)
            with pytest.raises(gov.PolicyDenied, match="no price"):
                await gov.reserve_call(pool, **call_args(org, worst=None))
        finally:
            await pool.close()
    run(main())


# ------------------------------------------------------------------ row-level security is strict

@pytest.fixture
def app_role():
    """A plain role (row-level security does not apply to a superuser or to BYPASSRLS roles)."""
    async def make():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            await conn.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sl_gov_app') THEN "
                               "CREATE ROLE sl_gov_app NOLOGIN; END IF; END $$")
            await conn.execute("GRANT USAGE ON SCHEMA public TO sl_gov_app")
            await conn.execute("GRANT SELECT, INSERT, UPDATE ON provider_call_ledger TO sl_gov_app")
            await conn.execute("GRANT SELECT ON v_org_usage_daily, v_org_performance_daily, v_org_usage_by_user_daily, org_denials, org_policies TO sl_gov_app")
        finally:
            await conn.close()
    try:
        run(make())
    except (asyncpg.InsufficientPrivilegeError, asyncpg.PostgresError) as exc:
        pytest.skip(f"cannot create a plain role here: {exc}")
    return "sl_gov_app"


def test_an_unset_tenant_setting_sees_nothing_and_writes_nothing(app_role):
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org_a, org_b = await new_org(pool), await new_org(pool)
            for org in (org_a, org_b):
                await set_policy(pool, org)
                await gov.reserve_call(pool, **call_args(org))
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(f"SET LOCAL ROLE {app_role}")
                    assert await conn.fetchval("SELECT count(*) FROM provider_call_ledger") == 0     # nothing bound
                    assert await conn.fetchval("SELECT count(*) FROM org_policies") == 0
                    with pytest.raises(asyncpg.PostgresError):
                        await conn.execute(
                            "INSERT INTO provider_call_ledger (id, organization_id, actor_subject, tool, unit, "
                            "connection_id, provider, model, scaffold, data_class, status, reserved_usd) "
                            "VALUES (gen_random_uuid(), $1::uuid, 'x', 't', 'u', 'c', 'p', 'm', 's', 'd', 'reserved', 0)",
                            org_a)
            for bound, expect_ledger in ((org_a, org_a), (org_b, org_b)):
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute(f"SET LOCAL ROLE {app_role}")
                        await conn.execute("SELECT set_config('app.tenant_id', $1, TRUE)", bound)
                        seen = {str(r["organization_id"]) for r in await conn.fetch(
                            "SELECT DISTINCT organization_id FROM provider_call_ledger")}
                        assert seen == {expect_ledger}
        finally:
            await pool.close()
    run(main())


def test_the_usage_view_obeys_the_callers_row_level_security(app_role):
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org_a, org_b = await new_org(pool), await new_org(pool)
            for org in (org_a, org_b):
                await set_policy(pool, org)
                res = await gov.reserve_call(pool, **call_args(org))
                await gov.settle_call(pool, res, tokens_input_fresh=1, tokens_cache_read=0, tokens_cache_write=0,
                                      tokens_output=1, cost_usd=0.05, cost_source="declared", latency_ms=1)
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(f"SET LOCAL ROLE {app_role}")
                    await conn.execute("SELECT set_config('app.tenant_id', $1, TRUE)", org_a)
                    seen = {str(r["organization_id"]) for r in await conn.fetch("SELECT * FROM v_org_usage_daily")}
                    assert seen == {org_a}, "the view leaked another organization's usage"
            rows = await gov.usage_daily(pool, actor_roles={"admin"}, org_id=org_b, since=window()[0], until=window()[1])
            assert rows and {str(r["organization_id"]) for r in rows} == {org_b}
        finally:
            await pool.close()
    run(main())


# ------------------------------------------------------------------ audit: written with the change, chained, exportable

def test_every_admin_change_writes_an_audit_row_and_the_chain_verifies():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            await set_policy(pool, org)
            await gov.set_kill_switch(pool, actor_subject="admin-1", actor_user_id=None, actor_roles={"admin"},
                                      org_id=org, on=True, reason="drill")
            await gov.place_legal_hold(pool, actor_subject="owner-1", actor_user_id=None, actor_roles=OWNER, org_id=org,
                                       reason="litigation")
            page = await gov.audit_export(pool, actor_roles={"admin"}, org_id=org, since=window()[0], until=window()[1])
            assert [e["action"] for e in page["events"]] == ["org_policy.put", "org_policy.kill_switch_on",
                                                             "org_legal_hold.placed"]
            assert page["chain_intact"] is True and page["first_broken_id"] is None
            first = page["events"][0]
            assert first["prev_hash"] == "0" * 64 and first["row_hash"] and len(first["row_hash"]) == 64
            assert page["events"][1]["prev_hash"] == first["row_hash"]
            assert first["details"]["after"]["monthly_budget_usd"] == "10"
            paged = await gov.audit_export(pool, actor_roles={"admin"}, org_id=org, since=window()[0],
                                           until=window()[1], after_id=page["events"][0]["id"], limit=1)
            assert [e["id"] for e in paged["events"]] == [page["events"][1]["id"]]
            assert paged["next_after_id"] == page["events"][1]["id"]
        finally:
            await pool.close()
    run(main())


def test_one_organizations_export_never_contains_anothers_events():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org_a, org_b = await new_org(pool), await new_org(pool)
            await set_policy(pool, org_a)
            await set_policy(pool, org_b)
            page = await gov.audit_export(pool, actor_roles={"admin"}, org_id=org_a, since=window()[0], until=window()[1])
            assert page["events"] and {e["tenant_id"] for e in page["events"]} == {org_a}
        finally:
            await pool.close()
    run(main())


def test_audit_rows_cannot_be_updated_or_deleted_and_tampering_is_detected():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            await set_policy(pool, org)
            await gov.set_kill_switch(pool, actor_subject="a", actor_user_id=None, actor_roles={"admin"}, org_id=org,
                                      on=True, reason="x")
            row_id = await pool.fetchval("SELECT min(id) FROM audit_events WHERE tenant_id = $1::uuid", org)
            for sql in ("UPDATE audit_events SET action = 'x' WHERE id = $1", "DELETE FROM audit_events WHERE id = $1"):
                with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="append-only"):
                    await pool.execute(sql, row_id)
            assert await pool.fetchval("SELECT sl_audit_chain_check($1::uuid)", org) is None
            # an owner who disables the guard and edits a row is caught by the chain check
            await pool.execute("ALTER TABLE audit_events DISABLE TRIGGER tg_audit_append_only")
            try:
                await pool.execute("UPDATE audit_events SET details = '{\"forged\": true}'::jsonb WHERE id = $1", row_id)
            finally:
                await pool.execute("ALTER TABLE audit_events ENABLE TRIGGER tg_audit_append_only")
            assert await pool.fetchval("SELECT sl_audit_chain_check($1::uuid)", org) == row_id
            page = await gov.audit_export(pool, actor_roles={"admin"}, org_id=org, since=window()[0], until=window()[1])
            assert page["chain_intact"] is False and page["first_broken_id"] == row_id
        finally:
            await pool.close()
    run(main())


def test_an_admin_change_and_its_audit_row_commit_or_roll_back_together():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            await set_policy(pool, org)
            before = await pool.fetchval("SELECT count(*) FROM audit_events WHERE tenant_id = $1::uuid", org)
            original = gov._audit

            async def failing(conn, **kw):
                raise gov.GovernanceError("audit store unavailable")
            gov._audit = failing
            try:
                with pytest.raises(gov.GovernanceError, match="audit store unavailable"):
                    await gov.set_kill_switch(pool, actor_subject="a", actor_user_id=None, actor_roles={"admin"},
                                              org_id=org, on=True, reason="x")
            finally:
                gov._audit = original
            assert (await gov.load_policy(pool, org)).kill_switch is False                    # the change rolled back
            assert await pool.fetchval("SELECT count(*) FROM audit_events WHERE tenant_id = $1::uuid", org) == before
        finally:
            await pool.close()
    run(main())


# ------------------------------------------------------------------ legal holds and erasure

def test_erasure_needs_two_owners_stops_at_a_hold_and_never_claims_more_than_it_removed():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            await set_policy(pool, org)
            for _ in range(2):
                res = await gov.reserve_call(pool, **call_args(org))
                await gov.settle_call(pool, res, tokens_input_fresh=1, tokens_cache_read=0, tokens_cache_write=0,
                                      tokens_output=1, cost_usd=0.01, cost_source="declared", latency_ms=1)
            common = dict(actor_user_id=None, actor_roles=OWNER, org_id=org)
            request_id = await gov.request_erasure(pool, actor_subject="owner-1", reason="contract ended", **common)
            with pytest.raises(gov.Conflict, match="already has an open"):
                await gov.request_erasure(pool, actor_subject="owner-2", reason="again", **common)
            with pytest.raises(gov.NotAuthorized, match="different owner"):
                await gov.approve_erasure(pool, actor_subject="owner-1", request_id=request_id, **common)
            with pytest.raises(gov.Conflict, match="not .*approved|must be approved"):
                await gov.execute_erasure(pool, actor_subject="owner-1", request_id=request_id, **common)

            hold = await gov.place_legal_hold(pool, actor_subject="owner-1", reason="litigation", **common)
            with pytest.raises(gov.Conflict, match="legal hold"):
                await gov.approve_erasure(pool, actor_subject="owner-2", request_id=request_id, **common)
            await gov.release_legal_hold(pool, actor_subject="owner-1", hold_id=hold, **common)
            await gov.approve_erasure(pool, actor_subject="owner-2", request_id=request_id, **common)

            hold2 = await gov.place_legal_hold(pool, actor_subject="owner-1", reason="new dispute", **common)
            with pytest.raises(gov.Conflict, match="legal hold"):                         # a hold placed AFTER approval
                await gov.execute_erasure(pool, actor_subject="owner-1", request_id=request_id, **common)
            assert len(await ledger(pool, org)) == 2                                      # nothing was deleted
            await gov.release_legal_hold(pool, actor_subject="owner-1", hold_id=hold2, **common)

            out = await gov.execute_erasure(pool, actor_subject="owner-1", request_id=request_id, **common)
            assert out["completed"] is False                                              # core tables are still blocked
            assert out["manifest"]["provider_call_ledger"] == {"deleted": 2, "remaining": 0}
            assert out["manifest"]["org_policies"] == {"deleted": 1, "remaining": 0}
            assert "procedures" in out["blocked"] and "blocked" in out["manifest"]["procedures"]
            assert await ledger(pool, org) == []
            with pytest.raises(gov.PolicyMissing):
                await gov.load_policy(pool, org)
            status = await pool.fetchval("SELECT status FROM org_erasure_requests WHERE id = $1::uuid", request_id)
            assert status == "executing"                                                  # not "completed"
        finally:
            await pool.close()
    run(main())


def test_a_ledger_row_cannot_be_deleted_by_naming_an_unapproved_request():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            await set_policy(pool, org)
            await gov.reserve_call(pool, **call_args(org))
            request_id = await gov.request_erasure(pool, actor_subject="owner-1", actor_user_id=None,
                                                   actor_roles=OWNER, org_id=org, reason="r")       # requested, NOT approved
            async with tenant_transaction(pool, TenantScope.for_tenant(org)) as conn:
                await conn.execute("SELECT set_config('app.erasure_request', $1, TRUE)", request_id)
                with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="append-only"):
                    await conn.execute("DELETE FROM provider_call_ledger WHERE organization_id = $1::uuid", org)
        finally:
            await pool.close()
    run(main())


def test_my_organizations_reads_real_names_and_skips_expired_organizations():
    from app.api import org_admin
    from app.services.auth_context import AuthContext

    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            live, gone = await new_org(pool), await new_org(pool)
            await pool.execute("UPDATE organizations SET t_expired = now() WHERE id = $1::uuid", gone)
            ctx = AuthContext(user_id=str(uuid.uuid4()), subject="u-mine", org_ids=(live, gone),
                              org_roles={live: frozenset({"member", "owner"}), gone: frozenset({"owner"})})
            rows = await org_admin.my_organizations(pool=pool, ctx=ctx)
            assert [(r["organization_id"], r["role"]) for r in rows] == [(live, "owner")]
            assert rows[0]["name"].startswith("gov-")
        finally:
            await pool.close()
    run(main())


def test_declared_costs_are_split_into_components_that_sum_to_the_total_and_other_sources_are_not():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            await set_policy(pool, org)
            prices = {"input": 10.0, "output": 40.0, "cache_read": 1.0, "cache_write": 12.5}
            declared = await gov.reserve_call(pool, **call_args(org, worst=0.5))
            await gov.settle_call(pool, declared, tokens_input_fresh=200, tokens_cache_read=800, tokens_cache_write=100,
                                  tokens_output=100, cost_usd=0.008050, cost_source="declared", latency_ms=5, prices=prices)
            reported = await gov.reserve_call(pool, **call_args(org, worst=0.5))
            await gov.settle_call(pool, reported, tokens_input_fresh=1000, tokens_cache_read=0, tokens_cache_write=0,
                                  tokens_output=100, cost_usd=0.02, cost_source="provider", latency_ms=5, prices=prices)
            bound = await gov.reserve_call(pool, **call_args(org, worst=0.4))
            await gov.settle_call(pool, bound, tokens_input_fresh=None, tokens_cache_read=None, tokens_cache_write=None,
                                  tokens_output=None, cost_usd=None, cost_source=None, latency_ms=None, prices=prices)
            rows = {str(r["id"]): r for r in await ledger(pool, org)}
            d = rows[declared.ledger_id]
            assert (d["cost_input_usd"], d["cost_output_usd"], d["cost_cache_read_usd"], d["cost_cache_write_usd"]) == (
                Decimal("0.002000"), Decimal("0.004000"), Decimal("0.000800"), Decimal("0.001250"))
            assert d["price_input_per_mtok"] == Decimal("10.000000") and d["price_cache_write_per_mtok"] == Decimal("12.500000")
            assert abs(d["cost_usd"] - (d["cost_input_usd"] + d["cost_output_usd"] + d["cost_cache_read_usd"]
                                        + d["cost_cache_write_usd"])) < Decimal("0.000001")
            for other in (reported, bound):                                  # no invented split
                r = rows[other.ledger_id]
                assert r["cost_input_usd"] is None and r["cost_output_usd"] is None and r["price_input_per_mtok"] == Decimal("10.000000")
            usage = (await gov.usage_daily(pool, actor_roles={"admin"}, org_id=org, since=window()[0],
                                           until=window()[1]))[0]
            assert usage["cost_usd"] == Decimal("0.008050") + Decimal("0.020000") + Decimal("0.400000")
            assert usage["cost_input_usd"] == Decimal("0.002000") and usage["cost_output_usd"] == Decimal("0.004000")
            assert usage["cost_cache_read_usd"] == Decimal("0.000800") and usage["cost_cache_write_usd"] == Decimal("0.001250")
            assert usage["cost_unattributed_usd"] == Decimal("0.420000") - Decimal("0.000000")      # provider + upper bound
        finally:
            await pool.close()
    run(main())


def test_a_component_cannot_be_recorded_for_a_cost_that_was_not_declared():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            await set_policy(pool, org)
            res = await gov.reserve_call(pool, **call_args(org))
            async with tenant_transaction(pool, TenantScope.for_tenant(org)) as conn:
                with pytest.raises(asyncpg.CheckViolationError):
                    await conn.execute(
                        "UPDATE provider_call_ledger SET status = 'settled', cost_usd = 1, cost_source = 'provider', "
                        "cost_input_usd = 1, settled_at = now() WHERE id = $1::uuid", res.ledger_id)
        finally:
            await pool.close()
    run(main())


def test_performance_view_gives_percentiles_gate_share_and_cache_rates_over_reporting_calls_only():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            await set_policy(pool, org)

            async def settle(fresh, read, write, latency, gate, tier):
                res = await gov.reserve_call(pool, **call_args(org, worst=0.01, actor=f"u{latency}"))
                await gov.settle_call(pool, res, tokens_input_fresh=fresh, tokens_cache_read=read,
                                      tokens_cache_write=write, tokens_output=10, cost_usd=0.001, cost_source="declared",
                                      latency_ms=latency, gate_ms=gate, tier=tier)
            await settle(200, 800, 0, 100, 5, "light")             # reports cache, a hit
            await settle(1000, 0, 0, 200, 10, "light")             # reports cache, a miss
            await settle(500, None, None, 300, 15, "light")        # the endpoint does not report cache tokens
            await settle(100, 900, 0, 400, 20, "flagship")
            rows = {r["tier"]: r for r in await gov.performance_daily(
                pool, actor_roles={"admin"}, org_id=org, since=window()[0], until=window()[1])}
            light, flagship = rows["light"], rows["flagship"]
            assert light["calls"] == 3
            assert (light["provider_p50_ms"], light["provider_p95_ms"], light["provider_p99_ms"]) == (
                pytest.approx(200.0), pytest.approx(290.0), pytest.approx(298.0))
            assert (light["gate_p50_ms"], light["gate_p95_ms"]) == (pytest.approx(10.0), pytest.approx(14.5))
            assert float(light["gate_time_share"]) == pytest.approx(30 / 630)               # our share of total time
            assert light["calls_reporting_cache"] == 2 and light["calls_with_cache_hit"] == 1
            assert float(light["cache_hit_rate_requests"]) == pytest.approx(0.5)            # the silent endpoint is excluded
            assert float(light["cache_hit_rate_tokens"]) == pytest.approx(800 / 2000)
            assert float(flagship["cache_hit_rate_tokens"]) == pytest.approx(0.9)
        finally:
            await pool.close()
    run(main())


def test_a_provider_that_never_reports_cache_tokens_has_no_cache_rate_not_a_zero_rate():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            await set_policy(pool, org)
            res = await gov.reserve_call(pool, **call_args(org, worst=0.01))
            await gov.settle_call(pool, res, tokens_input_fresh=100, tokens_cache_read=None, tokens_cache_write=None,
                                  tokens_output=10, cost_usd=0.001, cost_source="declared", latency_ms=50, gate_ms=3,
                                  tier=None)
            row = (await gov.performance_daily(pool, actor_roles={"admin"}, org_id=org, since=window()[0],
                                               until=window()[1]))[0]
            assert row["tier"] is None and row["calls_reporting_cache"] == 0
            assert row["cache_hit_rate_requests"] is None and row["cache_hit_rate_tokens"] is None
        finally:
            await pool.close()
    run(main())


def test_the_performance_view_obeys_the_callers_row_level_security(app_role):
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org_a, org_b = await new_org(pool), await new_org(pool)
            for org in (org_a, org_b):
                await set_policy(pool, org)
                res = await gov.reserve_call(pool, **call_args(org, worst=0.01))
                await gov.settle_call(pool, res, tokens_input_fresh=1, tokens_cache_read=0, tokens_cache_write=0,
                                      tokens_output=1, cost_usd=0.001, cost_source="declared", latency_ms=5, gate_ms=1,
                                      tier="light")
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(f"SET LOCAL ROLE {app_role}")
                    await conn.execute("SELECT set_config('app.tenant_id', $1, TRUE)", org_a)
                    seen = {str(r["organization_id"]) for r in await conn.fetch("SELECT * FROM v_org_performance_daily")}
                    assert seen == {org_a}, "the performance view leaked another organization's rows"
        finally:
            await pool.close()
    run(main())


# ------------------------------------------------------------------ denials, per-user usage, calls, budgets, ranges

def _governed_call(pool, org, *, unit="m1|direct", actor="u1", data_class="USER_PRIVATE", prompt="hi", conn_owner=None):
    """A real governed call_unit with a fake endpoint registered for `org`."""
    from app.providers import adapters, registry, service
    from app.providers.types import CallRequest, CallResult, Connection, UnitSpec
    from app.services.access import AccessScope

    class Fake:
        async def call(self, conn, spec, request, secret):
            return CallResult(unit=spec.unit, connection_id=conn.connection_id, text="ok", tokens_in=100, tokens_out=20,
                              tokens_cache_read=300, cost_usd=0.002, cost_source="declared", latency_ms=40)
    adapters.register_adapter("fake-e2e", Fake())
    conn = Connection(connection_id=f"c-{org[:8]}", kind="fake-e2e", base_url="https://8.8.8.8/v1", provider="example",
                      owner=conn_owner or f"org:{org}", allowed_data_classes=("USER_PRIVATE", "CONFIDENTIAL_DATA"),
                      units=(UnitSpec(model="m1", input_per_mtok=1.0, output_per_mtok=2.0, tier="light"),
                             UnitSpec(model="m2", input_per_mtok=1.0, output_per_mtok=2.0)))
    registry._STORES.pop("e2e", None)
    registry.register_connection_store("e2e", registry.StaticConnectionStore([conn]))
    return service.call_unit(pool, AccessScope.for_org_member(actor, [org]), unit, CallRequest(prompt=prompt, data_class=data_class),
                             actor=actor, governed=True, tool="call_model")


def test_refusals_are_recorded_with_a_reason_and_counted_and_allowed_calls_are_not():
    from app.providers.types import ProviderCallDenied

    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            with pytest.raises(ProviderCallDenied, match="no policy"):
                await _governed_call(pool, org)                                          # no policy yet
            await set_policy(pool, org, monthly="1", daily="1", allowed_models=["m1"])
            with pytest.raises(ProviderCallDenied, match="allowlist"):
                await _governed_call(pool, org, unit="m2|direct")                         # model not allowed
            with pytest.raises(ProviderCallDenied, match="allowlist"):
                await _governed_call(pool, org, data_class="CONFIDENTIAL_DATA")           # data class not allowed
            await gov.set_kill_switch(pool, actor_subject="a", actor_user_id=None, actor_roles={"admin"}, org_id=org,
                                      on=True, reason="drill")
            with pytest.raises(ProviderCallDenied, match="stopped"):
                await _governed_call(pool, org)
            await gov.set_kill_switch(pool, actor_subject="a", actor_user_id=None, actor_roles={"admin"}, org_id=org,
                                      on=False, reason="done")
            assert (await _governed_call(pool, org, prompt="SECRET-PROMPT")).text == "ok"   # allowed: no denial row
            page = await gov.list_denials(pool, actor_roles={"admin"}, org_id=org, since=window()[0], until=window()[1])
            assert {c["reason_code"]: c["count"] for c in page["counts_by_reason"]} == {
                "no_policy": 1, "model_not_allowed": 1, "data_class_not_allowed": 1, "kill_switch": 1}
            first = page["events"][0]
            assert (first["reason_code"], first["user_id"], first["tool"], first["unit"], first["provider"]) == (
                "no_policy", "u1", "call_model", "m1|direct", "example")
            assert "SECRET-PROMPT" not in str(page)                                       # no content anywhere
        finally:
            await pool.close()
    run(main())


def test_a_budget_refusal_is_recorded_and_a_denial_row_cannot_be_changed_or_deleted():
    from app.providers.types import ProviderCallDenied

    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            await set_policy(pool, org, monthly="0.0001", daily="0.0001", allowed_models=["m1"])   # below one call's worst case
            with pytest.raises(ProviderCallDenied, match="budget"):
                await _governed_call(pool, org)
            page = await gov.list_denials(pool, actor_roles={"admin"}, org_id=org, since=window()[0], until=window()[1])
            assert page["counts_by_reason"] == [{"reason_code": "monthly_budget_exceeded", "count": 1}]
            row_id = page["events"][0]["id"]
            async with tenant_transaction(pool, TenantScope.for_tenant(org)) as conn:
                with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="append-only"):
                    await conn.execute("UPDATE org_denials SET reason_code = 'kill_switch' WHERE id = $1::uuid", row_id)
            async with tenant_transaction(pool, TenantScope.for_tenant(org)) as conn:
                with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="append-only"):
                    await conn.execute("DELETE FROM org_denials WHERE id = $1::uuid", row_id)
        finally:
            await pool.close()
    run(main())


def test_calls_list_pages_by_cursor_filters_and_carries_timing_without_content():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            await set_policy(pool, org, allowed_models=["m1", "m2"])
            for actor in ("alice", "bob", "alice"):
                await _governed_call(pool, org, actor=actor, prompt="SECRET-PROMPT")
            await _governed_call(pool, org, actor="alice", unit="m2|direct")
            common = dict(actor_roles={"admin"}, org_id=org, since=window()[0], until=window()[1])
            everything = await gov.list_calls(pool, **common)
            assert len(everything["calls"]) == 4 and everything["next_after"] is None
            assert "SECRET-PROMPT" not in str(everything) and all(c["policy_decision"] == "allowed" for c in everything["calls"])
            first = everything["calls"][0]
            assert (first["provider_ms"], first["tier"], first["tokens_cache_read"], first["model"]) == (40, "light", 300, "m1")
            assert isinstance(first["gate_ms"], int)
            seen, after = [], None
            while True:                                                                   # page two at a time
                page = await gov.list_calls(pool, limit=2, after=after, **common)
                seen += [c["id"] for c in page["calls"]]
                after = page["next_after"]
                if after is None:
                    break
            assert seen == [c["id"] for c in everything["calls"]]
            assert len((await gov.list_calls(pool, user="alice", **common))["calls"]) == 3
            assert [c["model"] for c in (await gov.list_calls(pool, model="m2", **common))["calls"]] == ["m2"]
            assert (await gov.list_calls(pool, status="failed", **common))["calls"] == []
        finally:
            await pool.close()
    run(main())


def test_usage_per_user_and_the_budget_position_per_user():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            await set_policy(pool, org, monthly="10", daily="5", allowed_models=["m1"])
            for actor in ("alice", "alice", "bob"):
                await _governed_call(pool, org, actor=actor)
            held = await gov.reserve_call(pool, **call_args(org, actor="alice", worst=0.25))        # a live hold
            rows = await gov.usage_by_user(pool, actor_roles={"admin"}, org_id=org, since=window()[0], until=window()[1])
            by_user = {r["actor_subject"]: r for r in rows}
            assert by_user["alice"]["calls"] == 2 and by_user["bob"]["calls"] == 1
            assert by_user["alice"]["cost_usd"] == Decimal("0.004000") and by_user["bob"]["cost_usd"] == Decimal("0.002000")
            status = await gov.budget_status(pool, actor_roles={"admin"}, org_id=org)
            assert status["monthly"]["budget_usd"] == Decimal("10.0000") and status["monthly"]["used_usd"] == Decimal("0.006000")
            assert status["monthly"]["held_usd"] == Decimal("0.250000")
            assert status["monthly"]["remaining_usd"] == Decimal("10.0000") - Decimal("0.006000") - Decimal("0.250000")
            users = {u["user_id"]: u for u in status["users_today"]}
            assert users["alice"]["used_usd"] == Decimal("0.004000") and users["alice"]["held_usd"] == Decimal("0.250000")
            assert users["bob"]["remaining_usd"] == Decimal("5.0000") - Decimal("0.002000")
            await gov.fail_call(pool, held, error_type="x")
        finally:
            await pool.close()
    run(main())


def test_budget_status_needs_a_policy():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            with pytest.raises(gov.PolicyMissing):
                await gov.budget_status(pool, actor_roles={"admin"}, org_id=org)
        finally:
            await pool.close()
    run(main())


def test_the_range_summary_gives_true_merged_percentiles_per_group():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            await set_policy(pool, org)

            async def settle(latency, gate, tier):
                res = await gov.reserve_call(pool, **call_args(org, worst=0.01, actor=f"u{latency}"))
                await gov.settle_call(pool, res, tokens_input_fresh=1, tokens_cache_read=1, tokens_cache_write=0,
                                      tokens_output=1, cost_usd=0.001, cost_source="declared", latency_ms=latency,
                                      gate_ms=gate, tier=tier)
            for latency, gate in ((100, 5), (200, 10), (300, 15), (400, 20)):
                await settle(latency, gate, "light" if latency < 400 else "flagship")
            args = dict(actor_roles={"admin"}, org_id=org, since=window()[0], until=window()[1])
            total = (await gov.performance_summary(pool, group_by="total", **args))[0]
            assert total["key"] == "all" and total["calls"] == 4
            assert (total["provider_p50_ms"], total["provider_p95_ms"]) == (pytest.approx(250.0), pytest.approx(385.0))
            by_tier = {r["key"]: r for r in await gov.performance_summary(pool, group_by="tier", **args)}
            assert by_tier["light"]["calls"] == 3 and by_tier["light"]["provider_p50_ms"] == pytest.approx(200.0)
            assert by_tier["flagship"]["calls"] == 1 and by_tier["flagship"]["gate_p50_ms"] == pytest.approx(20.0)
            by_model = await gov.performance_summary(pool, group_by="model", **args)
            assert [r["key"] for r in by_model] == ["m1"] and by_model[0]["calls"] == 4
        finally:
            await pool.close()
    run(main())


def test_denials_and_per_user_usage_obey_row_level_security(app_role):
    from app.providers.types import ProviderCallDenied

    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org_a, org_b = await new_org(pool), await new_org(pool)
            for org in (org_a, org_b):
                await set_policy(pool, org, allowed_models=["m1"])
                await _governed_call(pool, org)
                with pytest.raises(ProviderCallDenied):
                    await _governed_call(pool, org, unit="m2|direct")
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(f"SET LOCAL ROLE {app_role}")
                    await conn.execute("SELECT set_config('app.tenant_id', $1, TRUE)", org_a)
                    for table in ("org_denials", "v_org_usage_by_user_daily"):
                        seen = {str(r["organization_id"]) for r in await conn.fetch(f"SELECT * FROM {table}")}
                        assert seen == {org_a}, f"{table} leaked another organization's rows"
        finally:
            await pool.close()
    run(main())


def test_members_lists_current_members_with_roles_and_skips_expired_ones():
    async def main():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        try:
            org = await new_org(pool)
            owner_role = await pool.fetchval("SELECT id FROM roles WHERE name = 'owner'")
            member_role = await pool.fetchval("SELECT id FROM roles WHERE name = 'member'")
            tag = uuid.uuid4().hex[:8]

            async def person(name, roles, *, expired=False):
                uid = await pool.fetchval(
                    "INSERT INTO users (issuer, external_subject, display_name, email) VALUES ($1, $2, $3, $4) "
                    "RETURNING id", f"iss-{tag}", f"sub-{tag}-{name}", name, f"{name}@example.com")
                for role in roles:
                    await pool.execute(
                        "INSERT INTO org_memberships (organization_id, user_id, role_id, t_expired) "
                        "VALUES ($1::uuid, $2, $3, CASE WHEN $4 THEN now() END)", org, uid, role, expired)
                return f"sub-{tag}-{name}"
            ada = await person("Ada", [member_role, owner_role])
            await person("Gone", [member_role], expired=True)
            bob = await person("bob", [member_role])
            rows = await gov.list_members(pool, actor_roles={"admin"}, org_id=org)
            assert [(r["display_name"], r["roles"]) for r in rows] == [("Ada", ["owner", "member"]), ("bob", ["member"])]
            assert {r["subject"] for r in rows} == {ada, bob} and rows[0]["email"] == "Ada@example.com"
            with pytest.raises(gov.NotAuthorized):
                await gov.list_members(pool, actor_roles={"member"}, org_id=org)
        finally:
            await pool.close()
    run(main())
