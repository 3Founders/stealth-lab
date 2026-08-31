"""
Real, live-database proving tests for
app/services/procedure_extraction/synthesis.py::synthesize_procedure().
Same pattern as test_episode_evidence_e2e.py / test_procedure_extraction_
init_e2e.py: requires a real DATABASE_URL, skips (not fails) without one.

Builds real episodes wired to real sessions (agent_traces + trace_events +
observations, via the same real writer path production uses --
extract_deterministic_observations / persist_observation, plus explicit
test_run rows for real recorded verification), then exercises
synthesize_procedure() end to end:

  (a) two episodes with the SAME real tool-call pattern and the SAME real
      environment claim, but DIFFERENT literal file paths -- must merge
      into one generalized procedure whose capability_statement/steps
      never leak either episode's literal path, whose source_episode_ids
      names both, and which passes the real V0 gate through
      capture_procedure().
  (b) a materially incompatible episode (a genuinely different tool-call
      pattern) -- must be REFUSED, not silently merged.
  (c) two structurally-aligned episodes whose real environment claims
      contradict (package_manager=pip vs package_manager=poetry, both
      genuinely load-bearing) -- must also be REFUSED, via the predicate-
      contradiction gate specifically (distinct from (b)'s structural-
      mismatch gate).
"""
import asyncio
import os

import asyncpg
import pytest

from app.db.session import create_pool as _real_create_pool
from app.services.environment_probe import assert_environment_claims
from app.services.observations import persist_observation
from app.services.procedure_extraction.synthesis import synthesize_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

SESSION_PREFIX = "procsyn-test-session"
PROJECT_PREFIX = "procsyn-test-project"


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.7] * 1024


async def _cleanup(
    pool: asyncpg.Pool, session_ids: list[str], project_ids: list[str],
    episode_ids: list[str] | None = None,
) -> None:
    """Session/project cleanup is safe to run BEFORE a test builds
    anything (session ids are deterministic per test). Procedure cleanup
    is NOT name-based (the synthesized `name` is derived from real
    observation labels, which carry no reliable test-fixture marker) --
    it deletes by real `source_episode_ids` overlap, so `episode_ids`
    should be passed in the `finally` block once the episode ids created
    during the test are known."""
    async with pool.acquire() as conn:
        if episode_ids:
            await conn.execute(
                "DELETE FROM procedures WHERE source_episode_ids && $1::uuid[]", episode_ids,
            )
        for session_id in session_ids:
            await conn.execute(
                "DELETE FROM observation_events WHERE event_id IN "
                "(SELECT id FROM trace_events WHERE session_id = $1)", session_id,
            )
        await conn.execute(
            "DELETE FROM observations WHERE id NOT IN "
            "(SELECT observation_id FROM observation_events)"
        )
        for session_id in session_ids:
            await conn.execute("DELETE FROM episodes WHERE session_id = $1", session_id)
            await conn.execute("DELETE FROM trace_events WHERE session_id = $1", session_id)
            await conn.execute("DELETE FROM agent_traces WHERE session_id = $1", session_id)
        for project_id in project_ids:
            subject = f"project:{project_id}"
            await conn.execute(
                "DELETE FROM edges WHERE source_id IN "
                "(SELECT id FROM knowledge_nodes WHERE properties->>'subject' = $1) "
                "OR target_id IN (SELECT id FROM knowledge_nodes WHERE properties->>'subject' = $1)",
                subject,
            )
            await conn.execute("DELETE FROM knowledge_nodes WHERE properties->>'subject' = $1", subject)


async def _insert_event(pool, *, trace_id, session_id, sequence, tool_name, dedup_key):
    return await pool.fetchval(
        "INSERT INTO trace_events (trace_id, session_id, sequence, event_type, "
        "\"timestamp\", tool_name, dedup_key, schema_version) "
        "VALUES ($1,$2,$3,'PostToolUse',now(),$4,$5,'1') RETURNING id",
        trace_id, session_id, sequence, tool_name, dedup_key,
    )


