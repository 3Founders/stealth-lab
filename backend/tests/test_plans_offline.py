"""Pro and Pilot without a database: the amounts are worked out on the server from the plan, bad requests are refused, an
activation is a series of idempotent steps with the entitlement written last, and who pays by default is a team before a
person's own wallet."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.mcp_server.chat_routes import paying_org
from app.services import accounts, plans

BUYER = "11111111-1111-4111-8111-111111111111"
TEAM = "22222222-2222-4222-8222-222222222222"
NOW = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ the amount is the server's

def test_the_ladder_is_200_1200_2000_rupees_for_30_days_with_one_six_and_twelve_dollars_of_usage():
    for plan, inr, usd in (("starter", 200, "1"), ("pro", 1200, "6"), ("plus", 2000, "12")):
        o = plans.parse_plan_order({"plan": plan}, env={})
        assert (o.plan, o.seats, o.amount_paise, o.period_days) == (plan, 1, inr * 100, 30)
        assert o.included_usd == Decimal(usd) and o.team_name is None and not o.creates_team


def test_the_recommended_tier_gives_more_usage_per_rupee_than_the_step_below_it_and_the_anchor():
    per_rupee = {p: plans.included_usd(p, 1, {}) / plans.TIERS[p]["inr"] for p in ("starter", "pro", "plus")}
    assert per_rupee["plus"] > per_rupee["pro"] and per_rupee["plus"] > per_rupee["starter"]
    assert plans.included_usd("plus", 1, {}) == 2 * plans.included_usd("pro", 1, {})        # twice Pro's usage for 800 rupees more


def test_a_request_cannot_set_the_price():
    o = plans.parse_plan_order({"plan": "pro", "amount_inr": 1, "amount_paise": 100, "price": 1}, env={})
    assert o.amount_paise == 120_000
    p = plans.parse_plan_order({"plan": "pilot", "seats": 3, "team_name": "Acme", "amount_paise": 1}, env={})
    assert p.amount_paise == 3 * 64_900


@pytest.mark.parametrize("seats,total", [(1, 64_900), (2, 129_800), (3, 194_700), (10, 649_000)])
def test_a_pilot_is_649_rupees_a_seat_for_14_days_with_four_dollars_a_seat(seats, total):
    o = plans.parse_plan_order({"plan": "pilot", "seats": seats, "team_name": "Acme pilot"}, env={})
    assert o.amount_paise == total and o.period_days == 14 and o.included_usd == Decimal("4") * seats


def test_one_seat_is_an_individual_and_two_or_more_make_a_team():
    one = plans.parse_plan_order({"plan": "pilot", "seats": 1}, env={})          # no team name needed
    assert one.team_name is None and not one.creates_team
    two = plans.parse_plan_order({"plan": "pilot", "seats": 2, "team_name": "  Two   of us "}, env={})
    assert two.creates_team and two.team_name == "Two of us"                      # spaces tidied


@pytest.mark.parametrize("body", [
    None, [], "pro", {}, {"plan": "teams"}, {"plan": "enterprise"}, {"plan": "PRO"}, {"plan": "free"}, 
    {"plan": "pilot"}, {"plan": "pilot", "seats": 0, "team_name": "x1"}, {"plan": "pilot", "seats": 11, "team_name": "Acme"},
    {"plan": "pilot", "seats": -1}, {"plan": "pilot", "seats": "many"}, {"plan": "pilot", "seats": True},
    {"plan": "pilot", "seats": 2}, {"plan": "pilot", "seats": 3, "team_name": ""}, {"plan": "pilot", "seats": 3, "team_name": "a"},
    {"plan": "pilot", "seats": 3, "team_name": "x" * 61}, {"plan": "pilot", "seats": 3, "team_name": "bad\x00name"},
])
def test_bad_plan_requests_are_refused(body):
    with pytest.raises(plans.PlanError):
        plans.parse_plan_order(body, env={})


def test_the_included_usage_is_a_fixed_dollar_figure_set_by_environment_not_by_the_margin():
    env = {"KELL_PRO_INCLUDED_USD": "7.5", "KELL_PILOT_SEAT_USD": "3", "KELL_MARGIN": "0.9"}
    assert plans.included_usd("pro", 1, env) == Decimal("7.5") and plans.included_usd("pilot", 4, env) == Decimal("12")
    bad = {"KELL_PRO_INCLUDED_USD": "-1", "KELL_PILOT_SEAT_USD": "nope"}
    assert plans.included_usd("pro", 1, bad) == Decimal("6") and plans.included_usd("pilot", 2, bad) == Decimal("8")


# ------------------------------------------------------------------ renewals and the team slug

def test_a_renewal_starts_when_the_current_period_ends_and_never_loses_days():
    soon = NOW + timedelta(days=10)
    start, end = plans.period_end(NOW, 30, soon)
    assert start == soon and end == soon + timedelta(days=30)
    start, end = plans.period_end(NOW, 30, NOW - timedelta(days=1))               # already ended: starts now
    assert start == NOW and end == NOW + timedelta(days=30)
    assert plans.period_end(NOW, 14, None) == (NOW, NOW + timedelta(days=14))


def test_the_team_slug_comes_from_the_payment_so_a_retry_finds_the_same_team():
    assert plans.team_slug("pay_1") == plans.team_slug("pay_1") != plans.team_slug("pay_2")
    assert plans.team_slug("pay_1").startswith("team-")


# ------------------------------------------------------------------ activation, against a fake database

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


def _order(plan, seats=1, team=None, days=30):
    return {"plan": plan, "seats": seats, "team_name": team, "buyer_user_id": BUYER, "period_days": days, "order_id": "order_1"}


@pytest.mark.asyncio
async def test_a_team_pilot_makes_the_team_then_credits_it_then_writes_the_entitlement_last():
    pool = FakePool({
        "FROM plan_entitlements WHERE payment_ref": None,                     # not processed before
        "SELECT id::text FROM organizations WHERE slug": TEAM,
        "INSERT INTO plan_entitlements": 1,
    })
    done = await plans.activate(pool, order=_order("pilot", 3, "Acme pilot", 14), payment_id="pay_1", amount_paise=194_700,
                                now=NOW, env={})
    assert done is True
    sql = [s for s, _ in pool.conn.log]
    first = {k: min(i for i, s in enumerate(sql) if k in s) for k in
             ("FROM plan_entitlements WHERE payment_ref", "INSERT INTO org_policies", "INSERT INTO org_memberships",
              "INSERT INTO org_wallet_credits", "INSERT INTO plan_entitlements")}
    assert first["FROM plan_entitlements WHERE payment_ref"] < first["INSERT INTO org_policies"] < first["INSERT INTO org_wallet_credits"]
    assert first["INSERT INTO org_wallet_credits"] < first["INSERT INTO plan_entitlements"] == max(first.values())
    credit_args = pool.statements("INSERT INTO org_wallet_credits")[0][1]
    assert credit_args[0] == TEAM and credit_args[1] == "pay_1:included" and credit_args[2] == Decimal("12")
    member_args = pool.statements("INSERT INTO org_memberships")[0][1]
    assert member_args[0] == TEAM and member_args[1] == BUYER                 # the buyer owns the team
    ent = pool.statements("INSERT INTO plan_entitlements")[0][1]
    assert ent[0] == BUYER and ent[1] == TEAM and ent[2] == "pilot" and ent[3] == 3 and ent[4] == "Acme pilot"
    assert ent[6] - ent[5] == timedelta(days=14) and ent[7] == "pay_1" and ent[9] == 194_700


@pytest.mark.asyncio
async def test_a_payment_already_processed_changes_nothing():
    pool = FakePool({"FROM plan_entitlements WHERE payment_ref": 1})
    assert await plans.activate(pool, order=_order("pro"), payment_id="pay_1", amount_paise=99_900, now=NOW, env={}) is False
    assert not pool.statements("INSERT INTO")


@pytest.mark.asyncio
async def test_a_pro_credits_six_dollars_to_the_buyers_own_account_and_makes_no_organization():
    pool = FakePool({"FROM plan_entitlements WHERE payment_ref": None, "INSERT INTO plan_entitlements": 1})
    assert await plans.activate(pool, order=_order("pro"), payment_id="pay_2", amount_paise=99_900, now=NOW,
                                env={"KELL_WALLETS": "on"})
    assert not pool.statements("INSERT INTO organizations") and not pool.statements("INSERT INTO org_memberships")
    credit = pool.statements("INSERT INTO org_wallet_credits")[0][1]
    assert credit[0] == BUYER and credit[2] == Decimal("6")
    assert pool.statements("INSERT INTO org_policies")                        # the buyer's own policy exists, so the credit can be spent


@pytest.mark.asyncio
async def test_a_one_seat_pilot_is_the_buyers_own_account_too():
    pool = FakePool({"FROM plan_entitlements WHERE payment_ref": None, "INSERT INTO plan_entitlements": 1})
    assert await plans.activate(pool, order=_order("pilot", 1, None, 14), payment_id="pay_3", amount_paise=64_900, now=NOW, env={})
    assert not pool.statements("INSERT INTO organizations")
    assert pool.statements("INSERT INTO org_wallet_credits")[0][1][2] == Decimal("4")


@pytest.mark.asyncio
async def test_a_renewal_extends_from_the_latest_end():
    soon = NOW + timedelta(days=5)
    pool = FakePool({"FROM plan_entitlements WHERE payment_ref": None, "max(ends_at)": soon, "INSERT INTO plan_entitlements": 1})
    await plans.activate(pool, order=_order("pro"), payment_id="pay_4", amount_paise=99_900, now=NOW, env={})
    ent = pool.statements("INSERT INTO plan_entitlements")[0][1]
    assert ent[5] == soon and ent[6] == soon + timedelta(days=30)


# ------------------------------------------------------------------ who pays by default

def test_a_person_in_one_team_pays_through_the_team_and_a_person_alone_through_their_own_wallet():
    assert paying_org(None, (TEAM, BUYER), None, BUYER) == (TEAM, None)             # a team before the person's own wallet
    assert paying_org(None, (BUYER,), None, BUYER) == (BUYER, None)
    assert paying_org(BUYER, (TEAM, BUYER), None, BUYER) == (BUYER, None)           # a named account is honoured
    assert paying_org("33333333-3333-4333-8333-333333333333", (TEAM, BUYER), None, BUYER)[1]    # not theirs: refused
    two_teams = "44444444-4444-4444-8444-444444444444"
    assert paying_org(None, (TEAM, two_teams, BUYER), None, BUYER) == (None, None)  # ambiguous: they must say which


def test_the_old_environment_name_still_switches_wallets_on_and_the_new_one_works():
    assert accounts.enabled({"KELL_WALLETS": "on"}) and accounts.enabled({"KELL_PERSONAL_ORGS": "1"}) and not accounts.enabled({})


# ------------------------------------------------------------------ the provider path picks the same payer

def test_the_provider_path_prefers_the_one_team_over_the_persons_own_wallet_and_still_refuses_real_ambiguity():
    from types import SimpleNamespace

    from app.providers.service import governing_org
    from app.providers.types import ProviderCallDenied
    from app.services.access import AccessScope

    conn = SimpleNamespace(owner="pool", connection_id="zai-glm")
    other = "44444444-4444-4444-8444-444444444444"
    one_team = AccessScope.for_org_member("sub", [TEAM, BUYER], BUYER)
    assert governing_org(conn, one_team, None, governed=True) == TEAM
    assert governing_org(conn, one_team, BUYER, governed=True) == BUYER                  # a named account is honoured
    alone = AccessScope.for_org_member("sub", [BUYER], BUYER)
    assert governing_org(conn, alone, None, governed=True) == BUYER
    with pytest.raises(ProviderCallDenied):
        governing_org(conn, AccessScope.for_org_member("sub", [TEAM, other, BUYER], BUYER), None, governed=True)
    with pytest.raises(ProviderCallDenied):                                                # without an own account nothing is guessed
        governing_org(conn, AccessScope.for_org_member("sub", [TEAM, other]), None, governed=True)


# ------------------------------------------------------------------ the reconcile job (a webhook that never arrived)

@pytest.mark.asyncio
async def test_reconcile_activates_only_captured_inr_payments_for_exactly_the_recorded_amount():
    from app.services import billing_reconcile as rec

    plan_order = {"order_id": "order_p", "organization_id": BUYER, "amount_paise": 120_000, "plan": "pro", "seats": 1,
                  "team_name": None, "buyer_user_id": BUYER, "period_days": 30}
    pool = FakePool({"FROM billing_orders o": [plan_order], "FROM plan_entitlements WHERE payment_ref": None,
                     "INSERT INTO plan_entitlements": 1})

    async def fetch(order_id):
        return [{"id": "pay_auth", "status": "authorized", "currency": "INR", "amount": 120_000},
                {"id": "pay_usd", "status": "captured", "currency": "USD", "amount": 120_000},
                {"id": "pay_short", "status": "captured", "currency": "INR", "amount": 100},
                {"id": "pay_ok", "status": "captured", "currency": "INR", "amount": 120_000}]

    dry = await rec.reconcile(pool, fetch_payments=fetch, apply=False)
    assert dry == {"checked": 1, "found": 1, "activated": 0, "errors": 0} and not pool.statements("INSERT INTO plan_entitlements")
    done = await rec.reconcile(pool, fetch_payments=fetch, apply=True)
    assert done["found"] == 1 and done["activated"] == 1 and done["errors"] == 0
    assert pool.statements("INSERT INTO plan_entitlements")[0][1][7] == "pay_ok"


@pytest.mark.asyncio
async def test_reconcile_survives_a_failed_fetch_and_carries_on_with_the_other_orders():
    from app.services import billing_reconcile as rec

    orders = [{"order_id": f"order_{i}", "organization_id": BUYER, "amount_paise": 20_000, "plan": None, "seats": None,
               "team_name": None, "buyer_user_id": None, "period_days": None} for i in range(3)]
    pool = FakePool({"FROM billing_orders o": orders})

    async def fetch(order_id):
        if order_id == "order_0":
            raise RuntimeError("Razorpay is down")
        return [{"id": f"pay_{order_id}", "status": "captured", "currency": "INR", "amount": 20_000}]

    out = await rec.reconcile(pool, fetch_payments=fetch, apply=True)
    assert out["checked"] == 3 and out["errors"] == 1 and out["found"] == 2
    assert len(pool.statements("INSERT INTO org_wallet_credits")) == 2
