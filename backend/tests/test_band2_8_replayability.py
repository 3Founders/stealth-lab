"""Band 2.8 proving tests -- end-to-end replayability.

Offline section proves the pure surface of app/execution/replay.py:
deterministic regeneration, canonical fingerprinting, the extractor
stamp registry pinned against the constants that actually govern each
write path, and static DDL/writer assertions for migration 26.

E2E section (real DATABASE_URL, skips without one) runs the founding
loop on raw traces only -- events -> normalize jobs -> observations ->
promoted claims -> deterministic procedure candidate -- and proves
replay_session() regenerates every layer bit-identically, twice, with
tamper-detection teeth, plus the spec's own sentence shape: claim C was
produced by extractor X from trace E.

Appendix C rows exercised: replayability invariants (#1-2 family),
extractor-version stamping per spec.md REPLAYABILITY.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import asyncpg
import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")

from app.execution.replay import (
    CLAIM_PROMOTION_CODE_VERSION,
    CLAIM_PROMOTION_EXTRACTOR_NAME,
    PROCEDURE_CANDIDATE_DETERMINISTIC_TAG,
    expected_claim_shape,
    extractor_stamps,
    fingerprint,
    regenerate_observations,
)
from app.services.observations import (
    CLAIM_PROMOTION_STAMP,
    DETERMINISTIC_CODE_VERSION,
    DETERMINISTIC_EXTRACTOR_NAME,
    MODEL_CODE_VERSION,
    MODEL_EXTRACTOR_NAME,
)

# ================================================================ fixtures

def _events() -> list[dict]:
    # Stable synthetic event ids: raw-event ids are assigned by STORAGE,
    # not by extraction -- regeneration consumes them, it must not
    # produce them. Two runs over the same raw events differ in nothing.
    return [
        {"id": f"00000000-0000-7e28-8000-{i:012x}", "sequence": i, "event_type": "PostToolUse",
         "tool_name": tool, "tool_input": tool_input}
        for i, (tool, tool_input) in enumerate([
            ("Edit", {"file_path": "src/replay28_auth.py"}),
            ("Bash", {"command": "git commit -m 'replay28 fix'"}),
            ("Bash", {"command": "pytest tests/ -q"}),
            ("Bash", {"command": "npm install"}),
            ("Read", {"file_path": "notes.md"}),  # yields nothing
            ("Bash", {}),  # empty command: yields nothing
        ])
    ]


# ================================================= offline: regeneration

def test_regeneration_is_deterministic_across_runs():
    first = regenerate_observations(_events())
    second = regenerate_observations(_events())
    assert first == second
    assert [fingerprint(o) for o in first] == [fingerprint(o) for o in second]


def test_regeneration_is_input_order_independent():
    events = _events()
    shuffled = list(reversed(events))
    assert regenerate_observations(shuffled) == regenerate_observations(events)


def test_regeneration_handles_json_encoded_tool_input():
    """asyncpg without the JSONB codec hands tool_input back as a str --
    observations.py's extractor already handles both; so must replay."""
    event = {"id": str(uuid.uuid4()), "sequence": 0, "event_type": "PostToolUse",
             "tool_name": "Edit", "tool_input": json.dumps({"file_path": "a.py"})}
    out = regenerate_observations([event])
    assert len(out) == 1
    assert out[0]["observation_type"] == "file_touched"


def test_events_yielding_nothing_yield_nothing():
    assert regenerate_observations([]) == []
    bare = [{"sequence": 9, "event_type": "PostToolUse", "tool_name": "Grep",
             "tool_input": {"pattern": "x"}}]
    assert regenerate_observations(bare) == []


def test_regenerated_shapes_carry_full_extractor_stamps():
    for obs in regenerate_observations(_events()):
        assert obs["extractor_kind"] == "deterministic"
        assert obs["extractor_name"] == DETERMINISTIC_EXTRACTOR_NAME
        assert obs["code_version"] == DETERMINISTIC_CODE_VERSION


# ============================================== offline: stamps registry

