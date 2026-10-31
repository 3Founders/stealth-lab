"""Additional kell credits (fixed packs) without a database: the price list, that a plan is always the better buy, the gate for
people with no active plan, and that a pack credits its fixed dollars (not the exchange-rate formula)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal

import pytest

from app.services import packs, plans, wallet

BUYER = "11111111-1111-4111-8111-111111111111"


def test_the_price_list_is_four_fixed_packs_and_the_server_prices_them():
    assert list(packs.PACKS) == ["credits_500", "credits_1000", "credits_2500", "credits_5000"]
    o = packs.parse_pack_order({"pack": "credits_1000", "amount_inr": 1, "price": 1})
    assert o.amount_paise == 100_000 and o.usd == Decimal("5.25")           # a price in the request changes nothing


@pytest.mark.parametrize("body", [None, [], "credits_500", {}, {"pack": "credits_999"}, {"pack": 500}, {"pack": "CREDITS_500"}])
def test_an_unknown_pack_is_refused(body):
    with pytest.raises(plans.PlanError):
        packs.parse_pack_order(body)


def test_every_pack_costs_more_per_credit_dollar_than_the_best_plan_and_a_bigger_pack_never_costs_more_per_dollar():
    best_plan = min(t["inr"] / float(plans.included_usd(name, 1, {})) for name, t in plans.TIERS.items())
    rates = [inr / float(usd) for inr, usd in packs.PACKS.values()]
    assert all(r > best_plan for r in rates)                 # a plan is always the better way to buy usage
    assert rates == sorted(rates, reverse=True)              # more rupees buy a little more per rupee


def test_the_listing_gives_the_website_what_it_needs():
    row = next(r for r in packs.listing() if r["id"] == "credits_2500")
    assert row == {"id": "credits_2500", "price_inr": 2500, "credit_usd": "13.50", "inr_per_usd": "185.2"}


def test_packs_need_a_plan_unless_switched_off():
    assert packs.requires_plan({}) is True and packs.requires_plan({"KELL_ADDON_REQUIRES_PLAN": "on"}) is True
    assert packs.requires_plan({"KELL_ADDON_REQUIRES_PLAN": "off"}) is False


class _Conn:
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

    async def fetchval(self, sql, *a):
        self.log.append((sql, a))
        return self._find(sql)

    def transaction(self):
        @asynccontextmanager
        async def tx():
            yield
        return tx()


class _Pool:
    def __init__(self, answers=None):
        self.conn = _Conn(answers or {})

    @asynccontextmanager
    async def acquire(self):
        yield self.conn

    def statements(self, needle):
        return [(s, a) for s, a in self.conn.log if needle in s]


@pytest.mark.asyncio
async def test_a_buyer_with_no_active_plan_is_refused_and_one_with_a_plan_is_allowed():
    with pytest.raises(packs.PackNeedsPlan) as refused:
        await packs.check_allowed(_Pool({"FROM plan_entitlements": None}), BUYER, env={})
    assert refused.value.status == 403
    await packs.check_allowed(_Pool({"FROM plan_entitlements": 1}), BUYER, env={})
    await packs.check_allowed(_Pool({"FROM plan_entitlements": None}), BUYER, env={"KELL_ADDON_REQUIRES_PLAN": "off"})


@pytest.mark.asyncio
async def test_a_pack_credits_its_fixed_dollars_not_the_margin_formula():
    pool = _Pool({"INSERT INTO org_wallet_credits": 1})
    env = {"KELL_INR_PER_USD": "90", "KELL_MARGIN": "0.5"}                    # the formula would give 1000 rupees = 7.407407 dollars
    assert await wallet.credit_payment(pool, org_id=BUYER, payment_ref="pay_1", amount_paise=100_000, created_by="razorpay",
                                       env=env, order_id="order_1", usd=Decimal("5.25"))
    args = pool.statements("INSERT INTO org_wallet_credits")[0][1]
    assert args[4] == Decimal("5.25")                                         # amount_usd
    assert args[6] is None                                                    # no margin recorded: the dollars were fixed
    plain = _Pool({"INSERT INTO org_wallet_credits": 1})
    await wallet.credit_payment(plain, org_id=BUYER, payment_ref="pay_2", amount_paise=100_000, created_by="razorpay", env=env,
                                order_id="order_2")
    assert plain.statements("INSERT INTO org_wallet_credits")[0][1][4] == Decimal("7.407407")


@pytest.mark.asyncio
async def test_record_order_stores_the_pack_and_its_dollars():
    pool = _Pool()
    await wallet.record_order(pool, org_id=BUYER, order_id="order_9", amount_paise=250_000, created_by="sub", pack="credits_2500",
                              pack_usd=Decimal("13.50"))
    sql, args = pool.statements("INSERT INTO billing_orders")[0]
    assert "pack, pack_usd" in sql and args[-2:] == ("credits_2500", Decimal("13.50"))
    plain = _Pool()
    await wallet.record_order(plain, org_id=BUYER, order_id="order_8", amount_paise=50_000, created_by="sub")
    assert "pack" not in plain.statements("INSERT INTO billing_orders")[0][0]
