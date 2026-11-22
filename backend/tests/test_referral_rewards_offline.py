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
        self.shared = st

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
                   "reward_inr": a[6], "reward_usd": a[7], "status": a[8], "reason": a[9],
                   "created_at": datetime(2026, 11, 18, tzinfo=timezone.utc),
                   "released_at": datetime(2026, 11, 18, tzinfo=timezone.utc) if a[8] == "released" else None}
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
        if "FROM referral_rewards" in sql and "created_at ASC" in sql:
            # release_pending's two queries: pending rows, then released rows with no wallet credit.
            if "NOT EXISTS" in sql:
                return [{"id": r["id"], "referrer": r["referrer_user_id"]} for r in self.s["rows"].values()
                        if r["status"] == "released" and f"referral:{r['id']}" not in self.s["credits"]]
            return [{"id": r["id"]} for r in self.s["rows"].values() if r["status"] == "pending"]
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
    monkeypatch.setenv("KEL_REFERRAL_REWARD_INR", "100")
    st["pool"] = Pool()
    return st


def _order(code=CODE, buyer=BUYER, order_id="order_1", paise=64900):
    return {"referral_code": code, "buyer_user_id": buyer, "order_id": order_id, "amount_paise": paise}


@pytest.mark.asyncio
async def test_a_paid_personal_code_pays_its_owner_at_once_and_counts_once(state, monkeypatch):
    async def ensure(*a, **k):
        return True
    monkeypatch.setattr("app.services.accounts.ensure_account", ensure)
    row = await referral_rewards.record_paid_order(state["pool"], order=_order(), payment_id="pay_1")
    assert row["status"] == "released" and row["referrer_user_id"] == REFERRER
    assert row["reward_inr"] == 100 and row["referred_user_id"] == BUYER
    assert row["reason"] is None and row["released_at"] is not None
    # AUTOMATIC: the credit is in the referrer's wallet as part of the same call, in rupees (100 -> 10000 paise).
    owner, usd = state["credits"][f"referral:{row['id']}"]
    assert owner == REFERRER and usd > 0


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
async def test_the_programme_is_ON_by_default_and_off_only_at_zero(state, monkeypatch):
    async def ensure(*a, **k):
        return True
    monkeypatch.setattr("app.services.accounts.ensure_account", ensure)
    monkeypatch.delenv("KEL_REFERRAL_REWARD_INR", raising=False)
    assert referral_rewards.enabled() is True and referral_rewards.reward_inr() == Decimal("100")
    row = await referral_rewards.record_paid_order(state["pool"], order=_order(), payment_id="pay_1")
    assert row is not None and row["status"] == "released"                 # on, out of the box
    monkeypatch.setenv("KEL_REFERRAL_REWARD_INR", "0")
    assert referral_rewards.enabled() is False
    assert await referral_rewards.record_paid_order(state["pool"], order=_order(buyer=THIRD, order_id="o2"), payment_id="p2") is None


@pytest.mark.asyncio
async def test_the_reward_is_credit_in_the_referrers_own_wallet_not_the_buyers(state, monkeypatch):
    async def ensure(*a, **k):
        return True
    monkeypatch.setattr("app.services.accounts.ensure_account", ensure)
    row = await referral_rewards.record_paid_order(state["pool"], order=_order(), payment_id="pay_1")
    assert row["status"] == "released"                                     # no separate release step
    owner, usd = state["credits"][f"referral:{row['id']}"]
    assert owner == REFERRER and owner != BUYER and usd > 0
    assert len(state["credits"]) == 1


@pytest.mark.asyncio
async def test_a_reversal_takes_back_exactly_what_was_credited(state, monkeypatch):
    async def ensure(*a, **k):
        return True
    monkeypatch.setattr("app.services.accounts.ensure_account", ensure)
    row = await referral_rewards.record_paid_order(state["pool"], order=_order(), payment_id="pay_1")
    credited_usd = state["credits"][f"referral:{row['id']}"][1]
    out = await referral_rewards.reverse(state["pool"], referral_id=row["id"], reason="refunded")
    assert out["status"] == "reversed" and out["reason"] == "refunded"
    assert state["credits"][f"referral-reversal:{row['id']}"][1] == -credited_usd


@pytest.mark.asyncio
async def test_a_referral_never_credited_is_not_reversed_into_negative_money(state, monkeypatch):
    """A 'void' or never-credited row has nothing to take back: no negative wallet row is written."""
    async def ensure(*a, **k):
        return True
    monkeypatch.setattr("app.services.accounts.ensure_account", ensure)
    monkeypatch.setattr(referral_rewards, "MAX_REFERRALS_PER_REFERRER", 1)
    await referral_rewards.record_paid_order(state["pool"], order=_order(), payment_id="p0")
    void = await referral_rewards.record_paid_order(state["pool"], order=_order(buyer=THIRD, order_id="o1"), payment_id="p1")
    assert void["status"] == "void"
    await referral_rewards.reverse(state["pool"], referral_id=void["id"], reason="refunded")
    assert f"referral-reversal:{void['id']}" not in state["credits"]