def test_extractor_stamps_pin_authoritative_values():
    stamps = extractor_stamps()
    assert stamps == {
        "normalize_trace_events": "trace_worker@1",
        "observation.deterministic": f"{DETERMINISTIC_EXTRACTOR_NAME}"
                                     f"@{DETERMINISTIC_CODE_VERSION}",
        "observation.model": f"{MODEL_EXTRACTOR_NAME}@{MODEL_CODE_VERSION}",
        "claim.promotion": f"{CLAIM_PROMOTION_EXTRACTOR_NAME}@{CLAIM_PROMOTION_CODE_VERSION}",
        "procedure_candidate.deterministic": PROCEDURE_CANDIDATE_DETERMINISTIC_TAG,
    }
    # literal pins: if upstream renames/reversions anything, THIS fails
    # loudly rather than provenance silently forking
    assert PROCEDURE_CANDIDATE_DETERMINISTIC_TAG == "deterministic_v1@1"
    assert f"{CLAIM_PROMOTION_EXTRACTOR_NAME}@{CLAIM_PROMOTION_CODE_VERSION}" == \
        "claim_promotion@1"


def test_procedure_tag_matches_migration20_registry_seed():
    ddl = _ddl(20, "procedure_extraction.sql")
    assert "'deterministic_v1'" in ddl, "registry seed row renamed?"
    assert "deterministic_v1" in PROCEDURE_CANDIDATE_DETERMINISTIC_TAG


def test_claim_promotion_stamp_agrees_across_modules():
    assert CLAIM_PROMOTION_STAMP == (
        f"{CLAIM_PROMOTION_EXTRACTOR_NAME}@{CLAIM_PROMOTION_CODE_VERSION}"
    )


# ============================================ offline: fingerprinting

def test_fingerprint_is_key_order_insensitive_and_value_sensitive():
    assert fingerprint({"a": 1, "b": [2, 3]}) == fingerprint({"b": [2, 3], "a": 1})
    assert fingerprint({"a": 1}) != fingerprint({"a": "1"})
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})


# ================================== offline: claim promotion transform

def test_expected_claim_shape_deterministic_observation():
    shape = expected_claim_shape({
        "label": "Ran tests: pytest -q", "observation_type": "test_run",
        "extractor_kind": "deterministic",
        "extractor_name": DETERMINISTIC_EXTRACTOR_NAME,
        "code_version": DETERMINISTIC_CODE_VERSION, "model_id": None,
    })
    assert shape == {
        "statement": "Ran tests: pytest -q",
        "claim_type": "test_run",
        "epistemic_status": "observed",
        "extraction_version": f"{DETERMINISTIC_EXTRACTOR_NAME}:{DETERMINISTIC_CODE_VERSION}",
        "promoted_by": "claim_promotion@1",
    }


def test_expected_claim_shape_model_observation_appends_model_id():
    shape = expected_claim_shape({
        "label": "auth implementation modified", "observation_type": "semantic_label",
        "extractor_kind": "model", "extractor_name": MODEL_EXTRACTOR_NAME,
        "code_version": MODEL_CODE_VERSION, "model_id": "gemma-4-31B-it",
    })
    assert shape["epistemic_status"] == "inferred"
    assert shape["extraction_version"] == \
        f"{MODEL_EXTRACTOR_NAME}:{MODEL_CODE_VERSION}:gemma-4-31B-it"


# ================================ offline: migration 26 + writer checks

def _ddl(number: int, suffix: str) -> str:
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "db" / f"{number}_{suffix}"
    return p.read_text(encoding="utf-8")


def test_migration26_creates_claim_sources_with_both_fks():
    ddl = _ddl(26, "replayability.sql")
    assert "CREATE TABLE IF NOT EXISTS claim_sources" in ddl
    assert "REFERENCES knowledge_nodes(id)" in ddl
    assert "REFERENCES observations(id)" in ddl
    assert "PRIMARY KEY (claim_id, observation_id)" in ddl
    assert "idx_claim_sources_observation" in ddl, "reverse direction must be indexed"


def test_migration26_has_no_backfill_statements():
    ddl = _ddl(26, "replayability.sql")
    assert "UPDATE " not in ddl.upper().replace("UPPER", ""), \
        "fresh-start ruling: migration must not contain data backfills"


def test_migration26_documents_the_full_chain():
    ddl = _ddl(26, "replayability.sql")
    for marker in ("observation_events", "extracted_by", "extraction_version"):
        assert marker in ddl, marker


def test_claim_source_writer_is_wired_into_promotion():
    import inspect

    import app.services.observations as obs_module
    text = inspect.getsource(obs_module)
    assert "INSERT INTO claim_sources" in text, \
        "promotion must write the provenance link, not just the claim"
    assert obs_module.CLAIM_PROMOTION_STAMP == "claim_promotion@1"


# ======================================================== e2e (live DB)

SESSION_ID = "replay28-session-001"
TRACE_ID = "replay28-trace-001"
SKILL_REF = "replay28_skill"
PROJECT_ENTITY = "replay28-project"
GOAL = "replay28: ship the feature end to end"

