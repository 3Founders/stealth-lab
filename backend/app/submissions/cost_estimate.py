"""
What a Way will cost, before it has ever run.

The problem (docs/pricingestimation.md §0): you cannot price a Way before you know what it costs, you cannot know
what it costs before it runs, and a Way must show a price before anyone buys.

Two facts from the literature shape everything here:

1. **Agent token consumption varies up to 30x across runs of the same task** (TokenCast, arXiv 2609.35760).
   So a single number is a lie. This module returns a RANGE.

2. **Later steps re-read earlier steps.** TokenCast's composition rule: the cost of a segment is not just its own
   tokens, it is its own tokens *plus* the extra input every later call pays to re-read the context it produced.
   In kell, "every step sees the run's input and every earlier step's output, not just the last one"
   (app/execution/way_runs.py) — so summing per-step costs underestimates, and the error grows with Way length.

What this does NOT do: pretend to know the output length. It bounds it with the step's `output_cap_tokens`, which
is why that cap matters — the cap IS the upper bound of the estimate (§4.3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

# A character-to-token ratio, not a tokeniser. kell has none: /v1/messages/count_tokens is itself documented as
# "a rough estimate". ~4 chars per token is the usual English approximation, and it is only ever multiplied into a
# range that is already wide, so the approximation is not the dominant error.
CHARS_PER_TOKEN = 4.0

# How much of the cap an output actually uses, on a first run. Low is a step that stops early, high is one that
# runs to the limit. These bound the range; neither is a prediction.
OUTPUT_LOW_FRACTION = 0.30
OUTPUT_HIGH_FRACTION = 1.00

DEFAULT_OUTPUT_CAP = 1500          # tokens per step, when the step does not declare one
DEFAULT_IN_RATE_PER_M = 0.15       # USD per 1M input tokens (deepseek-flash off-peak)
DEFAULT_OUT_RATE_PER_M = 0.60      # USD per 1M output tokens
PAISE_PER_USD = 8400               # display conversion only; the charged figure is always the licence

# --- margin, and why it is not a markup -------------------------------------------------------------
#
# A 20-30% margin on the *measured* cost does not pay for the thing most likely to happen: a step fails and
# runs again. Retries are not an edge case — an agent that fails verification retries, and every retry re-reads the
# context, so a retry costs MORE than the first attempt, not the same.
#
# So the margin is applied to an expected cost that already contains the retry term:
#
#     expected = first_attempt_cost × (1 + retry_probability × retry_fraction)
#
# `retry_probability` is the chance a step needs another go; `retry_fraction` is how much of the first attempt that
# retry costs (a retry sees a longer context, so it is usually ≥ 1). At 15% and 1.0, one step in seven costs double:
# a 17% invisible uplift. A 20% margin on top of that is a margin; a 20% margin without it is a subsidy.
DEFAULT_RETRY_PROBABILITY = 0.15
DEFAULT_RETRY_FRACTION = 1.0
MIN_MARGIN = 0.20
MAX_MARGIN = 0.30

# --- locators, which the token estimate does not see ------------------------------------------------
#
# A step with a locator calls something outside the model: a URL fetch, an MCP tool, a script. That call has its
# own price — Exa returns an actual `costDollars.total`, and tool_gateway_calls.cost_usd exists for the rest — and
# the estimate must include it or it is wrong about the very steps that cost money outside tokens.
#
# Measured 2026-11-22: 21 of 30 stored steps carry a locator (14 tool, 7 url), and 0 of 130 recorded gateway calls
# has a cost recorded, so these rates are ESTIMATES from vendor price lists, not measurements. When cost_usd starts
# being written, `measured_locator_cost()` replaces them.
LOCATOR_RATE_USD: dict[str, float] = {
    "tool": 0.005,      # a typical metered MCP tool call (Exa search ~ $0.005; scraping/people higher)
    "url": 0.0005,      # one page fetch, no model
    "file": 0.0,        # a local read
    "script": 0.0002,   # a 2-minute sandbox term at Agent37 rates
}


@dataclass(frozen=True)
class StepCost:
    """One step's share of the estimate, with the numbers that produced it shown."""

    order: int
    prompt_tokens: int
    context_tokens: int      # the re-read term: everything earlier that this step pays for again
    output_low: int
    output_high: int
    cost_low_usd: float
    cost_high_usd: float
    locator_kind: Optional[str] = None       # tool | url | file | script, when the step calls something
    locator_usd: float = 0.0                 # that call's own price, which the token maths cannot see


