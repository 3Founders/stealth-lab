"""
Proving tests for the step-0 B1 fix: the paid extraction call is genuinely
async, it is gated before it spends, and a failed pass can never be read as an
abstention.

Offline by construction: `DATABASE_URL` unset, hand-rolled fakes, no network,
no real LLM. The budget is exercised through the real `ingest_budget` module
with a `CostGovernor`-shaped fake pool, not by monkeypatching `guard` to a
no-op -- a test that stubs the gate proves nothing about the gate.

Why each test exists
--------------------
The step-0 pilot ran 1,000 trajectories, wrote 713 episodes, and produced 0
Goals / 0 Claims / 0 Procedures: 13 of 13 extraction calls died and the run
still reported "done". Three independent causes are pinned here.

1. `client.chat.completions.create(...)` was called inline inside an `async
   def`. With a sync `OpenAI` that blocks the event loop for the length of the
   call; with an `AsyncOpenAI` it returns an un-awaited coroutine and dies on
   `.choices` *after* the request was already paid for. Both shapes are
   covered, because both are real clients in this codebase.
2. Nothing checked the daily cap before spending. The half-gate rule says the
   enforcement trigger lands in the same change as the writer, so the guard and
   the ledger write are asserted together.
3. A failure was indistinguishable from "the model found nothing", so 13
   failures read as a completed run.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid

import pytest

import app.services.trajectory_semantics as ts
from app.services.governance import BudgetExceeded
from app.services.ingest_budget import BudgetStatus
from app.services.procedure_extraction.schema import ExtractionTransientFailure


# ----------------------------------------------------------------- fakes

class FakePool:
    def __init__(self, episode_row, event_rows):
        self._episode_row = episode_row
        self._event_rows = event_rows
        self.executed: list[tuple] = []
        self._extraction_id = str(uuid.uuid4())

    async def fetchrow(self, sql, *args):
        if "FROM episodes WHERE id" in sql:
            return dict(self._episode_row)
        if "INSERT INTO trajectory_extractions" in sql:
            return {"id": self._extraction_id}
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def fetch(self, sql, *args):
        if "FROM trace_events" in sql:
            return [dict(r) for r in self._event_rows]
        raise AssertionError(f"unexpected fetch: {sql}")

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    def status_updates(self) -> list[str]:
        out = []
        for sql, _ in self.executed:
            for state in ("completed", "failed"):
                if f"status='{state}'" in sql:
                    out.append(state)
        return out


class FakeChoice:
    def __init__(self, content, usage=None):
        self.message = type("M", (), {"content": content})()
        self.usage = usage


class FakeUsage:
    def __init__(self, prompt_tokens=1200, completion_tokens=400):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class FakeResponse:
    def __init__(self, content, usage=None):
        self.choices = [FakeChoice(content, usage)]
        self.usage = usage


class SyncClient:
    """The shape `ingestion_jobs._general_compute_client()` actually returns.

    Records the thread it was called on, because "was this on the event loop
    thread" is the whole point of the fix.
    """

    def __init__(self, text, *, delay_s: float = 0.0, usage=None):
        self._text = text
        self._delay = delay_s
        self._usage = usage
        self.calls = 0
        self.threads: list[int] = []

    class _Completions:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            outer = self._outer
            outer.calls += 1
            outer.threads.append(threading.get_ident())
            if outer._delay:
                time.sleep(outer._delay)
            return FakeResponse(outer._text, outer._usage)

    @property
    def chat(self):
        outer = self

        class _Chat:
            completions = SyncClient._Completions(outer)

        return _Chat()


class AsyncClient:
    """The shape that used to die with `'coroutine' object has no attribute
    'choices'` -- every call is awaited by the caller under test."""

    def __init__(self, text, *, usage=None):
        self._text = text
        self._usage = usage
        self.calls = 0

    class _Completions:
        def __init__(self, outer):
            self._outer = outer

        async def create(self, **kwargs):
            self._outer.calls += 1
            await asyncio.sleep(0)
            return FakeResponse(self._outer._text, self._outer._usage)

    @property
    def chat(self):
        outer = self

        class _Chat:
            completions = AsyncClient._Completions(outer)

        return _Chat()


class LedgerPool:
    """What `CostGovernor` asks a pool for: the rolling 24h spend and an insert."""

    def __init__(self, spent: float):
        self.spent = spent
        self.inserted: list[tuple] = []

    async def fetchval(self, sql, *args):
        return self.spent

    async def execute(self, sql, *args):
        self.inserted.append((sql, args))


def _episode_row(**overrides):
    row = {
        "id": "episode-1", "session_id": "sess-1", "project_id": None,
        "metadata": {}, "start_ts": None, "end_ts": None, "owner_id": None,
        "visibility": "public", "scope_type": None, "scope_entity_id": None,
    }
    row.update(overrides)
    return row


def _event_row(idx, tool_name="Bash", canonical="EXECUTE"):
    return {
        "id": f"00000000-0000-0000-0000-{idx:012d}",
        "sequence": idx, "event_type": "PostToolUse", "canonical_event_type": canonical,
        "tool_name": tool_name, "tool_input": {"command": "pytest"}, "tool_output": {},
        "success": True, "timestamp": None,
    }


def _patch_writers(monkeypatch):
    async def fake_find_or_create_goal(pool, *, canonical_name, **kwargs):
        return {"id": f"goal-{canonical_name[:8]}", "canonical_name": canonical_name, "created": True}

    async def fake_capture_claim(pool, *, statement, **kwargs):
        return "claim-1"

    async def fake_capture_procedure(pool, **kwargs):
        return {"id": "procver-1", "procedure_id": "proc-1"}

    monkeypatch.setattr(ts, "find_or_create_goal", fake_find_or_create_goal)
    monkeypatch.setattr(ts, "capture_claim", fake_capture_claim)
    monkeypatch.setattr(ts, "capture_procedure", fake_capture_procedure)


def _payload(**overrides):
    payload = {
        "primary_goal": {
            "text": "fix the failing test", "event_indices": [1],
            "epistemic_status": "observed", "confidence": 0.8,
        },
        "subgoals": [], "candidate_procedures": [], "claims": [],
        "preconditions": [], "failure_modes": [], "recovery_patterns": [],
        "verification_actions": [], "reusable_elements": [],
        "outcome": "success", "uncertainties": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


@pytest.fixture
def budget_off():
    """`ingest_budget` is a process-global singleton; never leak one into the
    next test."""
    from app.services import ingest_budget

    ingest_budget.uninstall()
    yield
    ingest_budget.uninstall()


# ------------------------------------------------- 1. the async call itself

@pytest.mark.asyncio
async def test_sync_client_call_runs_off_the_event_loop_thread(monkeypatch, budget_off):
    """A sync `OpenAI` is what `_general_compute_client()` returns. Calling
    `.create()` inline inside this `async def` froze the loop for the whole
    call -- which on the MCP server (one process, one loop) stalls every other
    request. `run_blocking` must put it on a worker thread."""
    pool = FakePool(_episode_row(), [_event_row(1)])
    _patch_writers(monkeypatch)
    client = SyncClient(_payload(), delay_s=0.05, usage=FakeUsage())

    await ts.extract_trajectory_semantics(pool, "episode-1", client=client)

    assert client.calls == 1
    assert client.threads and all(t != threading.get_ident() for t in client.threads), (
        "the blocking SDK call ran on the event loop thread"
    )


@pytest.mark.asyncio
async def test_event_loop_stays_responsive_during_the_extraction_call(monkeypatch, budget_off):
    """The stronger form of the same claim, and the one that would catch a
    future `await`-less regression even if the thread name matched: a 0.2 s
    sync call must not stop other coroutines from running."""
    pool = FakePool(_episode_row(), [_event_row(1)])
    _patch_writers(monkeypatch)
    ticks: list[int] = []

    async def heartbeat():
        for _ in range(4):
            await asyncio.sleep(0.05)
            ticks.append(1)

    beat = asyncio.ensure_future(heartbeat())
    await ts.extract_trajectory_semantics(
        pool, "episode-1", client=SyncClient(_payload(), delay_s=0.2, usage=FakeUsage()),
    )
    await beat
    assert len(ticks) == 4, "the event loop was blocked for the length of the LLM call"


@pytest.mark.asyncio
async def test_async_openai_client_is_awaited_not_discarded(monkeypatch, budget_off):
    """The exact trap: an `AsyncOpenAI` handed to a caller that never awaits
    gets a coroutine, and `coroutine.choices` raises
    `'coroutine' object has no attribute 'choices'` -- after the request was
    billed. `run_blocking` awaits an awaitable result, so both client shapes
    work and neither silently returns nothing."""
    pool = FakePool(_episode_row(), [_event_row(1)])
    _patch_writers(monkeypatch)
    client = AsyncClient(_payload(), usage=FakeUsage())

    result = await ts.extract_trajectory_semantics(pool, "episode-1", client=client)

    assert client.calls == 1
    assert result["goals"] == 1, "the async client's response was parsed, not dropped"
    assert pool.status_updates() == ["completed"]


# ---------------------------------------------------- 2. the budget guard

@pytest.mark.asyncio
async def test_guard_runs_before_the_call_and_the_completion_is_recorded(
    monkeypatch, budget_off
):
    """Half-gate rule, both halves: the ledger row is written, so the trigger
    that stops the next call has to exist in the same change. Asserted as an
    ORDER, not as two independent facts."""
    from app.services import ingest_budget

    ledger = LedgerPool(spent=0.0)
    ingest_budget.install(ledger, cap_usd=10.0, cache_s=0)
    order: list[str] = []
    real_guard = ingest_budget.guard
    real_record = ingest_budget.record_completion

    async def spy_guard(op=""):
        order.append(f"guard:{op}")
        await real_guard(op)

    async def spy_record(model, op, usage, **kw):
        order.append(f"record:{op}")
        await real_record(model, op, usage, **kw)

    monkeypatch.setattr(ingest_budget, "guard", spy_guard)
    monkeypatch.setattr(ingest_budget, "record_completion", spy_record)

    pool = FakePool(_episode_row(), [_event_row(1)])
    _patch_writers(monkeypatch)
    client = SyncClient(_payload(), usage=FakeUsage(1200, 400))

    await ts.extract_trajectory_semantics(pool, "episode-1", client=client)

    assert order == [f"guard:{ts.SEMANTICS_OP}", f"record:{ts.SEMANTICS_OP}"]
    assert client.calls == 1
    assert ledger.inserted, "spend must reach llm_spend, not just a local counter"
    recorded = ledger.inserted[0][1]
    assert 1200 in recorded and 400 in recorded, recorded
    assert any(ts.SEMANTICS_OP in str(a) for a in recorded), (
        f"the ledger row must be attributable to {ts.SEMANTICS_OP}: {recorded}"
    )


@pytest.mark.asyncio
async def test_over_cap_refuses_before_the_call_and_spends_nothing(monkeypatch, budget_off):
    """The cap has to be a ceiling. With the ledger already at the cap, the
    provider must not be called at all -- this is the assertion the old
    post-hoc-only design could not make."""
    from app.services import ingest_budget

    ledger = LedgerPool(spent=10.0)
    ingest_budget.install(ledger, cap_usd=10.0, cache_s=0)
    pool = FakePool(_episode_row(), [_event_row(1)])
    _patch_writers(monkeypatch)
    client = SyncClient(_payload(), usage=FakeUsage())

    with pytest.raises(BudgetExceeded):
        await ts.extract_trajectory_semantics(pool, "episode-1", client=client)

    assert client.calls == 0, "the pre-spend guard did not stop the call"
    assert ledger.inserted == [], "a refused call must not write spend"
    # The failure is still inspectable -- and it is a COST STOP, not a transient
    # provider failure, so a retry loop must not treat it as one.
    assert pool.status_updates() == ["failed"]


@pytest.mark.asyncio
async def test_budget_stop_is_not_disguised_as_a_transient_failure(monkeypatch, budget_off):
    from app.services import ingest_budget

    ingest_budget.install(LedgerPool(spent=99.0), cap_usd=10.0, cache_s=0)
    pool = FakePool(_episode_row(), [_event_row(1)])
    _patch_writers(monkeypatch)

    with pytest.raises(BudgetExceeded):
        await ts.extract_trajectory_semantics(
            pool, "episode-1", client=SyncClient(_payload(), usage=FakeUsage()),
        )
    assert not isinstance(
        BudgetExceeded("x"), ExtractionTransientFailure,
    ), "a cost stop must not satisfy `except ExtractionTransientFailure`"


@pytest.mark.asyncio
async def test_no_budget_installed_means_no_gate_and_no_ledger_row(monkeypatch, budget_off):
    """The API and MCP paths never install one. `guard` must stay a no-op
    there, or every non-ingestion extraction call would start failing."""
    pool = FakePool(_episode_row(), [_event_row(1)])
    _patch_writers(monkeypatch)
    client = SyncClient(_payload(), usage=FakeUsage())

    result = await ts.extract_trajectory_semantics(pool, "episode-1", client=client)

    assert client.calls == 1
    assert result["goals"] == 1


# ----------------------------- 3. the prompt cannot name a rejected field

def test_prompt_is_derived_from_the_schema_so_it_cannot_drift_again():
    """The first step-0 failure was prompt/schema drift in three places
    (`goal` vs `primary_goal`, `subgoal_text` vs step `description`, uppercase
    vs lowercase epistemic enums). Deriving the contract from the Pydantic
    model is the fix that cannot rot; this asserts the wiring rather than
    asserting today's field names, which would rot with the next rename."""
    assert ts._SCHEMA_CONTRACT
    assert "primary_goal" in ts._SCHEMA_CONTRACT
    assert "description" in ts._SCHEMA_CONTRACT
    assert "observed" in ts._SCHEMA_CONTRACT
    # Uppercase enum spellings are what the model used to emit; they must not be
    # presented to it as the allowed values anywhere in the prompt.
    assert "OBSERVED =" not in ts._SYSTEM_PROMPT
    assert "INFERRED =" not in ts._SYSTEM_PROMPT
    assert ts._SYSTEM_PROMPT.count("primary_goal") >= 1
    assert ts.PROMPT_VERSION == "v2", (
        "the prompt changed; a prompt is a versioned artifact, so the version "
        "moves with it"
    )