e2e = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- live-database integration test"
)

class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.3] * 1024


async def _cleanup(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM claim_sources WHERE claim_id IN "
            "(SELECT id FROM knowledge_nodes WHERE created_by = 'claim_capture' "
            " AND properties->>'statement' LIKE '%replay28%')"
        )
        await conn.execute(
            "DELETE FROM edges WHERE source_id IN "
            "(SELECT id FROM knowledge_nodes WHERE created_by = 'claim_capture' "
            " AND properties->>'statement' LIKE '%replay28%') "
            "OR target_id IN "
            "(SELECT id FROM knowledge_nodes WHERE created_by = 'claim_capture' "
            " AND properties->>'statement' LIKE '%replay28%')"
        )
        await conn.execute(
            "DELETE FROM knowledge_nodes WHERE created_by = 'claim_capture' "
            "AND properties->>'statement' LIKE '%replay28%'"
        )
        await conn.execute("DELETE FROM procedures WHERE name LIKE '%replay28%'")
        await conn.execute("DELETE FROM ingestion_jobs WHERE payload::text LIKE '%replay28%'")
        await conn.execute(
            "DELETE FROM observation_events WHERE event_id IN "
            "(SELECT id FROM trace_events WHERE session_id = $1)", SESSION_ID,
        )
        await conn.execute(
            "DELETE FROM observations WHERE id NOT IN "
            "(SELECT observation_id FROM observation_events)"
        )
        await conn.execute("DELETE FROM trace_events WHERE session_id = $1", SESSION_ID)
        await conn.execute("DELETE FROM agent_traces WHERE session_id = $1", SESSION_ID)
        await conn.execute("DELETE FROM task_nodes WHERE skill_ref = $1", SKILL_REF)


async def _seed_raw_traces(pool: asyncpg.Pool) -> tuple[list[str], list[int]]:
    """Raw layer ONLY: agent_traces header + trace_events rows + the
    ingestion_jobs rows process_collector_file() would have written.
    Everything downstream is produced by running the real pipeline.

    Returns (event_ids, job_ids) -- job_ids lets callers scope a
    post-processing status check to exactly the jobs THIS call seeded
    (same pattern test_ingestion_jobs_e2e.py's _seed_event uses), rather
    than trusting process_pending_jobs()'s aggregate counts on this
    shared, concurrently-used queue."""
    await pool.execute(
        "INSERT INTO agent_traces (trace_id, session_id, started_at, schema_version) "
        "VALUES ($1, $2, now(), '1') ON CONFLICT (trace_id) DO NOTHING",
        TRACE_ID, SESSION_ID,
    )
    raw = [
        (0, "Edit", {"file_path": "replay28_feature.py"}),
        (1, "Bash", {"command": "git commit -m 'replay28: add feature'"}),
        (2, "Bash", {"command": "pytest tests/test_replay28.py -q"}),
        (3, "Bash", {"command": "npm install"}),
    ]
    event_ids: list[str] = []
    job_ids: list[int] = []
    for seq, tool, tool_input in raw:
        event_id = await pool.fetchval(
            "INSERT INTO trace_events (trace_id, session_id, sequence, event_type, "
            "\"timestamp\", tool_name, tool_input, dedup_key, schema_version) "
            "VALUES ($1,$2,$3,'PostToolUse',now(),$4,$5,$6,'1') RETURNING id",
            TRACE_ID, SESSION_ID, seq, tool, tool_input,
            f"replay28-dedup-{seq}",
        )
        event_ids.append(str(event_id))
        job_id = await pool.fetchval(
            "INSERT INTO ingestion_jobs (job_type, payload) VALUES ('normalize_trace_event', $1) "
            "RETURNING id",
            json.dumps({"trace_event_id": str(event_id), "dedup_key": f"replay28-dedup-{seq}"}),
        )
        job_ids.append(job_id)
    return event_ids, job_ids


