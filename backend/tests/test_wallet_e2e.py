"""
The prepaid wallet against a real Postgres with every migration applied (db/156 included): money in, the balance check in
org_governance.reserve_call, idempotent crediting, own-wallet accounts (no organization row), plans, and the append-only guard.

Needs WALLET_TEST_DSN: a database that already has all migrations (python scripts/migrate.py against a scratch database).
It writes rows with random ids under its own organizations and does not clean up, so point it at a scratch database, never
at production. Skips otherwise, like the other *_e2e.py files.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import uuid
from decimal import Decimal

import pytest

asyncpg = pytest.importorskip("asyncpg")
from dotenv import dotenv_values  # noqa: E402

from app.services import org_governance as gov  # noqa: E402
from app.services import accounts, plans, wallet  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DSN = os.environ.get("WALLET_TEST_DSN") or dotenv_values(ROOT / ".env").get("WALLET_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="WALLET_TEST_DSN not set")

ENV = {"KELL_WALLETS": "on", "KELL_INR_PER_USD": "100", "KELL_MARGIN": "0.5"}


def run(coro_fn):
    async def main():
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
        try:
            return await coro_fn(pool)
        finally:
            await pool.close()
    return asyncio.run(main())


async def _user(pool):
    uid = str(uuid.uuid4())
    await pool.execute("INSERT INTO users (id, issuer, external_subject, email) VALUES ($1::uuid, 'test', $2, 'x@example.com')",
                       uid, "sub-" + uid)
    return uid, "sub-" + uid


async def _reserve(pool, org, subject, worst=0.01):
    return await gov.reserve_call(pool, org_id=org, actor_subject=subject, tool="call_model", unit="glm-5.3-flash|direct",
                                  connection_id="zai-glm", provider="zai", model="glm-5.3-flash", scaffold="direct",
                                  data_class="PUBLIC_SOURCE", instance_key=None, worst_case_usd=worst)


def test_a_new_person_gets_their_own_wallet_that_refuses_until_credit_arrives():
    async def body(pool):
        uid, sub = await _user(pool)
        assert await accounts.ensure_account(pool, user_id=uid, env=ENV)
        org = uid                                                    # a person's wallet account id is their user id
        assert await accounts.ensure_account(pool, user_id=uid, env=ENV)         # idempotent
        with pytest.raises(gov.BudgetExceeded) as refused:
            await _reserve(pool, org, sub)
        assert refused.value.code == "wallet_empty"
        # 100 rupees at 100 INR/USD with a 50% margin -> 1 / 1.5 dollars
        assert await wallet.credit_payment(pool, org_id=org, payment_ref="pay_" + uuid.uuid4().hex, amount_paise=10_000,
                                           created_by="test", env=ENV)
        bal = await wallet.balance(pool, org)
        assert Decimal(bal["purchased_usd"]) == Decimal("0.666666") and Decimal(bal["remaining_usd"]) == Decimal("0.666666")
        res = await _reserve(pool, org, sub, 0.5)                    # allowed: 0.5 <= 0.666666
        assert res.reserved_usd == Decimal("0.5")
        with pytest.raises(gov.BudgetExceeded):                      # 0.5 held, 0.166666 left, this needs 0.2
            await _reserve(pool, org, sub, 0.2)
        again = await wallet.balance(pool, org)
        assert Decimal(again["remaining_usd"]) == Decimal("0.166666")
    run(body)


def test_the_same_payment_credits_once_and_the_credits_cannot_be_edited():
    async def body(pool):
        uid, _ = await _user(pool)
        assert await accounts.ensure_account(pool, user_id=uid, env=ENV)
        org = uid
        ref = "pay_" + uuid.uuid4().hex
        assert await wallet.credit_payment(pool, org_id=org, payment_ref=ref, amount_paise=15_000, created_by="t", env=ENV)
        assert not await wallet.credit_payment(pool, org_id=org, payment_ref=ref, amount_paise=15_000, created_by="t", env=ENV)
        assert len((await wallet.balance(pool, org))["credits"]) == 1
        with pytest.raises(asyncpg.PostgresError):
            await pool.execute("DELETE FROM org_wallet_credits WHERE payment_ref = $1", ref)
        with pytest.raises(asyncpg.PostgresError):
            await pool.execute("UPDATE org_wallet_credits SET amount_usd = 999 WHERE payment_ref = $1", ref)
    run(body)


def test_an_org_without_wallet_required_is_unchanged():
    async def body(pool):
        org = str(uuid.uuid4())
        await pool.execute("INSERT INTO organizations (id, name, slug) VALUES ($1::uuid, 'plain', $2)", org, "plain-" + org[:8])
        await pool.execute(
            "INSERT INTO org_policies (organization_id, allowed_providers, allowed_models, allowed_tools, allowed_data_classes, "
            "monthly_budget_usd, per_user_daily_budget_usd, updated_by) VALUES ($1::uuid, '{zai}', '{glm-5.3-flash}', "
            "'{call_model}', '{PUBLIC_SOURCE}', 10, 10, 't')", org)
        res = await _reserve(pool, org, "sub-plain", 0.01)           # no wallet, no credits, still allowed by its budgets
        assert res.reserved_usd == Decimal("0.01")
    run(body)


def test_find_order_matches_only_orders_we_recorded():
    async def body(pool):
        uid, sub = await _user(pool)
        assert await accounts.ensure_account(pool, user_id=uid, env=ENV)
        org = uid
        oid = "order_" + uuid.uuid4().hex
        await wallet.record_order(pool, org_id=org, order_id=oid, amount_paise=20_000, created_by=sub)
        found = await wallet.find_order(pool, oid)
        assert found["organization_id"] == org and int(found["amount_paise"]) == 20_000
        assert await wallet.find_order(pool, "order_unknown") is None
    run(body)


def _order(plan, seats=1, team=None, period=30, buyer=None, oid=None):
    return {"plan": plan, "seats": seats, "team_name": team, "buyer_user_id": buyer, "period_days": period,
            "order_id": oid or "order_" + uuid.uuid4().hex}


def test_a_pro_payment_starts_the_plan_credits_six_dollars_once_and_a_renewal_extends_from_the_end():
    async def body(pool):
        uid, sub = await _user(pool)
        pay = "pay_" + uuid.uuid4().hex
        order = _order("pro", buyer=uid)
        assert await plans.activate(pool, order=order, payment_id=pay, amount_paise=120_000)
        assert not await plans.activate(pool, order=order, payment_id=pay, amount_paise=120_000)       # a repeat does nothing
        bal = await wallet.balance(pool, uid)
        assert Decimal(bal["purchased_usd"]) == Decimal("6")
        first = await plans.current(pool, uid)
        assert first["plan"] == "pro" and first["seats"] == 1 and first["team_name"] is None
        assert await plans.activate(pool, order=_order("pro", buyer=uid), payment_id="pay_" + uuid.uuid4().hex, amount_paise=120_000)
        second = await plans.current(pool, uid)
        assert second["active_until"] > first["active_until"]                                          # renewed from the end
        res = await _reserve(pool, uid, sub, 0.5)                                                     # the included usage can be spent
        assert res.reserved_usd == Decimal("0.5")
    run(body)


def test_a_team_pilot_creates_a_real_organization_owned_by_the_buyer_and_credits_four_dollars_a_seat():
    async def body(pool):
        uid, _ = await _user(pool)
        pay = "pay_" + uuid.uuid4().hex
        order = _order("pilot", seats=3, team="Acme pilot", period=14, buyer=uid)
        assert await plans.activate(pool, order=order, payment_id=pay, amount_paise=3 * 64_900)
        assert not await plans.activate(pool, order=order, payment_id=pay, amount_paise=3 * 64_900)
        cur = await plans.current(pool, uid)
        assert cur["plan"] == "pilot" and cur["seats"] == 3 and cur["team_name"] == "Acme pilot"
        org = await pool.fetchval("SELECT id::text FROM organizations WHERE slug = $1", plans.team_slug(pay))
        assert org and org != uid
        assert await pool.fetchval("SELECT 1 FROM org_memberships WHERE organization_id = $1::uuid AND user_id = $2::uuid", org, uid)
        assert Decimal((await wallet.balance(pool, org))["purchased_usd"]) == Decimal("12")
        assert await plans.has_pilot(pool, uid)
    run(body)


def test_a_one_seat_pilot_is_an_individual_account_with_no_organization():
    async def body(pool):
        uid, _ = await _user(pool)
        pay = "pay_" + uuid.uuid4().hex
        assert await plans.activate(pool, order=_order("pilot", seats=1, period=14, buyer=uid), payment_id=pay, amount_paise=64_900)
        assert Decimal((await wallet.balance(pool, uid))["purchased_usd"]) == Decimal("4")
        assert await pool.fetchval("SELECT 1 FROM organizations WHERE slug = $1", plans.team_slug(pay)) is None
    run(body)


def test_an_unknown_account_id_is_refused_by_the_database():
    async def body(pool):
        with pytest.raises(asyncpg.PostgresError):
            await pool.execute(
                "INSERT INTO org_wallet_credits (organization_id, payment_ref, amount_paise, amount_usd, created_by) "
                "VALUES ($1::uuid, $2, 100, 1, 't')", str(uuid.uuid4()), "pay_" + uuid.uuid4().hex)
    run(body)


def test_every_tier_can_be_bought_and_the_page_shows_the_active_higher_tier_before_a_lower_or_ended_one():
    async def body(pool):
        uid, _ = await _user(pool)
        assert await plans.current(pool, uid) is None
        for plan, inr, usd in (("starter", 200, "1"), ("plus", 2000, "12")):
            assert await plans.activate(pool, order=_order(plan, buyer=uid), payment_id="pay_" + uuid.uuid4().hex,
                                        amount_paise=inr * 100)
        assert (await plans.current(pool, uid))["plan"] == "plus"                       # the higher active tier wins
        assert Decimal((await wallet.balance(pool, uid))["purchased_usd"]) == Decimal("13")
    run(body)


def test_the_database_refuses_a_plan_name_that_is_not_on_the_ladder():
    async def body(pool):
        uid, _ = await _user(pool)
        with pytest.raises(asyncpg.PostgresError):
            await pool.execute(
                "INSERT INTO plan_entitlements (buyer_user_id, account_id, plan, seats, starts_at, ends_at, payment_ref, "
                "amount_paise) VALUES ($1::uuid, $1::uuid, 'free', 1, now(), now() + interval '1 day', $2, 100)",
                uid, "pay_" + uuid.uuid4().hex)
    run(body)


def test_reconcile_finds_a_captured_plan_payment_whose_webhook_never_came_and_a_second_run_does_nothing():
    from app.services import billing_reconcile as rec

    async def body(pool):
        uid, sub = await _user(pool)
        oid = "order_" + uuid.uuid4().hex
        await wallet.record_order(pool, org_id=uid, order_id=oid, amount_paise=200_000, created_by=sub, plan="plus",
                                  seats=1, team_name=None, buyer_user_id=uid, period_days=30)
        await pool.execute("UPDATE billing_orders SET created_at = now() - interval '1 hour' WHERE order_id = $1", oid)

        async def fetch(order_id):
            return [{"id": "pay_" + oid, "status": "captured", "currency": "INR", "amount": 200_000}] if order_id == oid else []

        young = await rec.reconcile(pool, fetch_payments=fetch, apply=True, older_than_minutes=5000)    # nothing is old enough
        assert young["checked"] == 0
        first = await rec.reconcile(pool, fetch_payments=fetch, apply=True)
        assert first["activated"] >= 1 and (await plans.current(pool, uid))["plan"] == "plus"
        again = await rec.reconcile(pool, fetch_payments=fetch, apply=True)                              # already active: not asked again
        assert again["activated"] == 0
        assert Decimal((await wallet.balance(pool, uid))["purchased_usd"]) == Decimal("12")
    run(body)