async def _build_episode(
    pool, *, session_id: str, project_id: str, file_path: str, test_command: str,
    tag: str, pkg_manager_command: str | None = None,
) -> str:
    """One real 'Edit a file -> run tests (passing) -> commit' episode,
    real trace_events + real observations, wired to a real session and
    project. Returns the real episode id. `pkg_manager_command`, when
    given, adds a real command_executed observation BEFORE the edit (same
    tool name, Bash, so the tool-call skeleton shape stays identical
    across episodes that all pass it) -- used to make `package_manager`
    a real, load-bearing precondition for the contradiction test below."""
    await pool.execute(
        "INSERT INTO agent_traces (trace_id, session_id, started_at, schema_version, project_id) "
        "VALUES ($1, $2, now(), '1', $3) ON CONFLICT (trace_id) DO NOTHING",
        session_id, session_id, project_id,
    )

    seq = 0
    if pkg_manager_command is not None:
        e0 = await _insert_event(
            pool, trace_id=session_id, session_id=session_id, sequence=seq,
            tool_name="Bash", dedup_key=f"{tag}-dedup-0",
        )
        await persist_observation(
            pool, observation_type="command_executed", label="Installed dependencies",
            extractor_kind="deterministic", event_ids=[str(e0)],
            properties={"command": pkg_manager_command, "exit_code": 0},
        )
        seq += 1

    e1 = await _insert_event(
        pool, trace_id=session_id, session_id=session_id, sequence=seq,
        tool_name="Edit", dedup_key=f"{tag}-dedup-1",
    )
    await persist_observation(
        pool, observation_type="file_touched", label=f"Modified {file_path}",
        extractor_kind="deterministic", event_ids=[str(e1)],
        properties={"file_path": file_path, "tool_name": "Edit"},
    )
    seq += 1

    e2 = await _insert_event(
        pool, trace_id=session_id, session_id=session_id, sequence=seq,
        tool_name="Bash", dedup_key=f"{tag}-dedup-2",
    )
    await persist_observation(
        pool, observation_type="test_run", label="Ran tests (passing)",
        extractor_kind="deterministic", event_ids=[str(e2)],
        properties={"command": test_command, "passed": True},
    )
    seq += 1

    e3 = await _insert_event(
        pool, trace_id=session_id, session_id=session_id, sequence=seq,
        tool_name="Bash", dedup_key=f"{tag}-dedup-3",
    )
    await persist_observation(
        pool, observation_type="commit_made", label="Committed the fix",
        extractor_kind="deterministic", event_ids=[str(e3)],
        properties={"command": f"git commit -m '{tag} fix'"},
    )

    episode_id = await pool.fetchval(
        "INSERT INTO episodes (episode_type, content_ref, timestamp, metadata, "
        "session_id, project_id) VALUES ('trace', $1, now(), $2::jsonb, $3, $4) "
        "RETURNING id",
        f"{tag}#0:{seq}", {"segmenter": "test-fixture"}, session_id, project_id,
    )
    return str(episode_id)


def test_synthesis_merges_compatible_episodes_and_generalizes_paths(tmp_path):
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_a = f"{SESSION_PREFIX}-ab-a"
        session_b = f"{SESSION_PREFIX}-ab-b"
        project_id = f"{PROJECT_PREFIX}-ab"
        episode_a = episode_b = None
        try:
            await _cleanup(pool, [session_a, session_b], [project_id])

            (tmp_path / "requirements.txt").write_text("pytest\n")
            await assert_environment_claims(
                pool, project_id=project_id, repo_root=str(tmp_path), embedder=FakeEmbedder(),
            )

            episode_a = await _build_episode(
                pool, session_id=session_a, project_id=project_id,
                file_path="auth/login.py", test_command="pytest tests/test_login.py",
                tag="procsyn-test-ab-a",
            )
            episode_b = await _build_episode(
                pool, session_id=session_b, project_id=project_id,
                file_path="payments/checkout.py", test_command="pytest tests/test_checkout.py",
                tag="procsyn-test-ab-b",
            )

            result = await synthesize_procedure(pool, [episode_a, episode_b])

            assert result.synthesized, f"expected synthesis to succeed, got: {result.refusal_reason}"
            assert result.procedure_id is not None
            assert set(result.contributing_episode_ids) == {episode_a, episode_b}

            row = await pool.fetchrow(
                "SELECT * FROM procedures WHERE id = $1::uuid", result.version_row_id,
            )
            assert row is not None
            assert set(str(s) for s in row["source_episode_ids"]) == {episode_a, episode_b}
            assert row["approval_status"] == "proposed"
            assert row["extracted_by"] == "multi_episode_synthesis_v1@1"
            assert row["verification_state"] == "candidate"  # never born verified

            # --- the core generalization claim: no literal per-episode
            # path survives into the abstract fields ---
            cap = row["capability_statement"].lower()
            for leaked in ("login.py", "checkout.py", "auth/", "payments/",
                           "test_login", "test_checkout"):
                assert leaked not in cap, f"{leaked!r} leaked into capability_statement: {cap!r}"

            steps_text = str(row["steps"]).lower()
            for leaked in ("login.py", "checkout.py", "auth/", "payments/"):
                assert leaked not in steps_text, f"{leaked!r} leaked into steps: {steps_text!r}"

            # --- slots: exactly one generalized slot, no literal paths ---
            slots = row["parameter_schema"]["slots"]
            assert len(slots) == 1
            assert slots[0]["name"] == "target_files"
            assert "login.py" not in slots[0]["description"]
            assert "checkout.py" not in slots[0]["description"]

            # --- preconditions: the language=python claim, real for BOTH
            # episodes' own project, survives the intersection ---
            preconditions = row["preconditions"]
            assert any(
                p["predicate"] == "language" and p["object"] == "python"
                for p in preconditions
            ), f"expected a real, common language precondition, got {preconditions}"

            # --- capture_procedure's V0 gate really ran (would have
            # raised otherwise) ---
            assert row["provenance"] == "system_pending_review"
            assert row["scope_type"] == "project"
            assert row["scope_entity_id"] == project_id
        finally:
            await _cleanup(
                pool, [session_a, session_b], [project_id],
                [e for e in (episode_a, episode_b) if e],
            )
            await pool.close()

    asyncio.run(_run())