@e2e
def test_founding_loop_replays_bit_identically_from_raw_traces():
    """Raw traces -> real normalize jobs -> observations -> promoted
    claims with provenance links; replay regenerates both derived
    layers bit-identically, twice, and catches tampering."""
    from app.db.session import create_pool
    from app.execution.replay import replay_session
    from app.services.ingestion_jobs import process_pending_jobs
    from app.services.observations import promote_observation_to_claim

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            event_ids, job_ids = await _seed_raw_traces(pool)
            await pool.execute(
                "INSERT INTO task_nodes (name, skill_ref) VALUES "
                "('replay28 task', $1) RETURNING id", SKILL_REF,
            )

            # --- run the REAL pipeline over the raw traces ----------
            # Scoped to this test's own 4 job_ids, not process_pending_
            # jobs()'s aggregate done/failed counts -- this shared,
            # concurrently-used queue may hold other pending jobs at the
            # same moment. This test's whole point IS the aggregate (all 4
            # of ITS jobs got claimed and finished together), so the count
            # query itself is scoped to just those job_ids rather than
            # trusting the queue's global state. (NOT scoped via payload
            # ->>'trace_event_id' -- that column is double-JSON-encoded by
            # this fixture's own json.dumps() call over asyncpg's jsonb
            # codec, confirmed via jsonb_typeof(payload) == 'string', so
            # ->>'key' resolves to NULL for every row; the handler already
            # tolerates this via its own `if isinstance(payload, str):
            # json.loads()` guard, but it makes payload unusable as a SQL
            # join key. Flagged on the board -- pre-existing, unrelated to
            # this task, real fix belongs in _seed_raw_traces's encoding.)
            await process_pending_jobs(pool, limit=10)
            job_statuses = await pool.fetch(
                "SELECT status, count(*) AS n FROM ingestion_jobs "
                "WHERE id = ANY($1::bigint[]) GROUP BY status",
                job_ids,
            )
            counts = {r["status"]: r["n"] for r in job_statuses}
            assert counts.get("done", 0) == 4, counts
            assert counts.get("failed", 0) == 0, counts

            obs_ids = [
                str(r["id"]) for r in await pool.fetch(
                    "SELECT DISTINCT o.id FROM observations o "
                    "JOIN observation_events oe ON oe.observation_id = o.id "
                    "JOIN trace_events te ON te.id = oe.event_id "
                    "WHERE te.session_id = $1 ORDER BY o.id",
                    SESSION_ID,
                )
            ]
            assert len(obs_ids) == 4

            claim_ids = []
            for obs_id in obs_ids:
                claim_id = await promote_observation_to_claim(
                    pool, observation_id=obs_id, task_ids=[SKILL_REF],
                    embedder=FakeEmbedder(),
                )
                assert claim_id is not None
                claim_ids.append(claim_id)
            linked = await pool.fetchval(
                "SELECT count(*) FROM claim_sources WHERE claim_id = ANY($1::uuid[])",
                claim_ids,
            )
            assert linked == 4, "every promoted claim must cite its source observation"

            # --- THE PROOF: regenerate everything from raw, compare ---
            report = await replay_session(pool, session_id=SESSION_ID)
            assert report["raw_events"] == 4
            assert report["observations"]["match"] is True, report["observations"]
            assert report["observations"]["checked"] == 4
            assert report["observations"]["regenerated"] == 4
            assert report["claims"]["match"] is True, report["claims"]
            assert report["claims"]["checked"] == 4
            for stage, stamp in report["stamps"].items():
                assert stamp and "@" in stamp, stage

            # determinism/idempotence: a second replay is byte-equal --
            # no timestamps, ids, or ordering noise in the report
            report_again = await replay_session(pool, session_id=SESSION_ID)
            assert report_again == report

            # --- tamper teeth: mutating stored derived data MUST be
            # caught by regeneration comparison, both layers down -----
            tampered = await pool.fetchval(
                "UPDATE observations SET label = label || ' TAMPERED' "
                "WHERE id = $1::uuid RETURNING label", obs_ids[0],
            )
            broken = await replay_session(pool, session_id=SESSION_ID)
            assert broken["observations"]["match"] is False
            assert broken["claims"]["match"] is False, \
                "claim statement derives from the observation label; it must flag too"
            await pool.execute(
                "UPDATE observations SET label = $2 WHERE id = $1::uuid",
                obs_ids[0], tampered[:-len(" TAMPERED")],
            )
            healed = await replay_session(pool, session_id=SESSION_ID)
            assert healed == report

            # --- the spec sentence: claim <- extractor X <- trace E ---
            row = await pool.fetchrow(
                """
                SELECT k.properties->>'extraction_version' AS version,
                       k.properties->>'epistemic_status' AS status,
                       te.dedup_key AS event_key
                FROM claim_sources cs
                JOIN knowledge_nodes k ON k.id = cs.claim_id
                JOIN observation_events oe ON oe.observation_id = cs.observation_id
                JOIN trace_events te ON te.id = oe.event_id
                WHERE te.dedup_key = 'replay28-dedup-2'
                  AND k.properties->>'claim_type' = 'test_run'
                """
            )
            assert row is not None, "test_run claim must resolve to its raw event"
            assert "deterministic_v1" in row["version"]
            assert row["status"] == "observed"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


