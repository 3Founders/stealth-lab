"""Wallet refunds (db/215): a refund the gateway reports takes money back out of the wallet -- never more than is left --and takes back the referral reward paid on that payment."""
from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal

import pytest

from app.services import wallet_refunds

BUYER = "22222222-2222-4222-8222-222222222222"
PAY = "pay_ABC123"


class Conn:
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
            self.s["credits"].append({"account": a[0], "ref": a[1], "source": "refund",
                                      "paise": int(a[2]), "usd": a[3]})

    async def fetchrow(self, sql, *a):
        self.s["sql"].append(sql)
        if "FROM wallet_refunds WHERE refund_id" in sql:
            return self.s["refunds"].get(a[0])
        if "FROM org_wallet_credits w WHERE w.payment_ref" in sql:
            return self.s["order"] if self.s["order"] else None
        if "sum(amount_paise)" in sql and "sum(amount_usd)" in sql:
            return {"paise": self.s["bought_paise"], "usd": self.s["bought_usd"]}
        if "INSERT INTO wallet_refunds" in sql:
            if a[0] in self.s["refunds"]:
                return None
            row = {"refund_id": a[0], "payment_id": a[1], "order_id": a[2], "account_id": a[3],
                   "amount_paise": a[4], "applied_paise": a[5], "currency": a[6], "status": a[7], "reason": a[8]}
            self.s["refunds"][a[0]] = row
            return row
        return None

    async def fetchval(self, sql, *a):
        self.s["sql"].append(sql)
        if "provider_call_ledger" in sql:
            return self.s["spent_usd"]
        if "INSERT INTO org_wallet_credits" in sql:
            if a[1] in {c["ref"] for c in self.s["credits"]}:
                return None
            self.s["credits"].append({"account": a[0], "ref": a[1], "paise": int(a[2]), "usd": a[3]})
            return 1
        return None

    async def fetch(self, sql, *a):
        self.s["sql"].append(sql)
        if "FROM referral_rewards" in sql:
            return self.s["reward_rows"]
        return []


@pytest.fixture
def state(monkeypatch):
    st = {"sql": [], "credits": [], "refunds": {}, "bought_paise": 64900, "bought_usd": Decimal("4.000000"),
          "spent_usd": Decimal("0"), "order": {"account_id": BUYER, "order_id": "order_1"}, "reward_rows": [],
          "reversed": []}

    @asynccontextmanager
    async def tx(pool, scope):
        yield Conn(st)

    @asynccontextmanager
    async def acquire():
        yield Conn(st)

    class Pool:
        def acquire(self):
            return acquire()

    async def fake_reverse(pool, *, referral_id, reason):
        st["reversed"].append(referral_id)
        return {"id": referral_id, "status": "reversed"}

    monkeypatch.setattr(wallet_refunds, "tenant_transaction", tx)
    monkeypatch.setattr(wallet_refunds.referral_rewards, "reverse", fake_reverse)
    st["pool"] = Pool()
    return st


@pytest.mark.asyncio
async def test_a_full_refund_takes_the_paid_rupees_back_out(state):
    out = await wallet_refunds.apply_refund(state["pool"], refund_id="rfnd_1", payment_id=PAY, amount_paise=64900)
    assert out["status"] == "applied" and out["applied_paise"] == 64900
    row = state["credits"][0]
    assert row["account"] == BUYER and row["ref"] == "refund:rfnd_1" and row["paise"] == -64900
    assert row["usd"] < 0


@pytest.mark.asyncio
async def test_a_redelivered_refund_moves_money_once(state):
    first = await wallet_refunds.apply_refund(state["pool"], refund_id="rfnd_1", payment_id=PAY, amount_paise=64900)
    again = await wallet_refunds.apply_refund(state["pool"], refund_id="rfnd_1", payment_id=PAY, amount_paise=64900)
    assert first["refund_id"] == again["refund_id"] and len(state["credits"]) == 1


@pytest.mark.asyncio
async def test_a_refund_never_takes_more_than_the_wallet_has_left(state):
    state["spent_usd"] = Decimal("3.000000")                    # most of the credit is already spent
    out = await wallet_refunds.apply_refund(state["pool"], refund_id="rfnd_1", payment_id=PAY, amount_paise=64900)
    assert out["status"] == "applied" and 0 < out["applied_paise"] < 64900
    assert state["credits"][0]["paise"] == -out["applied_paise"]
    assert "spent" in out["reason"]


@pytest.mark.asyncio
async def test_a_refund_of_a_fully_spent_wallet_is_skipped_not_driven_negative(state):
    state["spent_usd"] = Decimal("10.000000")
    out = await wallet_refunds.apply_refund(state["pool"], refund_id="rfnd_1", payment_id=PAY, amount_paise=64900)
    assert out["status"] == "skipped" and out["applied_paise"] == 0
    assert not state["credits"]                                # no negative row: nothing to take back


@pytest.mark.asyncio
async def test_a_refund_takes_back_the_referral_reward_on_that_payment(state):
    state["reward_rows"] = [{"id": "r1"}, {"id": "r2"}]
    out = await wallet_refunds.apply_refund(state["pool"], refund_id="rfnd_1", payment_id=PAY, amount_paise=64900)
    assert out["reward_reversed"] == 2 and state["reversed"] == ["r1", "r2"]


@pytest.mark.asyncio
async def test_a_payment_that_never_credited_a_wallet_is_skipped(state):
    state["order"] = None
    out = await wallet_refunds.apply_refund(state["pool"], refund_id="rfnd_1", payment_id="pay_nope", amount_paise=100)
    assert out["status"] == "skipped" and not state["credits"]


def test_only_created_and_processed_events_that_moved_money_are_acted_on():
    def ev(event, status="processed", captured=True, amount=100):
        return {"event": event, "payload": {
            "refund": {"entity": {"id": "rfnd_1", "payment_id": "pay_1", "amount": amount,
                                  "currency": "INR", "status": status}},
            "payment": {"entity": {"captured": captured}}}}

    assert wallet_refunds.refund_from_event(ev("refund.processed"))["refund_id"] == "rfnd_1"
    assert wallet_refunds.refund_from_event(ev("refund.created")) is not None
    assert wallet_refunds.refund_from_event(ev("refund.failed", status="failed")) is None
    assert wallet_refunds.refund_from_event(ev("refund.speed_changed")) is None
    assert wallet_refunds.refund_from_event(ev("refund.processed", captured=False)) is None   # an auto-refund
    assert wallet_refunds.refund_from_event(ev("refund.processed", amount=0)) is None
    assert wallet_refunds.refund_from_event({"event": "payment.captured"}) is None


def test_the_wallet_may_hold_a_negative_row_and_a_refund_has_its_own_source():
    """db/215 relaxed the CHECKs that made the wallet one-way; without that a reversal violates the constraint.
    They must allow a negative row WITHOUT forbidding the 0-paise grants the live table already holds."""
    from pathlib import Path

    sql = Path(__file__).resolve().parents[1] / "db" / "215_referral_and_refund_fixup.sql"
    text = sql.read_text(encoding="utf-8")
    assert "CHECK (amount_usd <> 0)" in text                        # a row that moves no usage is a caller bug
    assert "(amount_paise > 0) = (amount_usd > 0)" in text          # the two currencies cannot disagree in sign
    assert "amount_paise = 0 OR" in text                            # the existing 0-paise grants stay legal
    assert "'refund'" in text and "wallet_refunds" in text
