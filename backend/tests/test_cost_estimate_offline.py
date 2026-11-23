"""Cost estimation before a Way has run (app/submissions/cost_estimate.py, docs/pricingestimation.md).

The two claims worth testing, because both are easy to get wrong and both make the page lie:

  1. **The answer is a range, never a point.** Agent token consumption varies up to 30x.
  2. **Later steps pay to re-read earlier outputs.** A per-step sum underestimates, and the error grows with the
     Way's length — so a long Way must cost more than the sum of its steps, not the same.
"""
from __future__ import annotations

import pytest

from app.submissions.cost_estimate import (
    DEFAULT_OUTPUT_CAP,
    approx_tokens,
    estimate_cost,
    measured_after_first_run,
)


def _steps(n: int, prompt_chars: int = 400, cap: int | None = None) -> list[dict]:
    out = []
    for i in range(1, n + 1):
        s = {"order": i, "prompt": "x" * prompt_chars}
        if cap is not None:
            s["output_cap_tokens"] = cap
        out.append(s)
    return out


def test_the_answer_is_a_range():
    e = estimate_cost(_steps(3))
    assert e.low_usd > 0
    assert e.high_usd > e.low_usd, "a single number would be a lie; runs vary up to 30x"


def test_later_steps_cost_more_than_earlier_ones():
    """The re-read term: step 5 reads steps 1-4, so it must not cost what step 1 costs."""
    e = estimate_cost(_steps(5))
    first, last = e.steps[0], e.steps[-1]
    assert last.cost_high_usd > first.cost_high_usd
    assert last.context_tokens > first.context_tokens


def test_a_long_way_costs_more_than_the_sum_of_its_parts():
    """The error a naive estimator makes: summing steps misses the accumulated context."""
    steps = _steps(6)
    e = estimate_cost(steps)
    naive = sum(1 for _ in steps) * e.steps[0].cost_high_usd
    assert e.high_usd > naive, "composing must exceed the sum of equal-sized steps"


def test_the_cap_bounds_the_estimate():
    """The cap IS the upper bound — that is why an uncapped step makes pricing unquotable."""
    low = estimate_cost(_steps(3, cap=500))
    high = estimate_cost(_steps(3, cap=5000))
    assert high.high_usd > low.high_usd
    assert low.high_usd < high.low_usd * 20  # stays finite and proportional


def test_a_step_with_no_cap_uses_the_default_rather_than_unbounded():
    e = estimate_cost(_steps(2))
    assert e.steps[0].output_high == DEFAULT_OUTPUT_CAP


def test_the_run_input_counts_toward_the_context():
    bare = estimate_cost(_steps(2))
    fed = estimate_cost(_steps(2), run_input="y" * 4000)
    assert fed.high_usd > bare.high_usd


def test_every_estimate_states_its_assumptions():
    """A number without its assumptions cannot be argued with, which is the point of showing them."""
    e = estimate_cost(_steps(2))
    assert len(e.assumptions) >= 4
    assert any("character" in a for a in e.assumptions)
    assert any("30x" in a for a in e.assumptions)


def test_paise_conversion_is_derived_from_usd_not_invented():
    e = estimate_cost(_steps(2))
    assert e.low_paise == round(e.low_usd * 8400)


def test_approx_tokens_is_never_zero_for_real_text():
    assert approx_tokens("") == 0
    assert approx_tokens("a") >= 1


def test_steps_out_of_order_are_sorted_before_composing():
    """The composition depends on order; taking the list as given would silently mis-cost a shuffled Way."""
    shuffled = [{"order": 2, "prompt": "b" * 400}, {"order": 1, "prompt": "a" * 400}]
    e = estimate_cost(shuffled)
    assert [s.order for s in e.steps] == [1, 2]


def test_no_measurement_before_a_run():
    assert measured_after_first_run([]) is None


