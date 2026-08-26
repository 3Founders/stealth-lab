"""
DB-free coverage for evidence.py's two EvidenceSource implementations.
AgentRunEvidenceSource does no I/O at all and SessionEvidenceSource's
three queries are simple, independently-fakeable SELECTs -- neither
needed the real Postgres instance test_procedure_extraction_e2e.py
requires them through, and both had zero offline coverage before this.
"""
import asyncio
from datetime import datetime, timezone

from app.services.procedure_extraction.evidence import (
    AgentRunEvidenceSource,
    ProcedureEvidence,
    SessionEvidenceSource,
)


def _run(coro):
    return asyncio.run(coro)


def test_has_observations_reflects_the_list():
    assert not ProcedureEvidence(goal_text="g", outcome="success").has_observations()
    assert ProcedureEvidence(
        goal_text="g", outcome="success", observations=[{"observation_type": "file_touched"}],
    ).has_observations()


class FakePool:
    def __init__(self, *, observations=(), tool_names=(), started_at=None):
        self._observations = list(observations)
        self._tool_names = list(tool_names)
        self._started_at = started_at
        self.fetch_calls = []
        self.fetchval_calls = []

    async def fetch(self, sql, *params):
        self.fetch_calls.append((" ".join(sql.split()), params))
        if "FROM observations" in sql:
            return self._observations
        return [{"tool_name": t} for t in self._tool_names]

    async def fetchval(self, sql, *params):
        self.fetchval_calls.append((" ".join(sql.split()), params))
        return self._started_at


def _obs_row(obs_id, obs_type="file_touched", label="Modified x.py", properties=None):
    return {
        "id": obs_id, "observation_type": obs_type, "label": label,
        "properties": properties or {}, "extracted_at": datetime.now(timezone.utc),
    }


def test_session_source_collects_observations_tool_sequence_and_started_at():
    started_at = datetime.now(timezone.utc)
    pool = FakePool(
        observations=[_obs_row("o1"), _obs_row("o2", obs_type="commit_made", label="Committed")],
        tool_names=["Edit", "Bash"],
        started_at=started_at,
    )
    source = SessionEvidenceSource(
        pool, session_id="sess-1", goal_text="fix the bug", outcome="success",
        project_id="proj-1", episode_id="ep-1", steps_used=4,
    )

    evidence = _run(source.collect())

    assert evidence.goal_text == "fix the bug"
    assert evidence.outcome == "success"
    assert evidence.project_id == "proj-1"
    assert evidence.episode_id == "ep-1"
    assert evidence.session_id == "sess-1"
    assert evidence.steps_used == 4
    assert evidence.started_at == started_at
    assert evidence.tool_sequence == ["Edit", "Bash"]
    assert [o["observation_type"] for o in evidence.observations] == ["file_touched", "commit_made"]
    assert evidence.observations[0]["id"] == "o1"
    assert evidence.has_observations()

    # every query scoped by this session_id, nothing else
    assert all(params == (("sess-1",)) or params == ("sess-1",) for _, params in pool.fetch_calls)


def test_session_source_with_no_observations_is_honestly_empty():
    pool = FakePool(observations=[], tool_names=[], started_at=None)
    source = SessionEvidenceSource(pool, session_id="sess-2", goal_text="g", outcome="failure")

    evidence = _run(source.collect())

    assert evidence.observations == []
    assert evidence.tool_sequence == []
    assert evidence.started_at is None
    assert not evidence.has_observations()


def test_agent_run_source_does_no_io_and_returns_exactly_what_it_was_given():
    observations = [{"observation_type": "test_run", "label": "Ran tests", "properties": {}}]
    source = AgentRunEvidenceSource(
        goal_text="ship the feature", outcome="success",
        observations=observations, tool_sequence=["Bash", "Edit"],
        project_id="proj-2", episode_id="ep-2", session_id="sess-3", steps_used=7,
    )

    evidence = _run(source.collect())

    assert evidence.goal_text == "ship the feature"
    assert evidence.observations is observations  # passed through, not copied or re-derived
    assert evidence.tool_sequence == ["Bash", "Edit"]
    assert evidence.project_id == "proj-2"
    assert evidence.episode_id == "ep-2"
    assert evidence.session_id == "sess-3"
    assert evidence.steps_used == 7
