"""Per-procedure billing: the gate, the purchase, activation and refunds (docs/procedure_billing.md).

Offline: every database call is an AsyncMock with the rows the service would see. The important
assertions are the REFUSALS (an unpurchased paid Way, a refunded licence) and idempotency, not the
happy path alone.
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


def procedure(price=15000, currency="INR"):
    return {"id": ROW, "procedure_id": FAMILY, "version": 3, "name": "Refund a payment",
            "goal": "refund", "price_paise": price, "price_currency": currency,
            "availability": "active", "visibility": "public", "owner_id": "author"}


def pool_for(*, price_row, licences=(), existing=None, inserted="licence-1"):
    pool = AsyncMock()
    async def fetchrow(sql, *args):
        if "FROM goal_submissions" in sql:
            return None
        if "FROM procedures" in sql:
            return price_row
        if "FROM procedure_licences" in sql:
            return existing
        return None
    pool.fetchrow.side_effect = fetchrow
    pool.fetch.return_value = list(licences)
    pool.fetchval.return_value = inserted
    return pool


# ------------------------------------------------------------------ entitlement

@pytest.mark.asyncio
async def test_entitlement_free_way_never_queries_a_licence():
    pool = pool_for(price_row={"price_paise": None, "price_currency": "INR"})
    await pb.check_entitlement(pool, procedure_row_id=ROW, buyer_user_id=BUYER)
    pool.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_entitlement_refuses_an_unpurchased_paid_way():
    pool = pool_for(price_row={"price_paise": 15000, "price_currency": "INR"}, licences=[])
    with pytest.raises(pb.ProcedureNotPurchased) as caught:
        await pb.check_entitlement(pool, procedure_row_id=ROW, buyer_user_id=BUYER)
    assert caught.value.price_paise == 15000
    assert caught.value.status_code == 402
    assert "150.00" in str(caught.value)


@pytest.mark.asyncio
async def test_entitlement_allows_a_paid_unexpired_licence():
    pool = pool_for(price_row={"price_paise": 15000, "price_currency": "INR"},
                    licences=[{"status": "paid", "expires_at": None}])
    await pb.check_entitlement(pool, procedure_row_id=ROW, buyer_user_id=BUYER)


@pytest.mark.asyncio
async def test_entitlement_refuses_refunded_and_expired():
    for rows in ([{"status": "refunded", "expires_at": None}],
                 [{"status": "paid", "expires_at": datetime.now(timezone.utc) - timedelta(days=1)}]):
        pool = pool_for(price_row={"price_paise": 15000, "price_currency": "INR"}, licences=rows)
        with pytest.raises(pb.ProcedureNotPurchased):
            await pb.check_entitlement(pool, procedure_row_id=ROW, buyer_user_id=BUYER)


@pytest.mark.asyncio
async def test_licence_status_reports_none_purchased_refunded():
    pool = pool_for(price_row=None)
    pool.fetch.return_value = []
    assert await pb.licence_status(pool, procedure_row_id=ROW, buyer_user_id=BUYER) == "none"
    pool.fetch.return_value = [{"status": "paid", "expires_at": None}]
    assert await pb.licence_status(pool, procedure_row_id=ROW, buyer_user_id=BUYER) == "purchased"
    pool.fetch.return_value = [{"status": "refunded", "expires_at": None}]
    assert await pb.licence_status(pool, procedure_row_id=ROW, buyer_user_id=BUYER) == "refunded"
    # A non-UUID identity (the local operator) is never a buyer and never queries.
    pool.fetch.reset_mock()
    assert await pb.licence_status(pool, procedure_row_id=ROW, buyer_user_id="local-user") == "none"
    pool.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_way_run_refuses_an_unpurchased_paid_way(monkeypatch):
    async def fake_way(pool, way_id, owner_id, **kwargs):
        return {**procedure(), "steps": []}
    monkeypatch.setattr(way_runs, "_way", fake_way)
    pool = pool_for(price_row={"price_paise": 15000, "price_currency": "INR"}, licences=[])
    with pytest.raises(pb.ProcedureNotPurchased):
        await way_runs.start(pool, ROW, owner_id=BUYER)


# ------------------------------------------------------------------ purchase

@pytest.mark.asyncio
async def test_purchase_creates_a_payment_link_for_the_exact_version(monkeypatch):
    pool = pool_for(price_row=procedure())
    link = AsyncMock(return_value={"id": "plink_1", "short_url": "https://rzp.io/i/abc", "order_id": "order_1"})
    monkeypatch.setattr(pb, "create_payment_link", link)

    out = await pb.create_purchase(pool, procedure_row_id=ROW, buyer_user_id=BUYER,
                                   callback_url="https://www.kell.org.in/way/sub-1")

    assert out["checkout_url"] == "https://rzp.io/i/abc"
    assert out["price_paise"] == 15000
    assert out["version"] == 3
    link.assert_awaited_once()
    kwargs = link.await_args.kwargs
    assert kwargs["amount_paise"] == 15000
    assert kwargs["reference_id"] == "licence-1"
    assert kwargs["notes"]["procedure_row_id"] == ROW
    pool.execute.assert_awaited()


@pytest.mark.asyncio
async def test_purchase_is_idempotent_for_a_paid_licence(monkeypatch):
    pool = pool_for(price_row=procedure(),
                    existing={"id": "licence-1", "status": "paid", "payment_link_id": None,
                              "amount_paise": 15000, "procedure_version": 3})
    link = AsyncMock()
    monkeypatch.setattr(pb, "create_payment_link", link)

    out = await pb.create_purchase(pool, procedure_row_id=ROW, buyer_user_id=BUYER)
    assert out["already"] is True
    assert out["licence_status"] == "purchased"
    link.assert_not_called()


@pytest.mark.asyncio
async def test_purchase_refuses_a_free_way(monkeypatch):
    pool = pool_for(price_row=procedure(price=None))
    monkeypatch.setattr(pb, "create_payment_link", AsyncMock())
    with pytest.raises(pb.PurchaseError) as caught:
        await pb.create_purchase(pool, procedure_row_id=ROW, buyer_user_id=BUYER)
    assert caught.value.status_code == 400


# ------------------------------------------------------------------ activation / refund

@pytest.mark.asyncio
async def test_activation_by_link_grants_once_and_checks_the_amount():
    pool = AsyncMock()
    pool.fetchrow.return_value = {"id": "licence-1", "amount_paise": 15000, "status": "pending"}
    pool.fetchval.return_value = 1
    assert await pb.activate_by_link(pool, payment_link_id="plink_1", payment_id="pay_1",
                                     amount_paise=15000) is True
    assert "status = 'paid'" in pool.fetchval.await_args.args[0]

    pool.fetchval.reset_mock()
    pool.fetchrow.return_value = {"id": "licence-1", "amount_paise": 15000, "status": "paid"}
    assert await pb.activate_by_link(pool, payment_link_id="plink_1", payment_id="pay_1",
                                     amount_paise=15000) is False
    pool.fetchval.assert_not_called()

    pool.fetchrow.return_value = {"id": "licence-1", "amount_paise": 15000, "status": "pending"}
    assert await pb.activate_by_link(pool, payment_link_id="plink_1", payment_id="pay_1",
                                     amount_paise=100) is False


@pytest.mark.asyncio
async def test_refund_flips_the_licence_to_refunded():
    pool = AsyncMock()
    pool.fetchval.return_value = "licence-1"
    assert await pb.refund_by_payment(pool, payment_id="pay_1") == "licence-1"
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