def test_a_completed_run_reports_measured_rather_than_estimated():
    m = measured_after_first_run([{"output": "x" * 4000}, {"output": "y" * 2000}])
    assert m is not None
    assert m["measured"] is True
    assert m["steps"] == 2
    assert m["output_tokens_approx"] > 0


# --- retries and margin -----------------------------------------------------------------------------


def test_the_margin_lands_in_the_20_to_30_percent_band():
    e = estimate_cost(_steps(4), margin=0.20)
    assert 0.20 <= e.margin <= 0.30


def test_the_price_includes_retries_not_only_markup():
    """A 20% margin on a cost with no retry term is a 20% margin on an underestimate."""
    e = estimate_cost(_steps(4), margin=0.20)
    assert e.retry_usd > 0
    naked = e.low_usd * (1 + 0.20)
    assert e.price_low_usd > naked, "the price must cover retries as well as margin"


def test_a_zero_retry_probability_leaves_only_the_markup():
    e = estimate_cost(_steps(3), retry_probability=0.0, margin=0.25)
    assert e.retry_usd == 0
    # `low_usd` is stored rounded to 6dp, so compare to that precision rather than to a float's noise.
    assert abs(e.price_low_usd - e.low_usd * 1.25) < 1e-6


def test_more_retries_cost_more():
    rare = estimate_cost(_steps(3), retry_probability=0.10)
    often = estimate_cost(_steps(3), retry_probability=0.50)
    assert often.price_high_usd > rare.price_high_usd


def test_margin_above_the_ceiling_is_clamped_to_30_percent():
    """The band is 20-30; a caller cannot quietly charge 80% by passing 0.8."""
    e = estimate_cost(_steps(2), margin=0.80)
    assert e.margin == 0.30


def test_the_assumptions_state_both_the_retry_rate_and_the_margin():
    """A price a reader cannot audit is a price they should not trust."""
    e = estimate_cost(_steps(2), retry_probability=0.15, margin=0.25)
    joined = " ".join(e.assumptions)
    assert "retries" in joined and "15%" in joined
    assert "margin" in joined and "25%" in joined


# --- locators ---------------------------------------------------------------------------------------


def test_a_tool_step_costs_more_than_a_prompt_step_with_the_same_prompt():
    """21 of 30 stored steps carry a locator; the token maths cannot see their price."""
    prompt_only = [{"order": 1, "prompt": "x" * 400}]
    with_tool = [{"order": 1, "prompt": "x" * 400, "locator": {"type": "tool", "value": "exa.search"}}]
    assert estimate_cost(with_tool).low_usd > estimate_cost(prompt_only).low_usd


def test_a_url_step_costs_more_than_a_bare_prompt():
    bare = [{"order": 1, "prompt": "x" * 400}]
    fetched = [{"order": 1, "prompt": "x" * 400, "locator": {"type": "url", "value": "https://example.com"}}]
    assert estimate_cost(fetched).low_usd > estimate_cost(bare).low_usd


def test_a_model_binding_is_not_a_locator():
    """A step bound to a model makes no outside call; charging it a tool rate would be wrong."""
    bound = [{"order": 1, "prompt": "x" * 400, "binding": {"kind": "model"}}]
    bare = [{"order": 1, "prompt": "x" * 400}]
    assert estimate_cost(bound).low_usd == estimate_cost(bare).low_usd


def test_the_step_reports_which_locator_it_charged_for():
    e = estimate_cost([{"order": 1, "prompt": "x", "locator": {"type": "tool", "value": "exa.search"}}])
    assert e.steps[0].locator_kind == "tool"
    assert e.steps[0].locator_usd > 0


def test_locator_rates_are_overridable_so_a_real_price_can_replace_the_estimate():
    steps = [{"order": 1, "prompt": "x", "locator": {"type": "tool", "value": "exa.search"}}]
    cheap = estimate_cost(steps, locator_rates={"tool": 0.001})
    dear = estimate_cost(steps, locator_rates={"tool": 0.050})
    assert dear.low_usd > cheap.low_usd


