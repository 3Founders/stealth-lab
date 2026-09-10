"""
Real, live-database proving test for the auto-discovery wiring added to
app/services/ingestion_jobs.py::_maybe_auto_synthesize() /
_discover_synthesis_candidates() (called from
handle_extract_procedure_from_episode() -- the real production
`extract_procedure_from_episode` job handler, not a test harness).

Before this wiring, `synthesize_procedure()` (procedure_extraction/
synthesis.py) had ZERO production callers -- grepped this session:
every real reference outside that module lived in
test_procedure_synthesis_e2e.py / test_synthesis_generalization_offline.py,
which both pass a hand-picked `episode_ids` list. This test proves the
required real path instead: THREE real, distinct, compatible episodes are
extracted one at a time through the SAME production job handler normal
ingestion uses (`handle_extract_procedure_from_episode`, exactly as
`process_pending_jobs` would dispatch it) -- with NO caller ever supplying
a batch of episode ids -- and by the third episode, real DB retrieval
(scoped to the shared project, over already-captured single-episode
procedures) has discovered the other two and triggered a real, unchanged
`synthesize_procedure()` call that persists one generalized (L2) procedure
whose `source_episode_ids` names all three.

A FOURTH, structurally incompatible episode (same project, a materially
different tool-call pattern -- mirrors test_procedure_synthesis_e2e.py's
own gate-(c) fixture) is then extracted through the identical path and
must NOT get blended into that generalized procedure: the auto-discovered
batch including it is refused by synthesis.py's own real structural gate,
and its own single-episode procedure survives, untouched, as a distinct
alternative.
"""
import asyncio
import json
import os

import asyncpg
import pytest

from app.db.session import create_pool as _real_create_pool
from app.services.ingestion_jobs import handle_extract_procedure_from_episode
from app.services.observations import persist_observation

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

SESSION_PREFIX = "autosyn-test-session"
PROJECT_ID = "autosyn-test-project"


async def _cleanup(pool: asyncpg.Pool, session_ids: list[str], episode_ids: list[str]) -> None:
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


async def _insert_event(pool, *, trace_id, session_id, sequence, tool_name, dedup_key):
    return await pool.fetchval(
        "INSERT INTO trace_events (trace_id, session_id, sequence, event_type, "
        "\"timestamp\", tool_name, dedup_key, schema_version) "
        "VALUES ($1,$2,$3,'PostToolUse',now(),$4,$5,'1') RETURNING id",
        trace_id, session_id, sequence, tool_name, dedup_key,
    )


