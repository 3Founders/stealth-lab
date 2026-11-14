"""A manual pin (P1-03) keeps a place in its Goal's tool list: after the leader, never instead of it (goal_router._keep_pins)."""
from app.tool_routing.goal_router import MAX_TOOLS_PER_GOAL, ToolHit, _keep_pins


def hit(i, pinned=False):
    return ToolHit(card_id=f"c{i}", name="", server="", title="", description="", source="", score=0.0, pinned=pinned)


def test_a_pin_below_the_cut_comes_back_second_and_the_list_stays_the_same_length():
    pool = [hit(i) for i in range(12)] + [hit(99, pinned=True)]
    out = _keep_pins(pool[:MAX_TOOLS_PER_GOAL], pool)
    assert [t.card_id for t in out][:2] == ["c0", "c99"]
    assert len(out) == MAX_TOOLS_PER_GOAL and "c7" not in [t.card_id for t in out]


def test_a_pin_already_in_the_list_and_lists_without_pins_are_left_alone():
    pool = [hit(0), hit(1, pinned=True), hit(2)]
    assert [t.card_id for t in _keep_pins(pool, pool)] == ["c0", "c1", "c2"]
    plain = [hit(i) for i in range(10)]
    assert _keep_pins(plain[:MAX_TOOLS_PER_GOAL], plain) == plain[:MAX_TOOLS_PER_GOAL]
