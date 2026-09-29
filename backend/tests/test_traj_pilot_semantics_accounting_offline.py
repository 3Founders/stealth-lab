"""
Proving tests for the step-0 pilot's paid-layer accounting.

The bug this closes: 13 of 13 extraction calls failed, $0.20 was spent, 0
knowledge items were produced, and the run reported "done" with exit code 0.
Nothing in the report distinguished three very different outcomes:

- **failure** -- the pass raised
- **abstention** -- the pass succeeded and legitimately found nothing
- **yield** -- the pass succeeded and produced knowledge items

A caller reading only "the run finished" cannot tell the first from the third,
which is how a totally broken paid path shipped as a result.

Offline: no database, no network, no LLM. `extract_trajectory_semantics` is
monkeypatched on the module the helper imports it from, so these tests are
about the pilot's accounting, not about the extraction itself (that is
`test_trajectory_semantics_async_and_budget_offline.py`).
"""
from __future__ import annotations

import argparse

import pytest

from app.ingestion import traj_pilot_cli as cli
from app.services.governance import BudgetExceeded
from app.services.procedure_extraction.schema import ExtractionTransientFailure
import app.services.trajectory_semantics as ts


def _args(**overrides):
    ns = argparse.Namespace(semantics=5, semantics_model="test-model", shard_dsn_env="X")
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


@pytest.fixture
def no_client(monkeypatch):
    monkeypatch.setattr(cli, "_llm_client", lambda: object())


# ------------------------------------------------------- the three outcomes

@pytest.mark.asyncio
async def test_a_pass_with_knowledge_items_counts_as_yield(monkeypatch, no_client):
    c = cli.PilotCounters()

    async def fake(pool, episode_id, **kw):
        return {"goals": 2, "claims": 1, "procedures": 1, "uncertainties": []}

    monkeypatch.setattr(ts, "extract_trajectory_semantics", fake)
    await cli._one_semantic_pass(None, c, "ep-1", _args())

    assert (c.semantics_attempted, c.semantics_succeeded, c.semantics_yielded) == (1, 1, 1)
    assert c.semantics_abstained == 0
    assert c.semantics_failed == 0
    assert c.semantics_knowledge_items == 4
    assert c.as_dict()["semantics_knowledge_items"] == 4


@pytest.mark.asyncio
async def test_a_successful_empty_pass_is_an_abstention_not_a_failure(monkeypatch, no_client):
    """A completed pass that found nothing is a real answer about this
    trajectory. Counting it as a failure would make a model that is honestly
    reporting emptiness look broken -- and would hide a genuinely broken one."""
    c = cli.PilotCounters()

    async def fake(pool, episode_id, **kw):
        return {"goals": 0, "claims": 0, "procedures": 0, "uncertainties": ["unclear"]}

    monkeypatch.setattr(ts, "extract_trajectory_semantics", fake)
    await cli._one_semantic_pass(None, c, "ep-1", _args())

    assert c.semantics_succeeded == 1
    assert c.semantics_abstained == 1
    assert c.semantics_yielded == 0
    assert c.semantics_failed == 0
    assert c.semantics_knowledge_items == 0


@pytest.mark.asyncio
async def test_a_raised_pass_is_a_failure_and_is_categorised(monkeypatch, no_client, capsys):
    c = cli.PilotCounters()

    async def fake(pool, episode_id, **kw):
        raise ExtractionTransientFailure("semantic extraction response was not valid JSON")

    monkeypatch.setattr(ts, "extract_trajectory_semantics", fake)
    await cli._one_semantic_pass(None, c, "ep-1", _args())

    assert c.semantics_failed == 1
    assert c.semantics_succeeded == 0
    assert c.semantics_abstained == 0
    assert c.as_dict()["semantics_failure_reasons"] == {"ExtractionTransientFailure": 1}
    assert "ExtractionTransientFailure" in capsys.readouterr().out, (
        "a failure must be visible in the run's output, not only in a counter"
    )


@pytest.mark.asyncio
async def test_a_transport_bug_is_counted_not_swallowed(monkeypatch, no_client):
    """The shape that actually happened: the client call blew up with an
    attribute error on a coroutine. Whatever the exception, it lands in the
    counters."""
    c = cli.PilotCounters()

    async def fake(pool, episode_id, **kw):
        raise AttributeError("'coroutine' object has no attribute 'choices'")

    monkeypatch.setattr(ts, "extract_trajectory_semantics", fake)
    await cli._one_semantic_pass(None, c, "ep-1", _args())

    assert c.semantics_failure_reasons == {"AttributeError": 1}
    assert not cli._semantics_ok(c)