async def _build_compatible_episode(
    pool, *, session_id: str, file_path: str, test_command: str, tag: str,
    with_grep_step: bool = False,
) -> str:
    """Same real 'Edit a file -> run tests (passing) -> commit' shape
    test_procedure_synthesis_e2e.py's own `_build_episode` uses -- kept
    deliberately identical in structure (not shared, per this repo's own
    fake/fixture-isolation convention) so episodes built here are
    genuinely comparable to that file's own compatible pair."""
    await pool.execute(
        "INSERT INTO agent_traces (trace_id, session_id, started_at, schema_version, project_id) "
        "VALUES ($1, $2, now(), '1', $3) ON CONFLICT (trace_id) DO NOTHING",
        session_id, session_id, PROJECT_ID,
    )
    e1 = await _insert_event(
        pool, trace_id=session_id, session_id=session_id, sequence=0,
        tool_name="Edit", dedup_key=f"{tag}-dedup-1",
    )
    await persist_observation(
        pool, observation_type="file_touched", label=f"Modified {file_path}",
        extractor_kind="deterministic", event_ids=[str(e1)],
        properties={"file_path": file_path, "tool_name": "Edit"},
    )
    seq = 1
    if with_grep_step:
        # REAL structural variation, load-bearing for this test's L2
        # (multi-episode) generalization claim: one contributing episode
        # genuinely searched the codebase (Grep) before testing, the
        # others didn't -- a real, checkable difference between the
        # contributing episodes' own tool-call skeletons
        # (synthesis.py::_has_real_variation), not an LLM rewording of
        # otherwise-identical evidence. Still similar enough (edit-
        # distance) to pass the real structural-alignment gate.
        e_grep = await _insert_event(
            pool, trace_id=session_id, session_id=session_id, sequence=seq,
            tool_name="Grep", dedup_key=f"{tag}-dedup-grep",
        )
        await persist_observation(
            pool, observation_type="semantic_label", label="Searched for related usages",
            extractor_kind="deterministic", event_ids=[str(e_grep)], properties={},
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
    # A Read between the two Bash calls -- real, plausible ("check the
    # test output before committing") -- and load-bearing for this test's
    # own reliability: it keeps the two Bash calls as separate tool-call
    # GROUPS (derive_step_skeleton groups only CONSECUTIVE same-tool
    # calls) rather than one "Bash x2" group, which the real
    # GroundedHybridExtractor's step-count parser (strategies.py::
    # _parse_abstraction_response) was observed this session to
    # frequently mis-expand into extra phrases, falling back to
    # DeterministicExtractor's ABSTAIN-equivalent (capability_statement
    # == goal_text verbatim) -- a real, model-specific flakiness this
    # fixture works around by giving the real extractor a shape it
    # reliably handles, not by weakening the production code under test.
    e3 = await _insert_event(
        pool, trace_id=session_id, session_id=session_id, sequence=seq,
        tool_name="Read", dedup_key=f"{tag}-dedup-3",
    )
    await persist_observation(
        pool, observation_type="semantic_label", label="Checked test output",
        extractor_kind="deterministic", event_ids=[str(e3)], properties={},
    )
    seq += 1
    e4 = await _insert_event(
        pool, trace_id=session_id, session_id=session_id, sequence=seq,
        tool_name="Bash", dedup_key=f"{tag}-dedup-4",
    )
    await persist_observation(
        pool, observation_type="commit_made", label="Committed the fix",
        extractor_kind="deterministic", event_ids=[str(e4)],
        properties={"command": f"git commit -m '{tag} fix'"},
    )
    episode_id = await pool.fetchval(
        "INSERT INTO episodes (episode_type, content_ref, timestamp, metadata, "
        "session_id, project_id, start_ts, end_ts) "
        "VALUES ('trace', $1, now(), $2::jsonb, $3, $4, now() - interval '1 hour', NULL) "
        "RETURNING id",
        f"{tag}#0:4", {"segmenter": "test-fixture"}, session_id, PROJECT_ID,
    )
    return str(episode_id)


async def _build_incompatible_episode(pool, *, session_id: str, tag: str) -> str:
    """A genuinely different tool-call PATTERN in the SAME project (mostly
    research: Read/Grep/Read), with its own real test_run so the
    verification-agreement gate agrees -- the refusal this proves comes
    from the structural gate, not a different one (same discipline
    test_procedure_synthesis_e2e.py's own structural-mismatch fixture
    uses)."""
    await pool.execute(
        "INSERT INTO agent_traces (trace_id, session_id, started_at, schema_version, project_id) "
        "VALUES ($1, $2, now(), '1', $3) ON CONFLICT (trace_id) DO NOTHING",
        session_id, session_id, PROJECT_ID,
    )
    for i, tool in enumerate(["Read", "Grep", "Read"]):
        event_id = await _insert_event(
            pool, trace_id=session_id, session_id=session_id, sequence=i,
            tool_name=tool, dedup_key=f"{tag}-dedup-{i}",
        )
        await persist_observation(
            pool, observation_type="semantic_label", label=f"{tool} step {i}",
            extractor_kind="deterministic", event_ids=[str(event_id)],
            properties={},
        )
    e_test = await _insert_event(
        pool, trace_id=session_id, session_id=session_id, sequence=3,
        tool_name="Bash", dedup_key=f"{tag}-dedup-3",
    )
    await persist_observation(
        pool, observation_type="test_run", label="Ran tests (passing)",
        extractor_kind="deterministic", event_ids=[str(e_test)],
        properties={"command": "pytest tests/test_unrelated.py", "passed": True},
    )
    e_last = await _insert_event(
        pool, trace_id=session_id, session_id=session_id, sequence=4,
        tool_name="Read", dedup_key=f"{tag}-dedup-4",
    )
    await persist_observation(
        pool, observation_type="semantic_label", label="Read step 4",
        extractor_kind="deterministic", event_ids=[str(e_last)],
        properties={},
    )
    episode_id = await pool.fetchval(
        "INSERT INTO episodes (episode_type, content_ref, timestamp, metadata, "
        "session_id, project_id, start_ts, end_ts) "
        "VALUES ('trace', $1, now(), '{}'::jsonb, $2, $3, now() - interval '1 hour', NULL) "
        "RETURNING id",
        f"{tag}#0:5", session_id, PROJECT_ID,
    )
    return str(episode_id)


def test_auto_discovery_generalizes_three_episodes_and_refuses_a_fourth():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        sessions = [f"{SESSION_PREFIX}-{s}" for s in ("a", "b", "c", "d")]
        episode_ids: list[str] = []
        try:
            await _cleanup(pool, sessions, [])

            episode_a = await _build_compatible_episode(
                pool, session_id=sessions[0], file_path="auth/login.py",
                test_command="pytest tests/test_login.py", tag="autosyn-a",
            )
            episode_b = await _build_compatible_episode(
                pool, session_id=sessions[1], file_path="payments/checkout.py",
                test_command="pytest tests/test_checkout.py", tag="autosyn-b",
            )
            episode_c = await _build_compatible_episode(
                pool, session_id=sessions[2], file_path="billing/invoice.py",
                test_command="pytest tests/test_invoice.py", tag="autosyn-c",
                with_grep_step=True,
            )
            episode_d = await _build_incompatible_episode(
                pool, session_id=sessions[3], tag="autosyn-d",
            )
            episode_ids = [episode_a, episode_b, episode_c, episode_d]

            # THE REAL PRODUCTION ENTRYPOINT -- no episode_ids batch is
            # ever passed by this test. Each call only names ONE episode,
            # exactly as process_pending_jobs() dispatching a real
            # 'extract_procedure_from_episode' job would.
            for episode_id, session_id in (
                (episode_a, sessions[0]), (episode_b, sessions[1]),
                (episode_c, sessions[2]),
            ):
                await handle_extract_procedure_from_episode(
                    pool, {
                        "episode_id": episode_id, "session_id": session_id,
                        "goal_text": "Resolve the fixture issue through implementation and tests",
                        "outcome": "success",
                    },
                )

            # --- core claim: a generalized (L2) procedure now exists
            # whose real provenance names all three compatible episodes,
            # discovered by the production handler's own retrieval query,
            # never by this test supplying a batch. ---
            synthesized = await pool.fetchrow(
                "SELECT id, source_episode_ids, parameter_schema, extracted_by, "
                "verification_state, approval_status FROM procedures "
                "WHERE t_invalid IS NULL AND array_length(source_episode_ids, 1) >= 3 "
                "AND source_episode_ids @> $1::uuid[] "
                "ORDER BY t_created DESC LIMIT 1",
                [episode_a, episode_b, episode_c],
            )
            assert synthesized is not None, (
                "expected auto-discovery to have produced a >=3-episode "
                "generalized procedure over A, B, C"
            )
            assert set(str(s) for s in synthesized["source_episode_ids"]) >= {
                episode_a, episode_b, episode_c,
            }
            assert synthesized["extracted_by"] == "multi_episode_synthesis_v1@1"
            assert synthesized["verification_state"] == "candidate"
            assert synthesized["approval_status"] == "proposed"
            gen_level = synthesized["parameter_schema"]["generalization_level"]
            assert gen_level == 2, (
                f"expected real multi-episode generalization (level 2), got {gen_level} -- "
                "three episodes over genuinely different literal file paths, not an "
                "LLM rewording of one, is the required real variation"
            )

            # --- now process the incompatible 4th episode through the
            # SAME real entrypoint. Its own single-episode procedure must
            # be written (extract_procedure() itself doesn't care about
            # synthesis), but auto-discovery must REFUSE to blend it into
            # a falsely-universal procedure with A/B/C. ---
            await handle_extract_procedure_from_episode(
                pool, {
                    "episode_id": episode_d, "session_id": sessions[3],
                    "goal_text": "Resolve the fixture issue through implementation and tests",
                    "outcome": "success",
                },
            )

            d_solo = await pool.fetchrow(
                "SELECT id FROM procedures WHERE t_invalid IS NULL "
                "AND source_episode_ids = ARRAY[$1::uuid]",
                episode_d,
            )
            assert d_solo is not None, "episode D's own single-episode procedure must survive untouched"

            contaminated = await pool.fetchval(
                "SELECT count(*) FROM procedures WHERE t_invalid IS NULL "
                "AND $1::uuid = ANY(source_episode_ids) "
                "AND array_length(source_episode_ids, 1) > 1",
                episode_d,
            )
            assert contaminated == 0, (
                "episode D (structurally incompatible) must never be blended into any "
                "multi-episode synthesized procedure -- refuse or keep as a distinct "
                "alternative, never a false universal method"
            )
        finally:
            await _cleanup(pool, sessions, episode_ids)
            await pool.close()

    asyncio.run(_run())