def test_synthesis_refuses_structurally_incompatible_episodes():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_a = f"{SESSION_PREFIX}-struct-a"
        session_c = f"{SESSION_PREFIX}-struct-c"
        episode_a = episode_c = None
        try:
            await _cleanup(pool, [session_a, session_c], [])

            episode_a = await _build_episode(
                pool, session_id=session_a, project_id=f"{PROJECT_PREFIX}-struct-a",
                file_path="auth/login.py", test_command="pytest tests/test_login.py",
                tag="procsyn-test-struct-a",
            )

            # Episode C: a genuinely different tool-call PATTERN -- mostly
            # research (Read/Grep), with a real test_run so the
            # verification-signal gate (b) agrees with episode A and the
            # refusal below is proven to come from the STRUCTURAL gate
            # (c), not gate (b).
            trace_c = session_c
            await pool.execute(
                "INSERT INTO agent_traces (trace_id, session_id, started_at, schema_version) "
                "VALUES ($1, $2, now(), '1') ON CONFLICT (trace_id) DO NOTHING",
                trace_c, session_c,
            )
            for i, tool in enumerate(["Read", "Grep", "Read"]):
                event_id = await _insert_event(
                    pool, trace_id=trace_c, session_id=session_c, sequence=i,
                    tool_name=tool, dedup_key=f"procsyn-test-struct-c-dedup-{i}",
                )
                await persist_observation(
                    pool, observation_type="semantic_label", label=f"{tool} step {i}",
                    extractor_kind="deterministic", event_ids=[str(event_id)],
                    properties={},
                )
            e_test = await _insert_event(
                pool, trace_id=trace_c, session_id=session_c, sequence=3,
                tool_name="Bash", dedup_key="procsyn-test-struct-c-dedup-3",
            )
            await persist_observation(
                pool, observation_type="test_run", label="Ran tests (passing)",
                extractor_kind="deterministic", event_ids=[str(e_test)],
                properties={"command": "pytest tests/test_unrelated.py", "passed": True},
            )
            e_last = await _insert_event(
                pool, trace_id=trace_c, session_id=session_c, sequence=4,
                tool_name="Read", dedup_key="procsyn-test-struct-c-dedup-4",
            )
            await persist_observation(
                pool, observation_type="semantic_label", label="Read step 4",
                extractor_kind="deterministic", event_ids=[str(e_last)],
                properties={},
            )
            episode_c = await pool.fetchval(
                "INSERT INTO episodes (episode_type, content_ref, timestamp, metadata, "
                "session_id) VALUES ('trace', 'procsyn-test-struct-c#0:5', now(), "
                "'{}'::jsonb, $1) RETURNING id",
                session_c,
            )
            episode_c = str(episode_c)

            result = await synthesize_procedure(pool, [episode_a, episode_c])

            assert not result.synthesized
            assert result.procedure_id is None
            assert result.refusal_reason is not None
            assert "structurally" in result.refusal_reason or "similarity" in result.refusal_reason
            assert episode_c in result.refusal_reason or episode_a in result.refusal_reason

            count = await pool.fetchval(
                "SELECT count(*) FROM procedures WHERE $1 = ANY(source_episode_ids) "
                "OR $2 = ANY(source_episode_ids)",
                episode_a, episode_c,
            )
            assert count == 0, "an incompatible batch must never persist a procedure row"
        finally:
            await _cleanup(
                pool, [session_a, session_c], [],
                [e for e in (episode_a, episode_c) if e],
            )
            await pool.close()

    asyncio.run(_run())


