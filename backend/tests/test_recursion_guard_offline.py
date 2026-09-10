"""
MCP hardening B9-B13: pure-logic half of the recursion guard --
`AncestorChain.depth` and `assert_no_cycle` need no database at all.
"""
import pytest

from app.execution.recursion_guard import AncestorChain, RecursionCycleDetected, assert_no_cycle


def _chain(*procedure_ids: str) -> AncestorChain:
    return AncestorChain(
        run_ids=[f"run-{i}" for i in range(len(procedure_ids))],
        procedure_ids=list(procedure_ids),
        root_run_id="run-0",
        root_started_at=None,
    )


def test_depth_is_chain_length_plus_one():
    assert _chain().depth == 1
    assert _chain("A").depth == 2
    assert _chain("A", "B", "C").depth == 4


def test_assert_no_cycle_passes_for_a_genuinely_new_procedure():
    chain = _chain("A", "B")
    assert_no_cycle(chain, "C")  # must not raise


def test_assert_no_cycle_detects_direct_self_invocation():
    chain = _chain("A")
    with pytest.raises(RecursionCycleDetected):
        assert_no_cycle(chain, "A")


def test_assert_no_cycle_detects_a_deeper_ancestor_match():
    # A -> B -> C, and C now tries to invoke A again.
    chain = _chain("A", "B", "C")
    with pytest.raises(RecursionCycleDetected):
        assert_no_cycle(chain, "A")


def test_assert_no_cycle_message_names_the_offending_procedure():
    chain = _chain("A")
    with pytest.raises(RecursionCycleDetected, match="A"):
        assert_no_cycle(chain, "A")
