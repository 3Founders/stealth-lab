"""Referral codes without a database: what a code is, which codes unlock the one-seat pilot, the personal code made for a
buying account, and the dashboard's create / edit / delete rules (a code an order was made with is deactivated, never
deleted, so the attribution record survives)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from app.services import referrals

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
OWNER = "11111111-1111-4111-8111-111111111111"


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


class BrokenPool(FakePool):
    @asynccontextmanager
    async def acquire(self):
        raise RuntimeError("no table")
        yield  # pragma: no cover


def test_a_code_is_trimmed_upper_case_and_plain_letters_and_numbers():
    assert referrals.clean("  friend7 ") == "FRIEND7"
    assert referrals.clean(" Firsttrial ") == "FIRSTTRIAL"          # the seeded launch code, as the founders type it
    for bad in ("ab", "a" * 33, "fri end", "friend!", "FRIEND-7", "", None):
        with pytest.raises(referrals.ReferralError):
            referrals.clean(bad)


@pytest.mark.asyncio
async def test_active_codes_are_only_the_active_ones_and_a_broken_table_closes_the_gate():
    pool = FakePool({"FROM billing_referral_codes WHERE active": [{"code": "FRIEND"}, {"code": "PARTNER"}]})
    assert await referrals.active_codes(pool) == frozenset({"FRIEND", "PARTNER"})
    assert await referrals.active_codes(BrokenPool()) == frozenset()


@pytest.mark.asyncio
async def test_a_personal_code_is_made_once_per_buyer():
    pool = FakePool({"WHERE owner_user_id = $1::uuid LIMIT 1": None,
                     "INSERT INTO billing_referral_codes": "KELAB23CDX"})
    made = await referrals.ensure_personal(pool, OWNER)
    assert made == "KELAB23CDX" and len(made) == 10 and made.startswith("KEL")
    sql, args = pool.statements("INSERT INTO billing_referral_codes")[0]
    assert args[1] == OWNER and "personal" in sql and "plan_activation" in sql
    have = FakePool({"WHERE owner_user_id = $1::uuid LIMIT 1": "KELOLD123"})
    assert await referrals.ensure_personal(have, OWNER) == "KELOLD123"
    assert not have.statements("INSERT INTO")


@pytest.mark.asyncio
async def test_a_personal_code_failure_never_breaks_the_webhook():
    assert await referrals.ensure_personal(BrokenPool(), OWNER) is None


@pytest.mark.asyncio
async def test_created_codes_are_issued_and_a_duplicate_is_refused():
    row = {"code": "FRIEND", "kind": "issued", "note": "For the meetup", "active": True, "created_by": "admin", "created_at": NOW}
    pool = FakePool({"INSERT INTO billing_referral_codes": row})
    out = await referrals.create(pool, " friend ", " For   the meetup ", "admin")
    assert out["code"] == "FRIEND" and out["created_at"] == NOW.isoformat() and out["note"] == "For the meetup"
    with pytest.raises(referrals.ReferralError):                       # the insert was refused: already exists
        await referrals.create(FakePool({}), "FRIEND", None, "admin")


@pytest.mark.asyncio
async def test_a_used_code_cannot_be_deleted_only_deactivated():
    used = FakePool({"SELECT count(*) FROM billing_orders": 2})
    with pytest.raises(referrals.ReferralError):
        await referrals.remove(used, "FRIEND")
    assert not used.statements("DELETE FROM billing_referral_codes")
    free = FakePool({"SELECT count(*) FROM billing_orders": 0, "DELETE FROM billing_referral_codes": {"code": "FRIEND"}})
    assert await referrals.remove(free, "friend") == {"code": "FRIEND", "deleted": True}
    with pytest.raises(referrals.NotFound):
        await referrals.remove(FakePool({"SELECT count(*) FROM billing_orders": 0}), "FRIEND")


@pytest.mark.asyncio
async def test_set_code_turns_one_off_and_a_missing_one_is_a_404():
    row = {"code": "FRIEND", "kind": "issued", "note": None, "active": False, "created_by": "admin", "created_at": NOW}
    out = await referrals.set_code(FakePool({"UPDATE billing_referral_codes": row}), "friend", active=False)
    assert out["active"] is False
    with pytest.raises(referrals.NotFound):
        await referrals.set_code(FakePool({}), "FRIEND", active=True)


@pytest.mark.asyncio
async def test_my_code_is_only_my_own_active_personal_code():
    mine = FakePool({"WHERE owner_user_id = $1::uuid AND active": {"code": "KELAB23CD", "kind": "personal"}})
    assert await referrals.mine(mine, OWNER) == {"code": "KELAB23CD", "kind": "personal"}
    assert await referrals.mine(FakePool({}), OWNER) is None
