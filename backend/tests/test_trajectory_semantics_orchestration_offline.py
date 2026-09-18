"""Offline proving tests for extract_trajectory_semantics()'s wiring
(trajectory-ingestion-hardening task, Sec 6/20). The knowledge-object
writers it calls (find_or_create_goal, capture_claim, capture_procedure,
find_or_create_implementation_identity) are real, already-tested
functions elsewhere against a real database -- here they're monkeypatched
so this test proves the ORCHESTRATION (what gets called, with what
event citations, in what order, and how status transitions) without a
database or a real LLM call, same fake-service-layer convention
test_promote_observation_job_offline.py already uses.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.services.procedure_extraction.schema import ExtractionTransientFailure
import app.services.trajectory_semantics as ts


# --------------------------------------------------------------- fakes

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


class FakeChoice:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


class FakeResponse:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]


class FakeClient:
    def __init__(self, response_text):
        self._response_text = response_text
        self.calls = 0

    class _Completions:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.calls += 1
            return FakeResponse(self._outer._response_text)

    @property
    def chat(self):
        outer = self

        class _Chat:
            completions = FakeClient._Completions(outer)

        return _Chat()


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


def _patch_writers(monkeypatch, *, goal_ids=None, claim_id="claim-1",
                    procedure=None, impl_id="impl-1"):
    goal_calls = []

    async def fake_find_or_create_goal(pool, *, canonical_name, **kwargs):
        goal_calls.append(canonical_name)
        return {"id": f"goal-{len(goal_calls)}", "canonical_name": canonical_name, "created": True}

    claim_calls = []

    async def fake_capture_claim(pool, *, statement, **kwargs):
        claim_calls.append((statement, kwargs))
        return claim_id

    procedure_calls = []

    async def fake_capture_procedure(pool, **kwargs):
        procedure_calls.append(kwargs)
        return procedure or {"id": "procver-1", "procedure_id": "proc-1"}

    impl_calls = []

    async def fake_find_or_create_implementation_identity(pool, *, name, provider, kind, **kwargs):
        impl_calls.append((name, provider, kind))
        return {"id": impl_id, "name": name, "provider": provider}

    monkeypatch.setattr(ts, "find_or_create_goal", fake_find_or_create_goal)
    monkeypatch.setattr(ts, "capture_claim", fake_capture_claim)
    monkeypatch.setattr(ts, "capture_procedure", fake_capture_procedure)
    monkeypatch.setattr(ts, "find_or_create_implementation_identity", fake_find_or_create_implementation_identity)
    return {"goals": goal_calls, "claims": claim_calls, "procedures": procedure_calls, "implementations": impl_calls}


def _success_payload():
    return {
        "primary_goal": {
            "text": "fix the failing test", "event_indices": [1, 2],
            "epistemic_status": "inferred", "confidence": 0.8,
        },
        "subgoals": [],
        "candidate_procedures": [{
            "capability_statement": "reproduce then fix a failing test",
            "steps": [
                {"description": "reproduce the failure", "subgoal_text": "reproduce failure", "event_indices": [1]},
                {"description": "verify the fix", "subgoal_text": "verify fix", "event_indices": [2]},
            ],
            "event_indices": [1, 2], "epistemic_status": "inferred", "confidence": 0.6,
        }],
        "implementations": [{
            "tool_name": "Bash", "role": "ran the test suite",
            "event_indices": [2], "applicability_notes": None,
        }],
        "claims": [{
            "text": "the fix was verified by a passing test run",
            "event_indices": [2], "epistemic_status": "observed", "confidence": 0.9,
        }],
        "preconditions": [], "failure_modes": [], "recovery_patterns": [],
        "verification_actions": [], "outcome": "success", "reusable_elements": [],
        "uncertainties": [],
    }


# ------------------------------------------------------------------ tests

@pytest.mark.asyncio
async def test_raw_event_count_and_success_path_wires_every_writer(monkeypatch):
    pool = FakePool(_episode_row(), [_event_row(1, "Read", "READ"), _event_row(2, "Bash", "TEST")])
    calls = _patch_writers(monkeypatch)
    client = FakeClient(json.dumps(_success_payload()))

    result = await ts.extract_trajectory_semantics(pool, "episode-1", client=client)

    assert result["goals"] >= 1  # primary_goal + 2 step subgoals
    assert result["claims"] == 1
    assert result["procedures"] == 1
    assert result["implementations"] == 1
    assert calls["claims"][0][0] == "the fix was verified by a passing test run"
    assert client.calls == 1

    statuses = [args for sql, args in pool.executed if "trajectory_extractions" in sql and "status='completed'" in sql]
    assert len(statuses) == 1


@pytest.mark.asyncio
async def test_every_extracted_object_links_to_real_source_events(monkeypatch):
    pool = FakePool(_episode_row(), [_event_row(1, "Read", "READ"), _event_row(2, "Bash", "TEST")])
    _patch_writers(monkeypatch)
    client = FakeClient(json.dumps(_success_payload()))

    await ts.extract_trajectory_semantics(pool, "episode-1", client=client)

    link_inserts = [
        (sql, args) for sql, args in pool.executed
        if "INSERT INTO trajectory_extraction_objects" in sql
    ]
    # goal (primary), 2x step-goal, claim, implementation, procedure = 6 links
    assert len(link_inserts) == 6
    for sql, args in link_inserts:
        object_type, object_id, event_refs = args[1], args[2], args[3]
        assert event_refs, f"{object_type} {object_id} must cite at least one real event id"
        assert all(ref.startswith("00000000-0000-0000-0000-") for ref in event_refs)


@pytest.mark.asyncio
async def test_no_events_raises_before_any_llm_call(monkeypatch):
    pool = FakePool(_episode_row(), [])
    _patch_writers(monkeypatch)
    client = FakeClient(json.dumps(_success_payload()))

    with pytest.raises(ValueError, match="no trace_events"):
        await ts.extract_trajectory_semantics(pool, "episode-1", client=client)
    assert client.calls == 0


@pytest.mark.asyncio
async def test_failed_trajectory_still_produces_extraction(monkeypatch):
    """Task Sec 16: outcome='failure' must not gate this pass -- unlike
    procedure_extraction's V5 gate, this module has no success-only
    check."""
    pool = FakePool(_episode_row(), [_event_row(1, "Bash", "TEST")])
    calls = _patch_writers(monkeypatch)
    payload = _success_payload()
    payload["outcome"] = "failure"
    payload["candidate_procedures"] = []
    payload["implementations"] = []
    payload["claims"] = [{
        "text": "repeated edits without additional diagnosis between test runs",
        "event_indices": [1], "epistemic_status": "inferred", "confidence": 0.5,
    }]
    client = FakeClient(json.dumps(payload))

    result = await ts.extract_trajectory_semantics(pool, "episode-1", client=client)
    assert result["outcome"] == "failure"
    assert result["claims"] == 1
    assert calls["claims"][0][0] == "repeated edits without additional diagnosis between test runs"


@pytest.mark.asyncio
async def test_malformed_llm_response_marks_extraction_failed_and_raises(monkeypatch):
    pool = FakePool(_episode_row(), [_event_row(1)])
    _patch_writers(monkeypatch)
    client = FakeClient("not valid json")

    with pytest.raises(ExtractionTransientFailure):
        await ts.extract_trajectory_semantics(pool, "episode-1", client=client)

    failed = [args for sql, args in pool.executed if "trajectory_extractions" in sql and "status='failed'" in sql]
    assert len(failed) == 1


@pytest.mark.asyncio
async def test_llm_transport_failure_marks_extraction_failed_and_raises(monkeypatch):
    pool = FakePool(_episode_row(), [_event_row(1)])
    _patch_writers(monkeypatch)

    class BoomClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("connection reset")

    with pytest.raises(ExtractionTransientFailure):
        await ts.extract_trajectory_semantics(pool, "episode-1", client=BoomClient())

    failed = [args for sql, args in pool.executed if "trajectory_extractions" in sql and "status='failed'" in sql]
    assert len(failed) == 1