def test_every_field_name_the_prompt_shows_survives_the_strict_parser():
    """The load-bearing one: pull the field names the prompt actually contains
    and prove the strict schema accepts them."""
    from app.services.trajectory_semantics import TrajectorySemanticExtraction

    accepted = set(TrajectorySemanticExtraction.model_json_schema()["properties"])
    for defn in TrajectorySemanticExtraction.model_json_schema().get("$defs", {}).values():
        accepted |= set(defn.get("properties", {}))
    for name in re_find_field_names(ts._SYSTEM_PROMPT):
        if name in _NOT_SCHEMA_FIELDS:
            continue
        assert name in accepted, (
            f"the prompt names {name!r}, which the strict parser rejects"
        )


#: Words the prompt uses in backticks that are JSON-Schema KEYWORDS the model is
#: told not to emit -- not extraction fields. Listed explicitly rather than
#: filtered by pattern, so adding a new keyword mention stays a deliberate act.
_NOT_SCHEMA_FIELDS = {"title", "additionalProperties"}


def re_find_field_names(prompt: str) -> set[str]:
    import re

    return set(re.findall(r"`([a-z_]+)`", prompt))


@pytest.mark.asyncio
async def test_a_fenced_schema_following_response_parses_and_yields(monkeypatch, budget_off):
    """End-to-end shape of the fixed path: the response the model is now
    pointed at (fenced, schema-conformant) parses, produces knowledge items,
    and leaves the row 'completed' -- i.e. yield is no longer zero."""
    pool = FakePool(_episode_row(), [_event_row(1, "Read", "READ"), _event_row(2, "Bash", "TEST")])
    _patch_writers(monkeypatch)
    body = _payload(claims=[{
        "text": "the failure disappeared after regenerating the client",
        "event_indices": [2], "epistemic_status": "observed", "confidence": 0.7,
    }])

    result = await ts.extract_trajectory_semantics(
        pool, "episode-1", client=SyncClient(f"```json\n{body}\n```", usage=FakeUsage()),
    )

    assert result["goals"] == 1
    assert result["claims"] == 1
    assert pool.status_updates() == ["completed"]