@dataclass(frozen=True)
class CostEstimate:
    """A run's cost: the raw work, the retry-adjusted expectation, and the price to charge for it."""

    low_usd: float
    high_usd: float
    steps: list[StepCost] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    # What a retry actually adds, before any margin. `expected` is what the margin is applied to.
    retry_usd: float = 0.0
    retry_probability: float = DEFAULT_RETRY_PROBABILITY
    margin: float = MIN_MARGIN
    price_low_usd: float = 0.0
    price_high_usd: float = 0.0

    @property
    def low_paise(self) -> int:
        return round(self.low_usd * PAISE_PER_USD)

    @property
    def high_paise(self) -> int:
        return round(self.high_usd * PAISE_PER_USD)

    @property
    def price_low_paise(self) -> int:
        return round(self.price_low_usd * PAISE_PER_USD)

    @property
    def price_high_paise(self) -> int:
        return round(self.price_high_usd * PAISE_PER_USD)

    def as_dict(self) -> dict[str, Any]:
        return {
            "low_usd": round(self.low_usd, 4), "high_usd": round(self.high_usd, 4),
            "low_paise": self.low_paise, "high_paise": self.high_paise,
            "retry_usd": round(self.retry_usd, 6), "retry_probability": self.retry_probability,
            "margin": self.margin,
            "price_low_usd": round(self.price_low_usd, 4), "price_high_usd": round(self.price_high_usd, 4),
            "price_low_paise": self.price_low_paise, "price_high_paise": self.price_high_paise,
            "steps": [s.__dict__ for s in self.steps], "assumptions": self.assumptions,
        }


def approx_tokens(text: str) -> int:
    """A character-count estimate. Never zero for real text, because a step that reads something costs something."""
    if not text:
        return 0
    return max(1, round(len(text) / CHARS_PER_TOKEN))


def _step_prompt_text(step: Mapping[str, Any]) -> str:
    """The text a step sends: its prompt, its instruction, or its title — first non-empty."""
    for key in ("prompt", "do", "goal", "instruction", "description", "title"):
        v = step.get(key)
        if isinstance(v, str) and v.strip():
            return v
    params = step.get("binding", {}).get("parameters") if isinstance(step.get("binding"), Mapping) else None
    if isinstance(params, Mapping):
        p = params.get("prompt")
        if isinstance(p, str):
            return p
    return ""


def _locator_kind(step: Mapping[str, Any]) -> Optional[str]:
    """The kind of thing a step calls outside the model, or None for a pure prompt step."""
    loc = step.get("locator")
    if isinstance(loc, Mapping):
        t = loc.get("type")
        if isinstance(t, str) and t.strip():
            return t.strip().lower()
    binding = step.get("binding")
    if isinstance(binding, Mapping):
        b = binding.get("kind")
        # A model binding is still a prompt step; anything else reaches outside.
        if isinstance(b, str) and b.strip() and b.strip().lower() != "model":
            return "tool" if b.strip().lower() in ("mcp_tool", "tool") else b.strip().lower()
    return None


