"""
The prepaid wallet against a real Postgres with every migration applied (db/156 included): money in, the balance check in
org_governance.reserve_call, idempotent crediting, personal organizations, and the append-only guard.

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
from app.services import personal_org, wallet  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DSN = os.environ.get("WALLET_TEST_DSN") or dotenv_values(ROOT / ".env").get("WALLET_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="WALLET_TEST_DSN not set")

ENV = {"KELL_PERSONAL_ORGS": "on", "KELL_INR_PER_USD": "100", "KELL_MARGIN": "0.5"}


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


def test_a_new_person_gets_a_personal_org_that_refuses_until_credit_arrives():
    async def body(pool):
        uid, sub = await _user(pool)
        org = await personal_org.ensure_personal_org(pool, user_id=uid, env=ENV)
        assert org
        assert await personal_org.ensure_personal_org(pool, user_id=uid, org_ids=(org,), env=ENV) is None   # idempotent
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
        org = await personal_org.ensure_personal_org(pool, user_id=uid, env=ENV)
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
        org = await personal_org.ensure_personal_org(pool, user_id=uid, env=ENV)
        oid = "order_" + uuid.uuid4().hex
        await wallet.record_order(pool, org_id=org, order_id=oid, amount_paise=20_000, created_by=sub)
        found = await wallet.find_order(pool, oid)
        assert found["organization_id"] == org and int(found["amount_paise"]) == 20_000
        assert await wallet.find_order(pool, "order_unknown") is None
    run(body)
