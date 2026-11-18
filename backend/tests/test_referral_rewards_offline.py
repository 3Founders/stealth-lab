"""Referral rewards (db/212): a personal code pays its owner credit in their OWN wallet, once the person they referred
has paid -- 30 paid referrals per account, never a team wallet, never cash, never self-referral."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.services import referral_rewards

REFERRER = "11111111-1111-4111-8111-111111111111"
BUYER = "22222222-2222-4222-8222-222222222222"
THIRD = "33333333-3333-4333-8333-333333333333"
CODE = "KELABC2345"


class Conn:
    """A pool OR a connection: the service takes both, so one fake decides what each statement answers from `st`."""

    def __init__(self, st):
        self.s = st

    def transaction(self):
        @asynccontextmanager
        async def tx():
            yield self
        return tx()

    async def execute(self, sql, *a):
        self.s["sql"].append(sql)
        if "INSERT INTO org_wallet_credits" in sql:
            ref = a[1]
            if ref not in self.s["credits"]:
                self.s["credits"][ref] = (a[0], Decimal(str(a[2])))

    async def fetchrow(self, sql, *a):
        self.s["sql"].append(sql)
        if "FROM billing_referral_codes WHERE code" in sql:
            return self.s["code_row"]
        if "FROM referral_rewards WHERE payment_id" in sql:
            return self.s["rows"].get(a[0])
        if "INSERT INTO referral_rewards" in sql:
            buyer = a[2]
            if buyer in self.s["referred"] or a[4] in self.s["rows"]:
                return None
            row = {"id": f"r{len(self.s['rows']) + 1}", "code": a[0], "referrer_user_id": a[1],
                   "referred_user_id": buyer, "order_id": a[3], "payment_id": a[4], "amount_paise": a[5],
                   "reward_usd": a[6], "status": a[7], "reason": a[8],
                   "created_at": datetime(2026, 11, 18, tzinfo=timezone.utc), "released_at": None}
            self.s["referred"].add(buyer)
            self.s["rows"][a[4]] = row
            self.s["created"].append(row)
            return row
        if "FROM referral_rewards WHERE id" in sql:
            return next((r for r in self.s["rows"].values() if r["id"] == a[0]), None)
        if "UPDATE referral_rewards SET status = 'released'" in sql:
            r = next((r for r in self.s["rows"].values() if r["id"] == a[0] and r["status"] == "pending"), None)
            if r:
                r["status"], r["released_at"] = "released", datetime(2026, 11, 18, tzinfo=timezone.utc)
            return r
        if "UPDATE referral_rewards SET status = 'reversed'" in sql:
            r = next((r for r in self.s["rows"].values() if r["id"] == a[0] and r["status"] in ("pending", "released")), None)
            if r:
                r["status"], r["reason"] = "reversed", a[1]
            return r
        return None

    async def fetchval(self, sql, *a):
        self.s["sql"].append(sql)
        if "WHERE referred_user_id" in sql:
            return 1 if a[0] in self.s["referred"] else None
        if "status = 'released'" in sql and "count(*)" in sql:
            return sum(1 for r in self.s["rows"].values() if r["referrer_user_id"] == a[0] and r["status"] == "released")
        if "INSERT INTO org_wallet_credits" in sql:
            ref = a[1]
            if ref in self.s["credits"]:
                return None                                  # ON CONFLICT (payment_ref) DO NOTHING
            self.s["credits"][ref] = (a[0], Decimal(str(a[2])))
            return 1
        return None

    async def fetch(self, sql, *a):
        self.s["sql"].append(sql)
        return [r for r in self.s["rows"].values() if r["referrer_user_id"] == a[0]]


@pytest.fixture
def state(monkeypatch):
    st = {"code_row": {"owner": REFERRER, "kind": "personal", "grant_usd": None, "grant_org_id": None},
          "rows": {}, "referred": set(), "credits": {}, "created": [], "sql": []}

    @asynccontextmanager
    async def tx(pool, scope):
        st["last_scope"] = scope
        yield Conn(st)

    @asynccontextmanager
    async def acquire():
        yield Conn(st)

    class Pool:
        def acquire(self):
            return acquire()

    monkeypatch.setattr(referral_rewards, "tenant_transaction", tx)
    monkeypatch.setenv("KEL_REFERRAL_REWARD_USD", "1.00")
    st["pool"] = Pool()
    return st


def _order(code=CODE, buyer=BUYER, order_id="order_1", paise=64900):
    return {"referral_code": code, "buyer_user_id": buyer, "order_id": order_id, "amount_paise": paise}


@pytest.mark.asyncio
async def test_a_paid_personal_code_creates_one_pending_referral_for_its_owner(state, monkeypatch):
    async def ensure(*a, **k):
        return True
    monkeypatch.setattr("app.services.accounts.ensure_account", ensure)
    row = await referral_rewards.record_paid_order(state["pool"], order=_order(), payment_id="pay_1")
    assert row["status"] == "pending" and row["referrer_user_id"] == REFERRER
    assert Decimal(str(row["reward_usd"])) == Decimal("1.00") and row["referred_user_id"] == BUYER
    assert row["reason"] is None


@pytest.mark.asyncio
async def test_a_redelivered_payment_returns_the_same_row_and_pays_once(state, monkeypatch):
    async def ensure(*a, **k):
        return True
    monkeypatch.setattr("app.services.accounts.ensure_account", ensure)
    first = await referral_rewards.record_paid_order(state["pool"], order=_order(), payment_id="pay_1")
    again = await referral_rewards.record_paid_order(state["pool"], order=_order(), payment_id="pay_1")
    assert first["id"] == again["id"] and len(state["created"]) == 1


@pytest.mark.asyncio
async def test_a_second_code_for_the_same_buyer_earns_nothing_more(state, monkeypatch):
    async def ensure(*a, **k):
        return True
    monkeypatch.setattr("app.services.accounts.ensure_account", ensure)
    await referral_rewards.record_paid_order(state["pool"], order=_order(), payment_id="pay_1")
    again = await referral_rewards.record_paid_order(state["pool"], order=_order(order_id="order_2"), payment_id="pay_2")
    assert again is None and len(state["created"]) == 1


@pytest.mark.asyncio
async def test_the_buyer_cannot_refer_themselves(state, monkeypatch):
    async def ensure(*a, **k):
        return True
    monkeypatch.setattr("app.services.accounts.ensure_account", ensure)
    state["code_row"] = {"owner": BUYER, "kind": "personal", "grant_usd": None, "grant_org_id": None}
    assert await referral_rewards.record_paid_order(state["pool"], order=_order(), payment_id="pay_1") is None


@pytest.mark.asyncio
async def test_an_issued_free_or_team_code_pays_nobody(state, monkeypatch):
    async def ensure(*a, **k):
        return True
    monkeypatch.setattr("app.services.accounts.ensure_account", ensure)
    for row in ({"owner": None, "kind": "issued", "grant_usd": None, "grant_org_id": None},
                {"owner": REFERRER, "kind": "personal", "grant_usd": "0.25", "grant_org_id": None},
                {"owner": REFERRER, "kind": "personal", "grant_usd": None, "grant_org_id": "team-id"}):
        state["code_row"] = row
        assert await referral_rewards.record_paid_order(state["pool"], order=_order(), payment_id="pay_1") is None


@pytest.mark.asyncio
async def test_the_programme_is_off_by_default_and_off_when_the_reward_is_zero(state, monkeypatch):
    async def ensure(*a, **k):
        return True
    monkeypatch.setattr("app.services.accounts.ensure_account", ensure)
    monkeypatch.delenv("KEL_REFERRAL_REWARD_USD", raising=False)
    assert referral_rewards.enabled() is False
    assert await referral_rewards.record_paid_order(state["pool"], order=_order(), payment_id="pay_1") is None
    monkeypatch.setenv("KEL_REFERRAL_REWARD_USD", "0")
    assert await referral_rewards.record_paid_order(state["pool"], order=_order(), payment_id="pay_1") is None


@pytest.mark.asyncio
async def test_a_released_reward_is_credit_in_the_referrers_own_wallet_not_the_buyers(state, monkeypatch):
    async def ensure(*a, **k):
        return True
    monkeypatch.setattr("app.services.accounts.ensure_account", ensure)
    row = await referral_rewards.record_paid_order(state["pool"], order=_order(), payment_id="pay_1")
    done = await referral_rewards.release(state["pool"], referral_id=row["id"])
    assert done["status"] == "released"
    owner, usd = state["credits"][f"referral:{row['id']}"]
    assert owner == REFERRER and owner != BUYER and usd == Decimal("1.00")
    # once released, a second release returns the same row and credits nothing more (the guard is the status check)
    again = await referral_rewards.release(state["pool"], referral_id=row["id"])
    assert again["status"] == "released" and len(state["credits"]) == 1


@pytest.mark.asyncio
async def test_a_crash_after_crediting_does_not_pay_twice_on_retry(state, monkeypatch):
    """The process died between the wallet insert and the status update: the row is still pending, the credit is
    already there. A retry must find the existing payment_ref and stop rather than credit a second time."""
    async def ensure(*a, **k):
        return True
    monkeypatch.setattr("app.services.accounts.ensure_account", ensure)
    row = await referral_rewards.record_paid_order(state["pool"], order=_order(), payment_id="pay_1")
    state["credits"][f"referral:{row['id']}"] = (REFERRER, Decimal("1.00"))   # credited, but the row was never flipped
    assert await referral_rewards.release(state["pool"], referral_id=row["id"]) is None
    assert len(state["credits"]) == 1


@pytest.mark.asyncio
async def test_a_reversal_closes_the_credit_with_a_negative_row(state, monkeypatch):
    async def ensure(*a, **k):
        return True
    monkeypatch.setattr("app.services.accounts.ensure_account", ensure)
    row = await referral_rewards.record_paid_order(state["pool"], order=_order(), payment_id="pay_1")
    await referral_rewards.release(state["pool"], referral_id=row["id"])
    out = await referral_rewards.reverse(state["pool"], referral_id=row["id"], reason="refunded")
    assert out["status"] == "reversed" and out["reason"] == "refunded"
    assert state["credits"][f"referral-reversal:{row['id']}"][1] == Decimal("-1.00")


@pytest.mark.asyncio
async def test_the_thirty_first_paid_referral_is_recorded_but_void(state, monkeypatch):
    async def ensure(*a, **k):
        return True
    monkeypatch.setattr("app.services.accounts.ensure_account", ensure)
    monkeypatch.setattr(referral_rewards, "MAX_REFERRALS_PER_REFERRER", 2)
    for i in range(2):
        await referral_rewards.record_paid_order(state["pool"], order=_order(buyer=f"00000000-0000-4000-8000-{i:012d}",
                                                                            order_id=f"o{i}"), payment_id=f"p{i}")
        row = [r for r in state["created"] if r["payment_id"] == f"p{i}"][0]
        await referral_rewards.release(state["pool"], referral_id=row["id"])
    over = await referral_rewards.record_paid_order(state["pool"], order=_order(buyer=THIRD, order_id="o9"), payment_id="p9")
    assert over["status"] == "void" and "30" in over["reason"] and f"referral:{over['id']}" not in state["credits"]


@pytest.mark.asyncio
async def test_the_summary_never_names_the_people_who_used_the_code(state, monkeypatch):
    async def ensure(*a, **k):
        return True
    monkeypatch.setattr("app.services.accounts.ensure_account", ensure)
    row = await referral_rewards.record_paid_order(state["pool"], order=_order(), payment_id="pay_1")
    await referral_rewards.release(state["pool"], referral_id=row["id"])
    out = await referral_rewards.summary(state["pool"], referrer_user_id=REFERRER)
    assert out["limit"] == 30 and out["used"] == 1 and out["remaining"] == 29
    assert out["earned_usd"] == "1.00" and out["reward_usd"] == "1.00"
    assert set(out["referrals"][0]) == {"id", "status", "reward_usd", "at", "released_at", "reason"}
    assert BUYER not in str(out)


def test_the_allowance_and_the_default_reward_are_the_founders_numbers():
    assert referral_rewards.MAX_REFERRALS_PER_REFERRER == 30
    assert referral_rewards.DEFAULT_REWARD_USD == "1.00"