def estimate_cost(
    steps: Iterable[Mapping[str, Any]],
    *,
    run_input: str = "",
    in_rate_per_m: float = DEFAULT_IN_RATE_PER_M,
    out_rate_per_m: float = DEFAULT_OUT_RATE_PER_M,
    retry_probability: float = DEFAULT_RETRY_PROBABILITY,
    retry_fraction: float = DEFAULT_RETRY_FRACTION,
    margin: float = MIN_MARGIN,
    locator_rates: Optional[Mapping[str, float]] = None,
) -> CostEstimate:
    """A cost RANGE and a price RANGE for running these steps once.

    Three terms the naive estimate omits, each of which makes the real bill bigger:

    1. **The re-read term** — step i pays for every earlier step's output, not just its own prompt.
    2. **The locator term** — a step that calls a tool or a URL has a price the token maths cannot see.
    3. **The retry term** — a step that fails runs again, and a retry re-reads a longer context, so it costs *more*
       than the attempt that failed. The margin is applied on top of this, not instead of it.
    """
    rates = dict(LOCATOR_RATE_USD)
    if locator_rates:
        rates.update(locator_rates)
    margin = min(MAX_MARGIN, max(0.0, margin))
    retry_probability = min(1.0, max(0.0, retry_probability))

    ordered = sorted(
        (s for s in steps if isinstance(s, Mapping)),
        key=lambda s: int(s.get("order", s.get("step_order", 0)) or 0),
    )

    base_context = approx_tokens(run_input)
    grown_low = base_context
    grown_high = base_context

    per_step: list[StepCost] = []
    total_low = total_high = 0.0
    saw_locator = False

    for i, step in enumerate(ordered, start=1):
        order = int(step.get("order", step.get("step_order", i)) or i)
        own = approx_tokens(_step_prompt_text(step))
        cap = step.get("output_cap_tokens")
        cap = int(cap) if isinstance(cap, int) and cap > 0 else DEFAULT_OUTPUT_CAP

        out_low = max(1, round(cap * OUTPUT_LOW_FRACTION))
        out_high = cap

        kind = _locator_kind(step)
        loc_usd = float(rates.get(kind, 0.0)) if kind else 0.0
        if kind and loc_usd:
            saw_locator = True

        in_low = own + grown_low
        in_high = own + grown_high

        low = (in_low * in_rate_per_m + out_low * out_rate_per_m) / 1_000_000 + loc_usd
        high = (in_high * in_rate_per_m + out_high * out_rate_per_m) / 1_000_000 + loc_usd
        total_low += low
        total_high += high

        per_step.append(StepCost(
            order=order, prompt_tokens=own, context_tokens=max(0, grown_high),
            output_low=out_low, output_high=out_high,
            cost_low_usd=round(low, 6), cost_high_usd=round(high, 6),
            locator_kind=kind, locator_usd=round(loc_usd, 6),
        ))

        grown_low += out_low
        grown_high += out_high

    # Retries: expected work, then the margin on top of it.
    retry_low = total_low * retry_probability * retry_fraction
    retry_high = total_high * retry_probability * retry_fraction
    expected_low = total_low + retry_low
    expected_high = total_high + retry_high

    assumptions = [
        f"~{int(CHARS_PER_TOKEN)} characters per token (no tokeniser is available)",
        f"output per step bounded by its cap; low end assumes {int(OUTPUT_LOW_FRACTION * 100)}% of it",
        "every step re-reads all earlier outputs, as the executor does",
        f"rates: ${in_rate_per_m}/1M in, ${out_rate_per_m}/1M out (change with the model)",
        f"retries: {int(retry_probability * 100)}% of steps run again at {retry_fraction:.1f}x their cost",
        f"margin: {int(margin * 100)}% on the expected cost (retries included, not extra)",
        "cold cache: a repeat run is far cheaper and is not what this prices",
        "measured runs vary up to 30x (TokenCast); treat this as a bound, not a forecast",
    ]
    if saw_locator:
        assumptions.append("locator rates are vendor price-list estimates; gateway cost_usd is not recorded yet")

    return CostEstimate(
        low_usd=round(total_low, 6), high_usd=round(total_high, 6),
        steps=per_step, assumptions=assumptions,
        retry_usd=round((retry_low + retry_high) / 2, 6), retry_probability=retry_probability, margin=margin,
        price_low_usd=round(expected_low * (1 + margin), 6),
        price_high_usd=round(expected_high * (1 + margin), 6),
    )


def measured_after_first_run(run_outputs: Iterable[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    """The real figure, once a run has happened. `None` when there is nothing measured yet.

    This is the owner's rule, and it is the right one: the first successful run IS the measurement. Before it, a
    Way shows a range; after it, a number. The two must never be confused on the page, which is why this returns a
    different shape rather than degree-of-confidence on the same one.
    """
    char_counts = [len(str(o.get("output") or "")) for o in run_outputs if isinstance(o, Mapping)]
    if not char_counts:
        return None
    total_chars = sum(char_counts)
    return {"measured": True, "steps": len(char_counts),
            "output_tokens_approx": max(1, round(total_chars / CHARS_PER_TOKEN)),
            "note": "measured from a completed run; output tokens are an approximation from its character count"}