def test_a_locator_estimate_says_it_is_an_estimate():
    e = estimate_cost([{"order": 1, "prompt": "x", "locator": {"type": "tool", "value": "t"}}])
    assert any("locator" in a for a in e.assumptions), "the page must disclose that locator cost is guessed"


# --- per-step pricing, so a restart from the middle is priced --------------------------------------------

def test_per_step_prices_sum_to_the_whole_way():
    """If they did not, a person restarting from step 2 would be charged a different total than a full run —
    the arithmetic on the page would be a lie."""
    from app.submissions.cost_estimate import _step_costs_exact, estimate_cost, per_step_prices

    steps = _steps(5)
    whole = estimate_cost(steps)
    prices = per_step_prices(steps)
    mult = (1.0 + whole.retry_probability * 1.0) * (1.0 + whole.margin)
    exact = _step_costs_exact(steps)
    # Compare against the SAME unrounded accumulators the whole estimate sums, then against the rounded whole.
    assert abs(sum(p["price_high_usd"] for p in prices) - sum(h for _, _, h in exact) * mult) < 1e-12
    assert round(sum(p["price_high_usd"] for p in prices), 6) == whole.price_high_usd
    assert round(sum(p["price_low_usd"] for p in prices), 6) == whole.price_low_usd


def test_every_step_gets_its_own_price():
    from app.submissions.cost_estimate import per_step_prices

    prices = per_step_prices(_steps(4))
    assert [p["order"] for p in prices] == [1, 2, 3, 4]
    assert all(p["price_high_usd"] > 0 for p in prices)


def test_a_later_step_is_priced_above_an_earlier_one():
    """The re-read term again: step 5 reads steps 1-4, so it cannot cost what step 1 costs."""
    from app.submissions.cost_estimate import per_step_prices

    prices = per_step_prices(_steps(5))
    assert prices[-1]["price_high_usd"] > prices[0]["price_high_usd"]


def test_a_restart_from_the_middle_pays_only_for_what_remains():
    """The point of splitting the price: steps that already produced good output are not re-paid for."""
    from app.submissions.cost_estimate import per_step_prices, suffix_from

    prices = per_step_prices(_steps(5))
    rest = suffix_from(prices, 3)
    expected = sum(p["price_high_usd"] for p in prices if p["order"] >= 3)
    assert abs(rest["price_high_usd"] - expected) < 1e-12
    assert rest["steps_paid_for"] == 3


def test_a_restart_from_step_one_is_the_whole_run():
    from app.submissions.cost_estimate import per_step_prices, suffix_from

    prices = per_step_prices(_steps(4))
    whole = sum(p["price_high_usd"] for p in prices)
    assert abs(suffix_from(prices, 1)["price_high_usd"] - whole) < 1e-12


def test_a_restart_from_the_last_step_is_the_cheapest_possible_re_run():
    from app.submissions.cost_estimate import per_step_prices, suffix_from

    prices = per_step_prices(_steps(5))
    rest = suffix_from(prices, 5)
    assert rest["steps_paid_for"] == 1
    assert rest["price_high_usd"] == prices[-1]["price_high_usd"]


def test_the_restart_listing_marks_what_has_already_run():
    """The page shows the arithmetic, so a reader can see why the restart is cheaper."""
    from app.submissions.cost_estimate import per_step_prices, suffix_from

    rest = suffix_from(per_step_prices(_steps(3)), 2)
    flags = {s["order"]: s["already_run"] for s in rest["steps"]}
    assert flags == {1: True, 2: False, 3: False}


def test_a_human_step_is_marked_so_it_is_not_priced_as_model_work():
    from app.submissions.cost_estimate import per_step_prices

    prices = per_step_prices({"order": 1, "prompt": "x", "kind": "human", "title": "Sign the form"})
    assert prices[0]["is_human"] is True