@pytest.mark.asyncio
async def test_a_failed_pass_is_failed_not_completed(monkeypatch, budget_off):
    """The "13 of 13 died and the run said done" failure, at the module level:
    a raised failure must leave an unambiguous 'failed' row behind, never a
    'completed' one a reader could count as a successful abstention."""
    pool = FakePool(_episode_row(), [_event_row(1)])
    _patch_writers(monkeypatch)

    with pytest.raises(ExtractionTransientFailure):
        await ts.extract_trajectory_semantics(
            pool, "episode-1", client=SyncClient("not json at all", usage=FakeUsage()),
        )

    assert pool.status_updates() == ["failed"]


@pytest.mark.asyncio
async def test_a_rejection_while_persisting_also_fails_the_row(monkeypatch, budget_off):
    """Measured on the step-0 re-run: 2 of 10 passes raised `GoalQualityRejected`
    from `find_or_create_goal` AFTER a perfectly good parse, and both rows were
    left at 'pending'. That is a silent hole in both directions -- the report
    read `completed 8, failed 0` while the counter said two failed, and
    `run_trajectory_ingestion.py`'s re-extraction query skips any episode that
    has a row at all, so the work was silently orphaned."""
    pool = FakePool(_episode_row(), [_event_row(1)])
    _patch_writers(monkeypatch)

    async def rejecting_goal(pool, *, canonical_name, **kwargs):
        raise RuntimeError("GoalQualityRejected: goal text is not a durable objective")

    monkeypatch.setattr(ts, "find_or_create_goal", rejecting_goal)

    with pytest.raises(RuntimeError):
        await ts.extract_trajectory_semantics(
            pool, "episode-1", client=SyncClient(_payload(), usage=FakeUsage()),
        )

    assert pool.status_updates() == ["failed"], (
        "a post-parse rejection must close the row as failed, not leave it pending"
    )
    assert "GoalQualityRejected" in "".join(str(a) for _, a in pool.executed), (
        "the row must carry the reason, not just the state"
    )


