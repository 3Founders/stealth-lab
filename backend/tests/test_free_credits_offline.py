"""Free credit by code (db/183): COMPANYTRIAL gives $0.25 once to each of ten people, with no payment, and does not unlock the paid plan."""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.services import free_credits, referrals

USER = "11111111-1111-4111-8111-111111111111"


class Conn:
    def __init__(self, state):
        self.s = state

    async def execute(self, sql, *a):
        if "INSERT INTO free_credit_redemptions" in sql:
            self.s["redeemed"].add(a[1])
            if a[3]:
                self.s["phones"][a[3]] = a[1]
        if "INSERT INTO org_memberships" in sql:
            self.s["members"].append(a[1])

    async def fetchrow(self, sql, *a):
        return self.s["code_row"] if "FROM billing_referral_codes" in sql else None

    async def fetchval(self, sql, *a):
        if "count(*) FROM free_credit_redemptions" in sql:
            return len(self.s["redeemed"])
        if "1 FROM free_credit_redemptions WHERE code = $1 AND phone_hash" in sql:
            return 1 if a[1] in self.s["phones"] else None
        if "1 FROM free_credit_redemptions" in sql:
            return 1 if a[1] in self.s["redeemed"] else None
        if "INSERT INTO org_wallet_credits" in sql:
            if a[1] in self.s["credits"]:
                return None
            self.s["credits"][a[1]] = a[2]
            return 1
        return None


@pytest.fixture
def state(monkeypatch):
    st = {"redeemed": set(), "credits": {}, "code_row": {"grant_usd": "0.25", "max_uses": 10, "org": None, "require_phone": False}, "members": [], "phones": {}}

    @asynccontextmanager
    async def tx(pool, scope):
        yield Conn(st)

    monkeypatch.setattr(free_credits, "tenant_transaction", tx)
    monkeypatch.setenv("ORG_KEY_ENCRYPTION_KEY", "test-key")
    return st


@pytest.mark.asyncio
async def test_a_person_gets_the_credit_once_without_paying(state):
    out = await free_credits.redeem(object(), user_id=USER, code=" companytrial ")
    assert out["code"] == "COMPANYTRIAL" and out["credited_usd"] == "0.25" and out["places_left"] == 9 and not out["already"]
    assert str(state["credits"][f"free:COMPANYTRIAL:{USER}"]) == "0.25"
    with pytest.raises(free_credits.FreeCodeError) as e:
        await free_credits.redeem(object(), user_id=USER, code="COMPANYTRIAL")
    assert e.value.status == 409 and len(state["credits"]) == 1


@pytest.mark.asyncio
async def test_the_eleventh_person_is_refused(state):
    for i in range(10):
        await free_credits.redeem(object(), user_id=f"00000000-0000-4000-8000-{i:012d}", code="COMPANYTRIAL")
    with pytest.raises(free_credits.FreeCodeError) as e:
        await free_credits.redeem(object(), user_id=USER, code="COMPANYTRIAL")
    assert e.value.status == 409 and "full" in str(e.value) and USER not in state["redeemed"]


@pytest.mark.asyncio
async def test_a_redemption_recorded_without_its_credit_is_completed_on_retry(state):
    state["redeemed"].add(USER)                       # the process died after the first write
    out = await free_credits.redeem(object(), user_id=USER, code="COMPANYTRIAL")
    assert out["already"] is True and f"free:COMPANYTRIAL:{USER}" in state["credits"]


@pytest.mark.asyncio
async def test_an_unknown_or_ordinary_code_is_not_recognised(state):
    state["code_row"] = None
    with pytest.raises(free_credits.FreeCodeError) as e:
        await free_credits.redeem(object(), user_id=USER, code="NOSUCHCODE")
    assert e.value.status == 404
    with pytest.raises(free_credits.FreeCodeError):
        await free_credits.redeem(object(), user_id=USER, code="ab")


def test_a_free_credit_code_does_not_unlock_the_paid_plan():
    import inspect

    assert "grant_usd IS NULL" in inspect.getsource(referrals.active_codes)


TEAM = "22222222-2222-4222-8222-222222222222"


@pytest.mark.asyncio
async def test_a_team_code_makes_the_person_a_member_and_needs_a_verified_phone_once_per_number(state):
    state["code_row"] = {"grant_usd": "0.25", "max_uses": 10, "org": TEAM, "require_phone": True}
    with pytest.raises(free_credits.FreeCodeError) as e:                       # no confirmed phone on the sign-in
        await free_credits.redeem(object(), user_id=USER, code="COMPANYTRIAL")
    assert e.value.status == 403 and "verify your phone" in str(e.value) and not state["members"]
    out = await free_credits.redeem(object(), user_id=USER, code="COMPANYTRIAL", phone="919876543210")
    assert out["kind"] == "team" and out["team"] == TEAM and state["members"] == [USER] and not state["credits"]
    other = "00000000-0000-4000-8000-000000000009"
    with pytest.raises(free_credits.FreeCodeError) as e:                       # the same number from a second account
        await free_credits.redeem(object(), user_id=other, code="COMPANYTRIAL", phone="+91 98765 43210")
    assert e.value.status == 409 and "phone number" in str(e.value) and other not in state["members"]
    assert all("9876543210" not in h for h in state["phones"])                 # only a hash is kept


def test_a_number_is_read_as_digits_and_a_short_or_empty_one_is_nothing():
    assert free_credits.phone_digits("+91 98765-43210") == "919876543210"
    assert free_credits.phone_digits("") == "" and free_credits.phone_digits("12345") == "" and free_credits.phone_digits(None) == ""


@pytest.mark.asyncio
async def test_the_code_check_describes_a_free_code_and_ignores_a_plan_code(state):
    state["code_row"] = {"grant_usd": "0.25", "max_uses": 10, "team": True}
    state["redeemed"].update({"a", "b"})
    assert await free_credits.describe(object(), " companytrial ") == {"kind": "free", "credit_usd": "0.25", "team": True, "places_left": 8}
    state["code_row"] = None                                                   # an ordinary referral code, or no such code
    assert await free_credits.describe(object(), "FIRSTTRIAL") is None
    assert await free_credits.describe(object(), "ab") is None