@pytest.mark.asyncio
async def test_a_budget_stop_spends_nothing_and_stops_asking(monkeypatch, no_client):
    c = cli.PilotCounters()
    args = _args()

    async def fake(pool, episode_id, **kw):
        raise BudgetExceeded("ingestion model budget exceeded")

    monkeypatch.setattr(ts, "extract_trajectory_semantics", fake)
    await cli._one_semantic_pass(None, c, "ep-1", args)

    assert c.semantics_failure_reasons == {"budget_exceeded": 1}
    assert args.semantics == 0, (
        "the pilot must stop asking once the cap is gone, not spend the rest "
        "of the sub-sample on refusals"
    )


@pytest.mark.asyncio
async def test_no_configured_client_is_a_failure_not_a_skip(monkeypatch):
    """`--semantics N` was asked for and could not be honoured. Silently
    skipping is how a run reports a paid layer it never ran."""
    c = cli.PilotCounters()
    monkeypatch.setattr(cli, "_llm_client", lambda: None)

    async def must_not_run(*a, **kw):  # pragma: no cover
        raise AssertionError("no client, so no call")

    monkeypatch.setattr(ts, "extract_trajectory_semantics", must_not_run)
    await cli._one_semantic_pass(None, c, "ep-1", _args())

    assert c.semantics_attempted == 1
    assert c.semantics_failure_reasons == {"no_llm_client": 1}


# ---------------------------------------------------- the run-level verdict

def test_attempted_and_all_failed_is_not_ok():
    """This predicate is the exit code. 13/13 dead must read as not-ok."""
    c = cli.PilotCounters()
    c.semantics_attempted = 13
    for _ in range(13):
        c.semantics_failed_with("ExtractionTransientFailure")
    assert cli._semantics_ok(c) is False


def test_attempted_and_all_abstained_is_ok():
    c = cli.PilotCounters()
    c.semantics_attempted = 13
    c.semantics_succeeded = 13
    c.semantics_abstained = 13
    assert cli._semantics_ok(c) is True


def test_a_raw_only_run_is_ok():
    """`--semantics 0` spends nothing; there is no paid layer to have failed."""
    c = cli.PilotCounters()
    c.accepted = 713
    assert cli._semantics_ok(c) is True


def test_one_success_among_failures_is_ok_but_the_counts_still_tell_the_story():
    c = cli.PilotCounters()
    c.semantics_attempted = 10
    c.semantics_succeeded = 1
    c.semantics_failed = 9
    assert cli._semantics_ok(c) is True
    assert c.as_dict()["semantics_failed"] == 9, "ok must not erase the failure count"


def test_counters_serialise_the_new_fields():
    c = cli.PilotCounters()
    c.semantics_failed_with("boom")
    d = c.as_dict()
    for key in ("semantics_attempted", "semantics_succeeded", "semantics_abstained",
                "semantics_yielded", "semantics_failed", "semantics_failure_reasons",
                "semantics_knowledge_items"):
        assert key in d, key


# ------------------------------------------- the spend measurement's honesty

def test_spend_reading_returns_row_count_alongside_the_sum():
    """$0.00 next to 0 rows means one of two very different things: the run was
    free, or the money was never recorded. Only the row count tells them
    apart, so it is part of the reading rather than an afterthought."""
    import inspect

    src = inspect.getsource(cli._spend_between)
    assert "count(*)" in src
    assert "occured" not in src  # a typo guard on the window column
    assert "occurred_at >= $1 AND occurred_at <= $2" in src


def test_pilot_does_not_wrap_the_client_to_record_spend_anymore():
    """`extract_trajectory_semantics` records its own completion now. A second
    recorder in the pilot would double-count every call, and the report's
    $/knowledge-item figure would be inflated by exactly 2x."""
    assert not hasattr(cli, "_SpendRecordingClient")
    assert "flush_spend" not in dir(cli)
    src = inspect_source(cli._llm_client)
    assert "_SpendRecording" not in src


def inspect_source(fn) -> str:
    import inspect

    return inspect.getsource(fn)
