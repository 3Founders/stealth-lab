"""Auto-renew without a database or network: the Razorpay plan is cached per amount/period, a subscription records the
exact amount every charge must be, a charge activates the plan only at that amount, and cancelling stops it."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.services import subscriptions

BUYER = "11111111-1111-4111-8111-111111111111"
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
ROWS = [{"plan": "pro", "price_paise": 120_000, "period_days": 30, "included_usd": Decimal("6"), "active": True, "max_redemptions": None}]


class FakeConn:
    def __init__(self, answers):
        self.answers, self.log = answers, []

    def _find(self, sql):
        for needle, value in self.answers.items():
            if needle in sql:
                return value() if callable(value) else value
        return None

    async def execute(self, sql, *a):
        self.log.append((sql, a))
        return self._find(sql)

    async def fetch(self, sql, *a):
        self.log.append((sql, a))
        return self._find(sql) or []

    async def fetchrow(self, sql, *a):
        self.log.append((sql, a))
        return self._find(sql)

    async def fetchval(self, sql, *a):
        self.log.append((sql, a))
        return self._find(sql)

    def transaction(self):
        @asynccontextmanager
        async def tx():
            yield
        return tx()


class FakePool:
    def __init__(self, answers=None):
        self.conn = FakeConn(answers or {})

    @asynccontextmanager
    async def acquire(self):
        yield self.conn

    def statements(self, needle):
        return [(s, a) for s, a in self.conn.log if needle in s]


def razorpay(monkeypatch, calls):
    async def fake_post(path, body):
        calls.append((path, body))
        return {"id": "plan_x"} if path == "/plans" else ({"id": "sub_x"} if path == "/subscriptions" else {})
    monkeypatch.setattr(subscriptions, "_post", fake_post)


@pytest.mark.asyncio
async def test_a_subscription_is_recorded_with_the_exact_charge(monkeypatch):
    calls: list = []
    razorpay(monkeypatch, calls)
    pool = FakePool({"SELECT plan, price_paise": ROWS})
    out = await subscriptions.create(pool, buyer_user_id=BUYER, plan="pro", seats=1, referral_code=None)
    assert out == {"subscription_id": "sub_x", "plan": "pro", "seats": 1, "period_days": 30, "amount_paise": 120_000}
    plan_body = [b for p, b in calls if p == "/plans"][0]
    assert plan_body["period"] == "daily" and plan_body["interval"] == 30 and plan_body["item"]["amount"] == 120_000
    sub_body = [b for p, b in calls if p == "/subscriptions"][0]
    assert sub_body["plan_id"] == "plan_x" and sub_body["quantity"] == 1 and sub_body["customer_notify"] is True
    _sql, args = pool.statements("INSERT INTO billing_subscriptions")[0]
    assert args[0] == BUYER and args[1] == "pro" and args[4] == 120_000 and args[5] == 30 and args[6] == "sub_x"


@pytest.mark.asyncio
async def test_the_one_person_plan_and_a_plan_that_is_not_on_sale_are_refused(monkeypatch):
    monkeypatch.setattr(subscriptions, "_post", None)                     # nothing may reach the provider
    with pytest.raises(subscriptions.SubscriptionError):
        await subscriptions.create(FakePool({"SELECT plan, price_paise": ROWS}), buyer_user_id=BUYER, plan="pilot", seats=1, referral_code="X")
    off = [dict(ROWS[0], active=False)]
    with pytest.raises(subscriptions.SubscriptionError):
        await subscriptions.create(FakePool({"SELECT plan, price_paise": off}), buyer_user_id=BUYER, plan="pro", seats=1, referral_code=None)


@pytest.mark.asyncio
async def test_a_matching_razorpay_plan_is_reused_and_a_changed_price_makes_a_new_one(monkeypatch):
    calls: list = []
    razorpay(monkeypatch, calls)
    cached = {"SELECT rzp_plan_id": {"rzp_plan_id": "plan_cached", "rzp_plan_paise": 120_000, "rzp_plan_days": 30},
              "SELECT plan, price_paise": ROWS}
    await subscriptions.create(FakePool(cached), buyer_user_id=BUYER, plan="pro", seats=1, referral_code=None)
    assert [p for p, _ in calls] == ["/subscriptions"]                    # no new plan made

    stale = {"SELECT rzp_plan_id": {"rzp_plan_id": "plan_old", "rzp_plan_paise": 120_000, "rzp_plan_days": 45},
             "SELECT plan, price_paise": ROWS}
    calls.clear()
    await subscriptions.create(FakePool(stale), buyer_user_id=BUYER, plan="pro", seats=1, referral_code=None)
    assert [p for p, _ in calls] == ["/plans", "/subscriptions"]          # the period changed: a new Razorpay plan


@pytest.mark.asyncio
async def test_cancel_stops_the_mandate_and_a_missing_one_is_refused(monkeypatch):
    calls: list = []
    razorpay(monkeypatch, calls)
    pool = FakePool({"FROM billing_subscriptions WHERE buyer_user_id": {"razorpay_subscription_id": "sub_x"}})
    assert await subscriptions.cancel(pool, BUYER) == {"cancelled": True}
    assert calls[-1][0] == "/subscriptions/sub_x/cancel"
    assert pool.statements("UPDATE billing_subscriptions SET status = 'cancelled'")
    with pytest.raises(subscriptions.SubscriptionError):
        await subscriptions.cancel(FakePool({}), BUYER)


@pytest.mark.asyncio
async def test_the_running_subscription_is_read_back():
    row = {"plan": "pro", "seats": 1, "status": "active", "current_end": NOW, "amount_paise": 120_000, "period_days": 30}
    got = await subscriptions.by_buyer(FakePool({"FROM billing_subscriptions WHERE buyer_user_id": row}), BUYER)
    assert got == {"plan": "pro", "seats": 1, "status": "active", "current_end": NOW.isoformat(),
                   "amount_paise": 120_000, "period_days": 30}
    assert await subscriptions.by_buyer(FakePool({}), BUYER) is None


def _charge_pool(amount: int) -> FakePool:
    return FakePool({
        "FROM billing_subscriptions WHERE razorpay_subscription_id": {
            "buyer_user_id": BUYER, "plan": "pro", "seats": 1, "amount_paise": amount, "period_days": 30, "status": "created"},
        "FROM plan_entitlements WHERE payment_ref": None,
        "max(ends_at)": None,
        "SELECT plan, price_paise": ROWS,
        "INSERT INTO plan_entitlements": 1,
        "FROM billing_referral_codes WHERE owner": "KELTEST1",
    })


def _body(amount: int, event: str = "subscription.charged") -> dict:
    return {"event": event, "payload": {"subscription": {"entity": {"id": "sub_x"}},
                                        "payment": {"entity": {"id": "pay_1", "amount": amount, "currency": "INR"}}}}


@pytest.mark.asyncio
async def test_a_charge_activates_the_plan_and_extends_the_subscription():
    pool = _charge_pool(120_000)
    await subscriptions.on_event(pool, "subscription.charged", _body(120_000))
    assert pool.statements("INSERT INTO plan_entitlements")[0][1][7] == "pay_1"
    upd = pool.statements("UPDATE billing_subscriptions SET status = 'active'")
    assert upd and upd[0][1][0] == "sub_x" and upd[0][1][1] == 30


@pytest.mark.asyncio
async def test_a_charge_at_another_amount_is_not_credited():
    pool = _charge_pool(120_000)
    await subscriptions.on_event(pool, "subscription.charged", _body(100_000))
    assert not pool.statements("INSERT INTO plan_entitlements")


@pytest.mark.asyncio
async def test_the_first_authorisation_payment_activates_once_and_marks_the_subscription():
    pool = _charge_pool(120_000)
    await subscriptions.on_event(pool, "subscription.authenticated", _body(120_000, "subscription.authenticated"))
    assert len(pool.statements("INSERT INTO plan_entitlements")) == 1
    assert pool.statements("UPDATE billing_subscriptions SET status = $2")


@pytest.mark.asyncio
async def test_an_authenticated_event_without_a_payment_marks_only_and_never_downgrades_an_active_row():
    # Razorpay's authenticated payload usually has no payment entity (docs sample), and may arrive after charged.
    pool = _charge_pool(120_000)
    await subscriptions.on_event(pool, "subscription.authenticated", {"event": "subscription.authenticated", "payload": {"subscription": {"entity": {"id": "sub_x"}}}})
    assert not pool.statements("INSERT INTO plan_entitlements")
    assert pool.statements("UPDATE billing_subscriptions SET status = $2")

    active = _charge_pool(120_000)
    active.conn.answers["FROM billing_subscriptions WHERE razorpay_subscription_id"] = {
        "buyer_user_id": BUYER, "plan": "pro", "seats": 1, "amount_paise": 120_000, "period_days": 30, "status": "active"}
    await subscriptions.on_event(active, "subscription.authenticated", {"event": "subscription.authenticated", "payload": {"subscription": {"entity": {"id": "sub_x"}}}})
    assert not active.statements("UPDATE billing_subscriptions SET status = $2")