@pytest.mark.asyncio
async def test_the_thirty_first_paid_referral_is_recorded_but_void(state, monkeypatch):
    async def ensure(*a, **k):
        return True
    monkeypatch.setattr("app.services.accounts.ensure_account", ensure)
    monkeypatch.setattr(referral_rewards, "MAX_REFERRALS_PER_REFERRER", 2)
    for i in range(2):
        await referral_rewards.record_paid_order(state["pool"], order=_order(buyer=f"00000000-0000-4000-8000-{i:012d}",
                                                                            order_id=f"o{i}"), payment_id=f"p{i}")
    over = await referral_rewards.record_paid_order(state["pool"], order=_order(buyer=THIRD, order_id="o9"), payment_id="p9")
    assert over["status"] == "void" and "30" in over["reason"] and f"referral:{over['id']}" not in state["credits"]


@pytest.mark.asyncio
async def test_the_summary_never_names_the_people_who_used_the_code(state, monkeypatch):
    async def ensure(*a, **k):
        return True
    monkeypatch.setattr("app.services.accounts.ensure_account", ensure)
    await referral_rewards.record_paid_order(state["pool"], order=_order(), payment_id="pay_1")
    out = await referral_rewards.summary(state["pool"], referrer_user_id=REFERRER)
    assert out["limit"] == 30 and out["used"] == 1 and out["remaining"] == 29
    assert out["earned_inr"] == "100" and out["reward_inr"] == "100"
    assert set(out["referrals"][0]) == {"id", "status", "reward_inr", "at", "released_at", "reason"}
    assert BUYER not in str(out)


@pytest.mark.asyncio
async def test_the_repair_run_finishes_pending_and_uncredited_released_rows_once(state, monkeypatch):
    """release_pending is what a crash or an older deployment leaves for: a 'pending' row, and a 'released' row whose
    wallet credit never landed. Both are finished, and a second run changes nothing."""
    async def ensure(*a, **k):
        return True
    monkeypatch.setattr("app.services.accounts.ensure_account", ensure)
    old = await referral_rewards.record_paid_order(state["pool"], order=_order(), payment_id="pay_1")
    # An older deployment's row: written, never credited. Put it back that way by hand (the fake stores objects; the
    # service returns copies).
    state["rows"]["pay_1"]["status"] = "pending"
    state["rows"]["pay_1"]["released_at"] = None
    del state["credits"][f"referral:{old['id']}"]
    lost = await referral_rewards.record_paid_order(state["pool"], order=_order(buyer=THIRD, order_id="order_3"),
                                                    payment_id="pay_3")
    del state["credits"][f"referral:{lost['id']}"]                  # 'released', but the credit is missing

    fixed = await referral_rewards.release_pending(state["pool"])
    assert {f["id"] for f in fixed} == {old["id"], lost["id"]}
    assert state["rows"]["pay_1"]["status"] == "released" and f"referral:{old['id']}" in state["credits"]
    assert f"referral:{lost['id']}" in state["credits"]
    assert len(state["credits"]) == 2
    assert await referral_rewards.release_pending(state["pool"]) == []


def test_the_allowance_and_the_default_reward_are_the_founders_numbers():
    assert referral_rewards.MAX_REFERRALS_PER_REFERRER == 30
    assert referral_rewards.DEFAULT_REWARD_INR == "100"
    assert referral_rewards.reward_paise() == 10000                            # Rs 100 in paise


def test_rupees_convert_at_the_same_rate_a_purchase_uses():
    """Rs 100 of reward buys exactly what Rs 100 of top-up buys: both go through wallet.usd_for_paise."""
    from app.services import wallet
    assert referral_rewards.reward_usd() == wallet.usd_for_paise(10000)
    assert referral_rewards.reward_usd({"KEL_REFERRAL_REWARD_INR": "250"}) == wallet.usd_for_paise(25000)


def test_an_absurd_or_unreadable_reward_falls_back_to_the_default():
    assert referral_rewards.reward_inr({"KEL_REFERRAL_REWARD_INR": "lots"}) == Decimal("100")
    assert referral_rewards.reward_inr({"KEL_REFERRAL_REWARD_INR": "99999999"}) == Decimal("100")
    assert referral_rewards.reward_inr({"KEL_REFERRAL_REWARD_INR": "0"}) == Decimal("0")
    assert referral_rewards.reward_inr({"KEL_REFERRAL_REWARD_INR": "-5"}) == Decimal("0")
