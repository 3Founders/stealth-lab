"""One-run purchases: the gate, bind/consume, activation, refunds, and the price.

Offline: every database call is an AsyncMock with the rows the service would see. The important
assertions are the REFUSALS (unpurchased, already used, already in a run) and that a purchase is
consumed only when the run reaches done.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.execution import way_runs
from app.services import procedure_billing as pb

BUYER = str(uuid4())
ROW = str(uuid4())
FAMILY = str(uuid4())
PURCHASE = str(uuid4())


def procedure(price=15000, currency="INR"):
    return {"id": ROW, "procedure_id": FAMILY, "version": 3, "name": "Refund a payment",
            "goal": "refund", "price_paise": price, "price_currency": currency,
            "availability": "active", "visibility": "public", "owner_id": "author"}


def pool_for(*, price_row, purchase=None, active=None, used=None, inserted="purchase-1", rows=()):
    pool = AsyncMock()
    async def fetchrow(sql, *args):
        if "FROM goal_submissions" in sql:
            return None
        if "FROM procedures" in sql:
            return price_row
        if "FROM procedure_purchases" in sql:
            if "status IN ('pending', 'paid')" in sql:
                return active
            return purchase
        return None
    pool.fetchrow.side_effect = fetchrow
    pool.fetch.return_value = list(rows)
    pool.fetchval.return_value = inserted if used is None else used
    return pool


# ------------------------------------------------------------------ gate

@pytest.mark.asyncio
async def test_entitlement_free_way_never_queries_a_purchase():
    pool = pool_for(price_row={"price_paise": None, "price_currency": "INR"})
    assert await pb.check_entitlement(pool, procedure_row_id=ROW, buyer_user_id=BUYER) is None
    pool.fetch.assert_not_called()
    pool.fetchval.assert_not_called()


@pytest.mark.asyncio
async def test_entitlement_returns_a_paid_unconsumed_purchase():
    purchase = {"id": PURCHASE, "status": "paid", "consumed_at": None, "used_by_run_id": None}
    pool = pool_for(price_row={"price_paise": 15000, "price_currency": "INR"}, purchase=purchase)
    got = await pb.check_entitlement(pool, procedure_row_id=ROW, buyer_user_id=BUYER)
    assert got and got["id"] == PURCHASE


@pytest.mark.asyncio
async def test_entitlement_refuses_an_unpurchased_paid_way():
    pool = pool_for(price_row={"price_paise": 15000, "price_currency": "INR"}, used=None)
    pool.fetchval.return_value = None
    with pytest.raises(pb.ProcedureNotPurchased) as caught:
        await pb.check_entitlement(pool, procedure_row_id=ROW, buyer_user_id=BUYER)
    assert caught.value.price_paise == 15000
    assert caught.value.status_code == 402
    assert "one output" in str(caught.value)


@pytest.mark.asyncio
async def test_entitlement_refuses_an_already_used_purchase():
    pool = pool_for(price_row={"price_paise": 15000, "price_currency": "INR"}, used=1)
    with pytest.raises(pb.ProcedureAlreadyUsed) as caught:
        await pb.check_entitlement(pool, procedure_row_id=ROW, buyer_user_id=BUYER)
    assert "already received" in str(caught.value)


@pytest.mark.asyncio
async def test_entitlement_refuses_a_purchase_already_producing_an_output():
    purchase = {"id": PURCHASE, "status": "paid", "consumed_at": None, "used_by_run_id": str(uuid4())}
    pool = pool_for(price_row={"price_paise": 15000, "price_currency": "INR"}, purchase=purchase)
    with pytest.raises(pb.ProcedureInUse):
        await pb.check_entitlement(pool, procedure_row_id=ROW, buyer_user_id=BUYER)


@pytest.mark.asyncio
async def test_purchase_status_paid_used_refunded_none():
    pool = pool_for(price_row=None)
    pool.fetch.return_value = [{"status": "paid", "consumed_at": None, "expires_at": None}]
    assert await pb.purchase_status(pool, procedure_row_id=ROW, buyer_user_id=BUYER) == "paid"
    pool.fetch.return_value = [{"status": "paid", "consumed_at": datetime.now(timezone.utc), "expires_at": None}]
    assert await pb.purchase_status(pool, procedure_row_id=ROW, buyer_user_id=BUYER) == "used"
    pool.fetch.return_value = [{"status": "refunded", "consumed_at": None, "expires_at": None}]
    assert await pb.purchase_status(pool, procedure_row_id=ROW, buyer_user_id=BUYER) == "refunded"
    pool.fetch.return_value = []
    assert await pb.purchase_status(pool, procedure_row_id=ROW, buyer_user_id=BUYER) == "none"
    pool.fetch.reset_mock()
    assert await pb.purchase_status(pool, procedure_row_id=ROW, buyer_user_id="local-user") == "none"
    pool.fetch.assert_not_called()


# ------------------------------------------------------------------ purchase

@pytest.mark.asyncio
async def test_purchase_creates_a_payment_link_for_one_output(monkeypatch):
    pool = pool_for(price_row=procedure(), active=None, inserted=PURCHASE)
    link = AsyncMock(return_value={"id": "plink_1", "short_url": "https://rzp.io/i/abc", "order_id": "order_1"})
    monkeypatch.setattr(pb, "create_payment_link", link)

    out = await pb.create_purchase(pool, procedure_row_id=ROW, buyer_user_id=BUYER,
                                   callback_url="https://www.kell.org.in/way/sub-1")

    assert out["checkout_url"] == "https://rzp.io/i/abc"
    assert out["purchase_status"] == "pending"
    assert out["price_paise"] == 15000
    link.assert_awaited_once()
    kwargs = link.await_args.kwargs
    assert kwargs["amount_paise"] == 15000
    assert kwargs["reference_id"] == PURCHASE
    assert kwargs["notes"]["purchase_id"] == PURCHASE
    pool.execute.assert_awaited()


@pytest.mark.asyncio
async def test_purchase_is_idempotent_for_a_paid_purchase(monkeypatch):
    pool = pool_for(price_row=procedure(),
                    active={"id": PURCHASE, "status": "paid", "payment_link_id": None,
                            "amount_paise": 15000, "procedure_version": 3})
    link = AsyncMock()
    monkeypatch.setattr(pb, "create_payment_link", link)

    out = await pb.create_purchase(pool, procedure_row_id=ROW, buyer_user_id=BUYER)
    assert out["already"] is True
    assert out["purchase_status"] == "paid"
    link.assert_not_called()


@pytest.mark.asyncio
async def test_purchase_refuses_a_free_way(monkeypatch):
    pool = pool_for(price_row=procedure(price=None))
    monkeypatch.setattr(pb, "create_payment_link", AsyncMock())
    with pytest.raises(pb.PurchaseError) as caught:
        await pb.create_purchase(pool, procedure_row_id=ROW, buyer_user_id=BUYER)
    assert caught.value.status_code == 400


@pytest.mark.asyncio
async def test_a_used_purchase_does_not_block_buying_another_output(monkeypatch):
    # The active query returns None (the old row is consumed); the insert makes a new purchase.
    pool = pool_for(price_row=procedure(), active=None, inserted=PURCHASE)
    link = AsyncMock(return_value={"id": "plink_2", "short_url": "https://rzp.io/i/def"})
    monkeypatch.setattr(pb, "create_payment_link", link)
    out = await pb.create_purchase(pool, procedure_row_id=ROW, buyer_user_id=BUYER)
    assert out["purchase_id"] == PURCHASE
    assert out["purchase_status"] == "pending"


# ------------------------------------------------------------------ bind / consume / refund

@pytest.mark.asyncio
async def test_bind_purchase_refuses_a_second_run():
    pool = AsyncMock()
    pool.fetchval.return_value = None
    with pytest.raises(pb.ProcedureInUse):
        await pb.bind_purchase(pool, purchase_id=PURCHASE, run_id=str(uuid4()))


@pytest.mark.asyncio
async def test_consume_and_release_are_scoped_to_the_run():
    pool = AsyncMock()
    await pb.consume_run(pool, "run-1")
    assert "consumed_at = now()" in pool.execute.await_args.args[0]
    await pb.release_run(pool, "run-1")
    assert "used_by_run_id = NULL" in pool.execute.await_args.args[0]


@pytest.mark.asyncio
async def test_activation_by_link_grants_once_and_checks_the_amount():
    pool = AsyncMock()
    pool.fetchrow.return_value = {"id": PURCHASE, "amount_paise": 15000, "status": "pending"}
    pool.fetchval.return_value = 1
    assert await pb.activate_by_link(pool, payment_link_id="plink_1", payment_id="pay_1",
                                     amount_paise=15000) is True
    assert "status = 'pending'" in pool.fetchval.await_args.args[0]

    pool.fetchval.reset_mock()
    pool.fetchrow.return_value = {"id": PURCHASE, "amount_paise": 15000, "status": "paid"}
    assert await pb.activate_by_link(pool, payment_link_id="plink_1", payment_id="pay_1",
                                     amount_paise=15000) is False
    pool.fetchval.assert_not_called()

    pool.fetchrow.return_value = {"id": PURCHASE, "amount_paise": 15000, "status": "pending"}
    assert await pb.activate_by_link(pool, payment_link_id="plink_1", payment_id="pay_1",
                                     amount_paise=100) is False


@pytest.mark.asyncio
async def test_refund_flips_the_purchase_to_refunded():
    pool = AsyncMock()
    pool.fetchval.return_value = PURCHASE
    assert await pb.refund_by_payment(pool, payment_id="pay_1") == PURCHASE
    sql = pool.fetchval.await_args.args[0]
    assert "status = 'refunded'" in sql
    assert "refunded_at = now()" in sql


@pytest.mark.asyncio
async def test_set_price_bounds_and_update():
    pool = AsyncMock()
    with pytest.raises(pb.PurchaseError):
        await pb.set_price(pool, procedure_row_id=ROW, price_paise=100_001)
    pool.fetchrow.return_value = {**procedure(), "price_paise": 15000}
    out = await pb.set_price(pool, procedure_row_id=ROW, price_paise=15000)
    assert out["price_paise"] == 15000
    assert "priced_at = now()" in pool.fetchrow.await_args.args[0]


@pytest.mark.asyncio
async def test_way_run_refuses_an_unpurchased_paid_way(monkeypatch):
    async def fake_way(pool, way_id, owner_id, **kwargs):
        return {**procedure(), "steps": []}
    monkeypatch.setattr(way_runs, "_way", fake_way)
    pool = pool_for(price_row={"price_paise": 15000, "price_currency": "INR"}, used=None)
    pool.fetchval.return_value = None
    with pytest.raises(pb.ProcedureNotPurchased):
        await way_runs.start(pool, ROW, owner_id=BUYER)