async def _procedures_schema_ready(pool: asyncpg.Pool) -> bool:
    """True only where the Band-1 migrations (scope columns, extracted_by,
    capability_statement) are actually applied. The long-lived shared dev
    instance predates them -- known drift, queue item 2's disposable-DB
    chain run owns it -- so this proof degrades to a documented SKIP
    there instead of adding one more pre-existing red to that env."""
    return await pool.fetchval(
        "SELECT count(*) = 3 FROM information_schema.columns "
        "WHERE table_name = 'procedures' "
        "AND column_name IN ('extracted_by', 'capability_statement', 'scope_type')"
    )


@e2e
def test_procedure_candidate_replays_bit_identically_from_raw_traces():
    from app.db.session import create_pool
    from app.execution.replay import replay_session
    from app.services.ingestion_jobs import process_pending_jobs
    from app.services.procedure_extraction.evidence import AgentRunEvidenceSource
    from app.services.procedure_extraction.strategies import DeterministicExtractor
    from app.services.procedures import capture_procedure

    episode_id = str(uuid.uuid4())

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            if not await _procedures_schema_ready(pool):
                pytest.skip(
                    "procedures table lacks Band-1 columns (shared-instance "
                    "drift) -- candidate-replay proof needs the migrated schema"
                )

            await _cleanup(pool)
            event_ids, job_ids = await _seed_raw_traces(pool)

            # Same rescoping as test_founding_loop_replays_bit_identically_
            # from_raw_traces above -- see its comment for why the count
            # query is scoped to this test's own job_ids rather than
            # process_pending_jobs()'s aggregate return.
            await process_pending_jobs(pool, limit=10)
            job_statuses = await pool.fetch(
                "SELECT status, count(*) AS n FROM ingestion_jobs "
                "WHERE id = ANY($1::bigint[]) GROUP BY status",
                job_ids,
            )
            counts = {r["status"]: r["n"] for r in job_statuses}
            assert counts.get("done", 0) == 4 and counts.get("failed", 0) == 0, counts

            # --- candidate leg: deterministic extraction over shapes
            # REGENERATED from the raw events, persisted V0-clean -----
            raw_rows = await pool.fetch(
                "SELECT id, sequence, event_type, tool_name, tool_input "
                "FROM trace_events WHERE session_id = $1 ORDER BY sequence",
                SESSION_ID,
            )
            regenerated = regenerate_observations([dict(r) for r in raw_rows])
            evidence = await AgentRunEvidenceSource(
                goal_text=GOAL, outcome="success",
                observations=[
                    {"observation_type": o["observation_type"], "label": o["label"],
                     "properties": o["properties"]}
                    for o in regenerated
                ],
                tool_sequence=["Edit", "Bash", "Bash", "Bash"],
            ).collect()
            extracted = await DeterministicExtractor().extract(pool, evidence)
            captured = await capture_procedure(
                pool, name=extracted.name, goal=extracted.goal,
                steps=[s.model_dump() for s in extracted.steps],
                parameter_schema={
                    "slots": [s.model_dump() for s in extracted.slots],
                    "extraction_method": PROCEDURE_CANDIDATE_DETERMINISTIC_TAG,
                },
                preconditions=[], failure_conditions=extracted.failure_conditions,
                scope=extracted.scope, source_episode_ids=[episode_id],
                provenance="public_generated", scope_type="project",
                scope_entity_id=PROJECT_ENTITY,
            )
            await pool.execute(
                "UPDATE procedures SET capability_statement = $2, extracted_by = $3 "
                "WHERE id = $1::uuid",
                captured["id"], extracted.capability_statement,
                PROCEDURE_CANDIDATE_DETERMINISTIC_TAG,
            )

            report = await replay_session(
                pool, session_id=SESSION_ID, episode_ids=[episode_id],
            )
            assert report["procedures"]["match"] is True, report["procedures"]
            assert report["procedures"]["checked"] == 1
            assert report["observations"]["match"] is True

            # second full replay is byte-equal: regeneration, and the
            # verifier itself, are deterministic end to end
            report_again = await replay_session(
                pool, session_id=SESSION_ID, episode_ids=[episode_id],
            )
            assert report_again == report

            # a second full regeneration of the candidate is identical
            re_extracted = await DeterministicExtractor().extract(pool, evidence)
            assert fingerprint([s.model_dump() for s in re_extracted.steps]) == \
                fingerprint([s.model_dump() for s in extracted.steps])
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
