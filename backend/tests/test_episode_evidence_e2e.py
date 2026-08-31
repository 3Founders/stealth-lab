"""
Real, live-database proving tests for
app/services/procedure_extraction/episode_evidence.py. Same pattern as
test_observations_e2e.py / test_procedure_extraction_e2e.py: requires a
real DATABASE_URL, skips (not fails) without one.

Builds a real episode (episodes row) wired to a real session
(agent_traces + trace_events rows), runs the real observation extractors
(app.services.observations) to persist real observation rows off those
trace_events -- the same writer path production uses, not a hand-rolled
fixture shape -- then calls build_episode_evidence() and asserts every
field is populated HONESTLY from what was actually inserted: no more, no
less, and never fabricated.
"""
import asyncio
import os

import asyncpg
import pytest

from app.db.session import create_pool as _real_create_pool
from app.services.observations import (
    extract_deterministic_observations,
    persist_observation,
)
from app.services.procedure_extraction.episode_evidence import (
    build_episode_evidence,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


async def _cleanup(pool: asyncpg.Pool, session_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM observation_events WHERE event_id IN "
            "(SELECT id FROM trace_events WHERE session_id = $1)", session_id,
        )
        await conn.execute(
            "DELETE FROM observations WHERE id NOT IN "
            "(SELECT observation_id FROM observation_events)"
        )
        await conn.execute("DELETE FROM episodes WHERE session_id = $1", session_id)
        await conn.execute("DELETE FROM trace_events WHERE session_id = $1", session_id)
        await conn.execute("DELETE FROM agent_traces WHERE session_id = $1", session_id)


async def _insert_event(pool, *, trace_id, session_id, sequence, tool_name, tool_input, dedup_key):
    return await pool.fetchval(
        "INSERT INTO trace_events (trace_id, session_id, sequence, event_type, "
        "\"timestamp\", tool_name, tool_input, dedup_key, schema_version) "
        "VALUES ($1,$2,$3,'PostToolUse',now(),$4,$5,$6,'1') RETURNING id",
        trace_id, session_id, sequence, tool_name, tool_input, dedup_key,
    )


async def _extract_and_persist(pool, event_id: str, event_row: dict) -> list[str]:
    """Runs the REAL deterministic extractor over a real trace_event row
    and persists whatever it derives -- exactly production's own path
    (trace_worker's observation pass), not a shortcut that hand-builds
    an observation row directly."""
    derived = extract_deterministic_observations(event_row)
    obs_ids = []
    for d in derived:
        obs_id = await persist_observation(
            pool, observation_type=d["observation_type"], label=d["label"],
            extractor_kind="deterministic", event_ids=[event_id],
            properties=d["properties"],
        )
        obs_ids.append(obs_id)
    return obs_ids


def test_episode_evidence_honest_fields_from_real_observations():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_id = "epi-evi-test-session-001"
        try:
            await _cleanup(pool, session_id)

            trace_id = await pool.fetchval(
                "INSERT INTO agent_traces (trace_id, session_id, started_at, schema_version) "
                "VALUES ($1, $2, now(), '1') RETURNING trace_id",
                "epi-evi-test-trace-001", session_id,
            )

            # A real sequence: edit a file, run a failing test, run a
            # passing test, run a plain command, commit.
            e1 = await _insert_event(
                pool, trace_id=trace_id, session_id=session_id, sequence=0,
                tool_name="Edit", tool_input={"file_path": "epi_evi_target.py"},
                dedup_key="epi-evi-dedup-1",
            )
            ids1 = await _extract_and_persist(
                pool, str(e1),
                {"tool_name": "Edit", "tool_input": {"file_path": "epi_evi_target.py"}},
            )

            e2 = await _insert_event(
                pool, trace_id=trace_id, session_id=session_id, sequence=1,
                tool_name="Bash", tool_input={"command": "pytest tests/epi_evi_test.py"},
                dedup_key="epi-evi-dedup-2",
            )
            # NOT run through _extract_and_persist here -- the deterministic
            # extractor would ALSO derive a test_run observation off this
            # same pytest command with no `passed` property, double-counting
            # against the hand-persisted one below. Real properties note:
            # extract_deterministic_observations never sets `passed` -- that
            # is a downstream signal a test RESULT parser would add. Set it
            # explicitly here via a direct persist_observation call, to
            # exercise the honest branch: a test_run WITH a real recorded
            # outcome.
            failing_test_obs = await persist_observation(
                pool, observation_type="test_run", label="Ran tests (failing)",
                extractor_kind="deterministic", event_ids=[str(e2)],
                properties={"command": "pytest tests/epi_evi_test.py", "passed": False},
            )

            e3 = await _insert_event(
                pool, trace_id=trace_id, session_id=session_id, sequence=2,
                tool_name="Bash", tool_input={"command": "pytest tests/epi_evi_test.py"},
                dedup_key="epi-evi-dedup-3",
            )
            passing_test_obs = await persist_observation(
                pool, observation_type="test_run", label="Ran tests (passing)",
                extractor_kind="deterministic", event_ids=[str(e3)],
                properties={"command": "pytest tests/epi_evi_test.py", "passed": True},
            )

            e4 = await _insert_event(
                pool, trace_id=trace_id, session_id=session_id, sequence=3,
                tool_name="Bash", tool_input={"command": "ls -la"},
                dedup_key="epi-evi-dedup-4",
            )
            ids4 = await _extract_and_persist(
                pool, str(e4), {"tool_name": "Bash", "tool_input": {"command": "ls -la"}},
            )

            e5 = await _insert_event(
                pool, trace_id=trace_id, session_id=session_id, sequence=4,
                tool_name="Bash", tool_input={"command": "git commit -m 'epi evi test commit'"},
                dedup_key="epi-evi-dedup-5",
            )
            ids5 = await _extract_and_persist(
                pool, str(e5),
                {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'epi evi test commit'"}},
            )

            episode_id = await pool.fetchval(
                "INSERT INTO episodes (episode_type, content_ref, timestamp, metadata, "
                "session_id) VALUES ('trace', 'epi-evi-test#0:5', now(), $1::jsonb, $2) "
                "RETURNING id",
                {"segmenter": "test-fixture"}, session_id,
            )

            evidence = await build_episode_evidence(pool, str(episode_id))

            # --- provenance / identity ---
            assert evidence.episode_id == str(episode_id)
            assert evidence.session_id == session_id

            # --- declared_goal: honestly None -- no metadata goal key,
            # no agent_traces.intent set on this fixture's trace row.
            assert evidence.declared_goal is None

            # --- files_touched: exactly the one real file_touched
            # observation's file_path, no more, no less.
            assert evidence.files_touched == ["epi_evi_target.py"]

            # --- commands_run: exactly the one real command_executed
            # observation's command (the `ls -la` call) -- test_run
            # commands must NOT leak into commands_run.
            assert evidence.commands_run == ["ls -la"]

            # --- tests_run: both real test_run observations, with their
            # real recorded pass/fail properties, and nothing invented.
            assert len(evidence.tests_run) == 2
            passed_flags = sorted(t["passed"] for t in evidence.tests_run)
            assert passed_flags == [False, True]

            # --- verification: populated only because real test_run rows
            # exist; all_passed is False since one of the two failed.
            assert evidence.verification is not None
            assert evidence.verification["test_run_count"] == 2
            assert evidence.verification["all_passed"] is False

            # --- decisions: always empty -- no fabrication, ever.
            assert evidence.decisions == []

            # --- outputs: the one real commit_made observation.
            assert len(evidence.outputs) == 1
            assert "commit" in evidence.outputs[0]["command"]

            # --- state_changes: file_touched + commit_made observations
            # recast as deltas -- exactly 2 (one file edit, one commit).
            change_types = sorted(c["type"] for c in evidence.state_changes)
            assert change_types == ["commit_made", "file_touched"]

            # --- failures_retries: the one failing test_run observation
            # is its own group of size 1 (not merged with the passing one
            # that follows -- that one is NOT marked failed).
            assert len(evidence.failures_retries) == 1
            assert evidence.failures_retries[0].observation_type == "test_run"
            assert evidence.failures_retries[0].count == 1
            assert evidence.failures_retries[0].observation_ids == [failing_test_obs]

            # --- observations: the real, unsummarized rows -- every
            # observation actually inserted for this session must appear
            # (5 from the deterministic extractor path + 2 hand-persisted
            # test_run rows = 7), each carrying its real extractor
            # provenance.
            all_obs_ids = set(ids1 + ids4 + ids5 + [failing_test_obs, passing_test_obs])
            assert {o["id"] for o in evidence.observations} == all_obs_ids
            for o in evidence.observations:
                assert o["extractor_kind"] == "deterministic"
                assert o["extractor_name"]
                assert o["code_version"]

            # --- actions: same count, ordered, 1-indexed.
            assert len(evidence.actions) == len(evidence.observations)
            assert [a["order"] for a in evidence.actions] == list(range(1, len(evidence.actions) + 1))

            # --- tool_calls: real trace_events.tool_name in sequence
            # order -- Edit, Bash, Bash, Bash, Bash.
            assert evidence.tool_calls == ["Edit", "Bash", "Bash", "Bash", "Bash"]

            # --- initial_state / final_state: real, honestly-approximate
            # snapshots of the first/last observation -- never claimed as
            # a real state projection.
            assert evidence.initial_state is not None
            assert evidence.initial_state.approximate is True
            assert evidence.final_state is not None
            assert evidence.final_state.approximate is True
            assert evidence.initial_state.observation_id != evidence.final_state.observation_id

            # --- observed_goal_signals: real labels from the episode's
            # own earliest observations, bounded by GOAL_SIGNAL_WINDOW.
            assert evidence.observed_goal_signals
            assert evidence.observed_goal_signals[0] == evidence.observations[0]["label"]

            # --- environment: None, honestly, since no repo_root was
            # given.
            assert evidence.environment is None

            # --- provenance: real source_event_ids fold every real
            # trace_event id this episode's observations cite.
            assert set(evidence.provenance["source_event_ids"]) == {
                str(e1), str(e2), str(e3), str(e4), str(e5),
            }
            assert evidence.provenance["observation_count"] == len(evidence.observations)
            assert all("deterministic_v1" in v for v in evidence.provenance["extractor_versions"])
        finally:
            await _cleanup(pool, session_id)
            await pool.close()

    asyncio.run(_run())


def test_episode_evidence_with_repo_root_populates_environment():
    """Real, live confirmation that `environment` is populated from a
    real repo_root via the same probe_environment() derive.py already
    uses -- this repo's own backend/ checkout has a real pyproject.toml,
    so `language: python` must appear, never guessed."""
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_id = "epi-evi-test-session-002"
        try:
            await _cleanup(pool, session_id)
            await pool.execute(
                "INSERT INTO agent_traces (trace_id, session_id, started_at, schema_version) "
                "VALUES ($1, $2, now(), '1')",
                "epi-evi-test-trace-002", session_id,
            )
            episode_id = await pool.fetchval(
                "INSERT INTO episodes (episode_type, content_ref, timestamp, metadata, "
                "session_id) VALUES ('trace', 'epi-evi-test-2#0:0', now(), '{}'::jsonb, $1) "
                "RETURNING id",
                session_id,
            )
            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            assert os.path.isfile(os.path.join(repo_root, "pyproject.toml"))

            evidence = await build_episode_evidence(pool, str(episode_id), repo_root=repo_root)

            assert evidence.observations == []
            assert evidence.files_touched == []
            assert evidence.commands_run == []
            assert evidence.verification is None
            assert evidence.decisions == []
            assert evidence.environment is not None
            assert any(f["predicate"] == "language" and f["object"] == "python"
                       for f in evidence.environment)
        finally:
            await _cleanup(pool, session_id)
            await pool.close()

    asyncio.run(_run())


def test_episode_evidence_missing_episode_raises():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            with pytest.raises(ValueError):
                await build_episode_evidence(pool, "00000000-0000-0000-0000-000000000099")
        finally:
            await pool.close()

    asyncio.run(_run())
