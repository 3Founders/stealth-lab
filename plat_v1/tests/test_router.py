"""Router selection: the quality bar, cost ordering, and the cache bypass."""
from __future__ import annotations

from uuid import uuid4


from app.services.cache import CacheHit
from app.services.router import Router
from tests.helpers import FakeRouterStore, implementation

TASK = uuid4()


async def test_picks_the_cheapest_implementation_that_clears_the_bar():
    weak = implementation("weak_but_free", cost=0.0)
    good = implementation("cheap_and_good", cost=0.02)
    best = implementation("expensive_and_best", cost=0.10)

    store = FakeRouterStore(
        implementations=[weak, good, best],
        scores={weak.id: 0.50, good.id: 0.90, best.id: 0.95},
    )
    # bar = 0.95 - 0.05 = 0.90, so `weak` is excluded despite being free.
    decision = await Router(store, tolerance=0.05).route(TASK, fingerprint=None)

    assert decision.selected.name == "cheap_and_good"
    assert [i.name for i in decision.alternatives] == ["expensive_and_best"]


async def test_latency_breaks_a_cost_tie():
    slow = implementation("slow", cost=0.0, latency=5000)
    fast = implementation("fast", cost=0.0, latency=50)
    store = FakeRouterStore(implementations=[slow, fast])

    decision = await Router(store).route(TASK, fingerprint=None)
    assert decision.selected.name == "fast"


async def test_no_eval_data_orders_by_cost_alone():
    store = FakeRouterStore(
        implementations=[implementation("pricey", 0.5), implementation("free", 0.0)]
    )
    decision = await Router(store).route(TASK, fingerprint=None)

    assert decision.selected.name == "free"
    assert "no eval data" in decision.reason


async def test_unscored_implementation_is_not_excluded_by_the_bar():
    """
    Otherwise a newly registered implementation can never run, so can never
    be scored, so can never become eligible.
    """
    scored = implementation("measured", cost=0.10)
    fresh = implementation("brand_new", cost=0.0)
    store = FakeRouterStore(implementations=[scored, fresh], scores={scored.id: 0.9})

    decision = await Router(store, tolerance=0.05).route(TASK, fingerprint=None)
    assert decision.selected.name == "brand_new"


async def test_explicit_quality_bar_overrides_the_measured_one():
    weak = implementation("weak", cost=0.0)
    strong = implementation("strong", cost=0.10)
    store = FakeRouterStore(
        implementations=[weak, strong], scores={weak.id: 0.5, strong.id: 0.95}
    )

    decision = await Router(store).route(TASK, fingerprint=None, quality_bar=0.9)
    assert decision.selected.name == "strong"


async def test_an_explicit_quality_bar_nothing_meets_fails_the_stage():
    """
    A bar the caller asked for is a constraint, not a hint. Running something
    they explicitly excluded would be worse than failing.
    """
    weak = implementation("weak", cost=0.0)
    store = FakeRouterStore(implementations=[weak], scores={weak.id: 0.2})

    decision = await Router(store).route(TASK, fingerprint=None, quality_bar=0.99)

    assert not decision.found
    assert "requested quality bar" in decision.reason


async def test_a_derived_bar_nothing_meets_falls_back_instead():
    """
    The derived bar is a heuristic from eval history. Failing a stage untried
    because every implementation sits just under it would be worse than
    trying the best one available.
    """
    weak = implementation("weak", cost=0.0)
    strong = implementation("strong", cost=1.0)
    # Best measured is 0.2, so the derived bar is 0.15 and `weak` clears it;
    # drop both below by making the only scored one the *other* candidate.
    store = FakeRouterStore(
        implementations=[weak, strong], scores={weak.id: 0.2, strong.id: 0.9}
    )
    # Derived bar = 0.9 - 0.05 = 0.85, which only `strong` clears.
    decision = await Router(store, tolerance=0.05).route(TASK, fingerprint=None)
    assert decision.selected.name == "strong"

    # Now make nothing clear it: both scored well below the best-ever.
    store.scores = {weak.id: 0.2, strong.id: 0.2}
    decision = await Router(store, tolerance=0.0).route(TASK, fingerprint=None, quality_bar=None)
    assert decision.found  # fell back rather than failing


async def test_max_cost_excludes_everything_and_the_stage_is_not_routed():
    store = FakeRouterStore(implementations=[implementation("pricey", 1.0)])
    decision = await Router(store).route(TASK, fingerprint=None, max_cost=0.01)

    assert not decision.found
    assert "max_cost" in decision.reason


async def test_cache_hit_bypasses_routing_entirely():
    entry = uuid4()
    cached_impl = uuid4()
    store = FakeRouterStore(
        implementations=[implementation("would_have_been_picked", 0.0)],
        cache_hit=CacheHit(
            entry_id=entry, implementation_id=cached_impl, params={"layout": "v2"}, hits=3
        ),
    )

    decision = await Router(store).route(TASK, fingerprint="abc123")

    assert decision.from_cache
    assert decision.selected.id == cached_impl
    assert decision.params == {"layout": "v2"}
    # The whole point: no candidate enumeration, no eval lookup, no sort.
    assert store.enumeration_calls == 0
    assert store.recorded_hits == [entry]
    # A hit carries no escalation order -- the executor re-routes if it fails.
    assert decision.alternatives == []


async def test_ignore_cache_forces_a_real_route():
    store = FakeRouterStore(
        implementations=[implementation("real", 0.0)],
        cache_hit=CacheHit(uuid4(), uuid4(), {}, 1),
    )
    decision = await Router(store).route(TASK, fingerprint="abc", ignore_cache=True)

    assert not decision.from_cache
    assert decision.selected.name == "real"
    assert store.enumeration_calls == 1


async def test_no_implementations_is_reported_not_raised():
    decision = await Router(FakeRouterStore()).route(TASK, fingerprint=None)
    assert not decision.found
    assert "no enabled implementation" in decision.reason