@pytest.mark.asyncio
async def test_a_persistence_failure_does_not_overwrite_an_already_closed_row(monkeypatch, budget_off):
    """The close-out UPDATE is `AND status='pending'`, so a failure raised after
    the row was already marked completed cannot rewrite history."""
    pool = FakePool(_episode_row(), [_event_row(1)])
    _patch_writers(monkeypatch)
    await ts.extract_trajectory_semantics(
        pool, "episode-1", client=SyncClient(_payload(), usage=FakeUsage()),
    )
    assert pool.status_updates() == ["completed"]
    assert all("AND status='pending'" in sql for sql, _ in pool.executed
               if "status='failed'" in sql)


@pytest.mark.asyncio
async def test_confidence_summary_is_bound_as_an_object_not_a_pre_dumped_string(monkeypatch, budget_off):
    """Measured on the step-0 re-run: `jsonb_typeof(confidence_summary)` came
    back `"string"` for all 34 completed extractions, so
    `confidence_summary->>'goals'` was NULL and this module's own per-extraction
    yield counters could not be read back by anything.

    Cause: the pool registers a `jsonb` codec with `encoder=json.dumps`
    (`app/db/session.py::_init_connection`), so a pre-serialised `str` is
    encoded a second time and lands as a JSON string scalar. The same trap is
    already documented in `route_decision.py:450` and `trace_worker.py:1280`.
    """
    pool = FakePool(_episode_row(), [_event_row(1)])
    _patch_writers(monkeypatch)

    await ts.extract_trajectory_semantics(
        pool, "episode-1", client=SyncClient(_payload(), usage=FakeUsage()),
    )

    update = next(
        args for sql, args in pool.executed
        if "status='completed'" in sql
    )
    summary = update[2]
    assert isinstance(summary, dict), (
        f"confidence_summary was bound as {type(summary).__name__}, so the "
        f"jsonb codec double-encodes it into a JSON string"
    )
    assert summary["goals"] == 1 and summary["procedures"] == 0
    # A pre-dumped string is the failure mode, and the repo's own convention
    # (route_decision.py:450) is the opposite of what this used to do.
    assert not isinstance(summary, str)
