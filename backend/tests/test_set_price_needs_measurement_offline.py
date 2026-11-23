"""A Way with no measured run cannot be priced (procedure_billing.set_price).

The rule is `pricing_discussions.md` D6: publishable, not sellable. It matters more since one purchase became one run
(docs/blinkit/what_was_done.md §2) — an unmeasured positive price can now lose money on every sale, with nobody able
to see it, because the run's cost was never attributed.

The tests pin what must stay permissive as hard as what must be refused: withdrawing a price and listing a Way free
have no margin to be wrong about, so neither may ever need a measurement.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services import procedure_billing as pb


class FakePool:
    """Records the statements run against it; answers the two SELECTs set_price makes."""

    def __init__(self, *, exists: bool = True, measured_runs: int = 0) -> None:
        self.exists = exists
        self.measured_runs = measured_runs
        self.updated: list[tuple] = []

    async def fetchrow(self, sql: str, *args):
        if "FROM procedures WHERE id::text = $1 AND t_invalid IS NULL" in sql:
            if not self.exists:
                return None
            return {"id": str(args[0]), "name": "NeurIPS report", "version": 3,
                    "measured_runs": self.measured_runs, "measured_cost_paise": None}
        if "UPDATE procedures" in sql:
            self.updated.append(args)
            return {"id": str(args[0]), "procedure_id": "p", "version": 3, "name": "NeurIPS report",
                    "price_paise": args[1], "price_currency": args[2]}
        return None


def _price(pool, paise, **kw):
    return asyncio.run(pb.set_price(pool, procedure_row_id="row-1", price_paise=paise, **kw))


def test_pricing_an_unmeasured_way_is_refused():
    pool = FakePool(measured_runs=0)
    with pytest.raises(pb.PurchaseError) as exc:
        _price(pool, 100)
    assert exc.value.status_code == 409
    assert pool.updated == [], "nothing may be written when the price is refused"


def test_the_refusal_says_run_it_once():
    """A refusal that only says 'no' leaves the operator stuck; this one says what to do."""
    with pytest.raises(pb.PurchaseError) as exc:
        _price(FakePool(measured_runs=0), 100)
    msg = str(exc.value)
    assert "has not been run" in msg and "run it once" in msg
    assert "NeurIPS report" in msg and "v3" in msg, "name the exact version being priced"


def test_un_pricing_never_needs_a_measurement():
    """Withdrawing a price cannot mislead anyone, so it must work on a Way nobody has run."""
    pool = FakePool(measured_runs=0)
    out = _price(pool, None)
    assert out["price_paise"] is None
    assert pool.updated


def test_a_free_listing_needs_no_measurement():
    """0 is a deliberate free listing: no margin to be wrong about."""
    pool = FakePool(measured_runs=0)
    _price(pool, 0)
    assert pool.updated, "a free listing is allowed without a measurement"


def test_a_measured_way_can_be_priced():
    pool = FakePool(measured_runs=1)
    out = _price(pool, 500)
    assert out["price_paise"] == 500


def test_a_missing_way_is_a_404_not_a_rule_violation():
    """No such Way and an unmeasured Way are different answers, and must not be conflated."""
    with pytest.raises(pb.PurchaseError) as exc:
        _price(FakePool(exists=False), 100)
    assert exc.value.status_code == 404


def test_an_out_of_range_price_is_refused_before_the_database():
    with pytest.raises(pb.PurchaseError) as exc:
        _price(FakePool(measured_runs=1), pb.MAX_PRICE_PAISE + 1)
    assert "between" in str(exc.value)


def test_a_negative_price_is_refused():
    with pytest.raises(pb.PurchaseError):
        _price(FakePool(measured_runs=1), -1)