def test_synthesis_refuses_contradictory_environment_claims(tmp_path):
    """Two episodes with the SAME real tool-call pattern (so the
    structural gate passes) but real, contradictory `package_manager`
    claims -- project D genuinely uses pip (a bare requirements.txt),
    project E genuinely uses poetry (a pyproject.toml naming poetry) --
    made LOAD-BEARING for both episodes via a real command_executed
    observation invoking that package manager. Must be refused by the
    predicate-contradiction gate specifically, not the structural one."""
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_d = f"{SESSION_PREFIX}-contra-d"
        session_e = f"{SESSION_PREFIX}-contra-e"
        project_d = f"{PROJECT_PREFIX}-contra-d"
        project_e = f"{PROJECT_PREFIX}-contra-e"
        episode_d = episode_e = None
        try:
            await _cleanup(pool, [session_d, session_e], [project_d, project_e])

            pip_root = tmp_path / "pip_repo"
            pip_root.mkdir()
            (pip_root / "requirements.txt").write_text("pytest\n")
            await assert_environment_claims(
                pool, project_id=project_d, repo_root=str(pip_root), embedder=FakeEmbedder(),
            )

            poetry_root = tmp_path / "poetry_repo"
            poetry_root.mkdir()
            (poetry_root / "pyproject.toml").write_text(
                "[tool.poetry]\nname = \"procsyn-test-poetry\"\n"
            )
            (poetry_root / "requirements.txt").write_text("pytest\n")
            await assert_environment_claims(
                pool, project_id=project_e, repo_root=str(poetry_root), embedder=FakeEmbedder(),
            )

            # Same real tool-call PATTERN (Bash install, Edit, Bash
            # test_run, Bash commit) in both -- structurally compatible,
            # so the contradiction must be caught by the predicate gate,
            # not the structural one.
            episode_d = await _build_episode(
                pool, session_id=session_d, project_id=project_d,
                file_path="src/service.py", test_command="pytest tests/test_service.py",
                tag="procsyn-test-contra-d", pkg_manager_command="pip install -r requirements.txt",
            )
            episode_e = await _build_episode(
                pool, session_id=session_e, project_id=project_e,
                file_path="src/other_service.py", test_command="pytest tests/test_other.py",
                tag="procsyn-test-contra-e", pkg_manager_command="poetry install",
            )

            result = await synthesize_procedure(pool, [episode_d, episode_e])

            assert not result.synthesized
            assert result.procedure_id is None
            assert result.refusal_reason is not None
            assert "contradictory precondition" in result.refusal_reason
            assert "package_manager" in result.refusal_reason

            count = await pool.fetchval(
                "SELECT count(*) FROM procedures WHERE $1 = ANY(source_episode_ids) "
                "OR $2 = ANY(source_episode_ids)",
                episode_d, episode_e,
            )
            assert count == 0
        finally:
            await _cleanup(
                pool, [session_d, session_e], [project_d, project_e],
                [e for e in (episode_d, episode_e) if e],
            )
            await pool.close()

    asyncio.run(_run())


def test_synthesis_dry_run_persists_nothing():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_a = f"{SESSION_PREFIX}-dryrun-a"
        session_b = f"{SESSION_PREFIX}-dryrun-b"
        episode_a = episode_b = None
        try:
            await _cleanup(pool, [session_a, session_b], [])
            episode_a = await _build_episode(
                pool, session_id=session_a, project_id=f"{PROJECT_PREFIX}-dryrun-a",
                file_path="a.py", test_command="pytest tests/test_a.py",
                tag="procsyn-test-dryrun-a",
            )
            episode_b = await _build_episode(
                pool, session_id=session_b, project_id=f"{PROJECT_PREFIX}-dryrun-b",
                file_path="b.py", test_command="pytest tests/test_b.py",
                tag="procsyn-test-dryrun-b",
            )

            result = await synthesize_procedure(pool, [episode_a, episode_b], dry_run=True)

            assert result.synthesized
            assert result.procedure_id is None
            assert result.extracted is not None

            count = await pool.fetchval(
                "SELECT count(*) FROM procedures WHERE $1 = ANY(source_episode_ids) "
                "OR $2 = ANY(source_episode_ids)",
                episode_a, episode_b,
            )
            assert count == 0
        finally:
            await _cleanup(
                pool, [session_a, session_b], [],
                [e for e in (episode_a, episode_b) if e],
            )
            await pool.close()

    asyncio.run(_run())


def test_synthesis_refuses_fewer_than_two_episodes():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            result = await synthesize_procedure(pool, ["00000000-0000-0000-0000-000000000001"])
            assert not result.synthesized
            assert "at least 2" in result.refusal_reason
        finally:
            await pool.close()

    asyncio.run(_run())
