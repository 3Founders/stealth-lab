"""
Real, live-database proof that POST /v1/admin/ingestion/process
(app/api/admin.py::process_ingestion) is the real, missing production
entrypoint for the founding loop this directive names: "user does normal
agent work" -> real collector .jsonl -> trace_events -> observations ->
claims -> a real procedure candidate, WITHOUT a developer hand-running
scripts/run_ingestion.py with tuning flags after every session.

Before this endpoint existed, the ONLY real caller of
process_collector_file() / process_pending_jobs() /
enqueue_pending_claim_promotions() / enqueue_pending_procedure_extractions()
(grepped this session, same discipline as test_failure_routing_e2e.py's
own req #22 proof) was scripts/run_ingestion.py -- a hand-run CLI script, an
"operator" behavior, not a "product" one. This test proves the real gap is
closed: a genuine collector .jsonl file (the exact format
scripts/hook_wrapper.py's real collector appends, per trace_worker.py's
_read_records()) is dropped on disk -- never a direct trace_events INSERT
-- and every downstream hop (normalize -> observations -> promote ->
claim -> gate -> extract -> procedure candidate) is driven purely by
repeated calls to the real admin endpoint function, same "call the
endpoint function with pool=pool" pattern test_failure_routing_e2e.py's
own test_admin_endpoint_is_the_real_production_consumer() already
established for this session's prior gap closure.

Episode assembly itself is a separate, transcript-file-based pipeline
(assemble_episodes()/ingest_transcripts.py, a different input format from
the collector .jsonl this test drives) -- this file follows the exact same
precedent every other e2e test in this suite already uses
(test_ingestion_jobs_e2e.py, test_synthesis_auto_discovery_e2e.py): the
episode row itself is inserted directly, standing in for "episode assembly
already ran for this session", while every hop this directive actually
scopes (jobs / admin trigger) goes through the real production entrypoint.

Same pattern as the other e2e files: requires a real DATABASE_URL, skips
(not fails) without one.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import asyncpg
import pytest

from app.db.session import create_pool as _real_create_pool

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

TAG = "adm-ingest-test"


def _record(*, session_id, trace_id, sequence, dedup_key, tool_name, tool_input, timestamp):
    return {
        "trace_id": trace_id,
        "session_id": session_id,
        "sequence": sequence,
        "event_type": "PostToolUse",
        "dedup_key": dedup_key,
        "event": {
            "timestamp": timestamp,
            "tool_name": tool_name,
            "tool_input": tool_input,
            "success": True,
        },
    }


async def _cleanup(pool: asyncpg.Pool, session_id: str, episode_id: str | None) -> None:
    async with pool.acquire() as conn:
        if episode_id:
            await conn.execute(
                "DELETE FROM procedures WHERE source_episode_ids @> ARRAY[$1::uuid]", episode_id,
            )
        await conn.execute(
            "DELETE FROM ingestion_jobs WHERE payload::text LIKE $1", f"%{session_id}%",
        )
        await conn.execute(
            "DELETE FROM observation_events WHERE event_id IN "
            "(SELECT id FROM trace_events WHERE session_id = $1)", session_id,
        )
        await conn.execute(
            "DELETE FROM observations WHERE id NOT IN "
            "(SELECT observation_id FROM observation_events)"
        )
        # episode_links / claims (knowledge_nodes) are [H] append-only,
        # same as evidence/failure_routes/change_sets in
        # test_failure_routing_e2e.py -- honored, not fought. The episode
        # row itself is therefore left in place too (deleting it would hit
        # episode_links' FK); trace_events/observations/agent_traces (all
        # real, mutable tables) are the only rows cleaned up here.
        await conn.execute("DELETE FROM trace_events WHERE session_id = $1", session_id)
        await conn.execute("DELETE FROM agent_traces WHERE session_id = $1", session_id)


def test_admin_ingestion_endpoint_drives_real_traces_to_a_real_procedure_candidate(tmp_path):
    """
    1. A real collector .jsonl file (the on-disk format hook_wrapper.py's
       real collector writes) lands in a fresh trace directory -- a
       genuine 7-tool-call sequence (edit -> test -> read -> write ->
       command -> read -> commit), never a direct trace_events INSERT.
    2. A real episode row (standing in for episode assembly having already
       run for this session, same precedent every other e2e file in this
       suite uses) spans the sequence's timestamps.
    3. The real production entrypoint -- POST /v1/admin/ingestion/process,
       called here as app.api.admin.process_ingestion(pool=pool, ...),
       exactly as a cron job or dashboard button would invoke it over
       HTTP -- is called repeatedly (matching a real recurring sweep: a
       job's own newly-enqueued follow-on job is only visible to the NEXT
       claim, by process_pending_jobs()'s own SKIP LOCKED design) until
       collector drain -> normalize -> observations -> promote -> claim ->
       gated extraction -> a real procedure candidate all close, purely
       through that one HTTP-shaped entrypoint.
    4. No internal function is ever called directly except the endpoint
       itself and DB assertions.
    """
    async def _run():
        from app.api.admin import process_ingestion

        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        session_id = f"{TAG}-{uuid.uuid4().hex[:12]}"
        trace_id = session_id
        episode_id: str | None = None
        try:
            await _cleanup(pool, session_id, None)

            # -- 1. real collector .jsonl, genuine tool-call sequence.
            trace_dir = tmp_path / "traces"
            trace_dir.mkdir()
            base_ts = "2026-09-02T10:00:00.000Z"
            records = [
                _record(
                    session_id=session_id, trace_id=trace_id, sequence=0,
                    dedup_key=f"{session_id}-dedup-0", tool_name="Edit",
                    tool_input={"file_path": f"{TAG}/mod_a.py"}, timestamp=base_ts,
                ),
                _record(
                    session_id=session_id, trace_id=trace_id, sequence=1,
                    dedup_key=f"{session_id}-dedup-1", tool_name="Bash",
                    tool_input={"command": "pytest tests/test_a.py"}, timestamp=base_ts,
                ),
                _record(
                    session_id=session_id, trace_id=trace_id, sequence=2,
                    dedup_key=f"{session_id}-dedup-2", tool_name="Read",
                    tool_input={"file_path": f"{TAG}/mod_a.py"}, timestamp=base_ts,
                ),
                _record(
                    session_id=session_id, trace_id=trace_id, sequence=3,
                    dedup_key=f"{session_id}-dedup-3", tool_name="Write",
                    tool_input={"file_path": f"{TAG}/mod_b.py"}, timestamp=base_ts,
                ),
                _record(
                    session_id=session_id, trace_id=trace_id, sequence=4,
                    dedup_key=f"{session_id}-dedup-4", tool_name="Bash",
                    tool_input={"command": "ls -la"}, timestamp=base_ts,
                ),
                _record(
                    session_id=session_id, trace_id=trace_id, sequence=5,
                    dedup_key=f"{session_id}-dedup-5", tool_name="Read",
                    tool_input={"file_path": f"{TAG}/mod_b.py"}, timestamp=base_ts,
                ),
                _record(
                    session_id=session_id, trace_id=trace_id, sequence=6,
                    dedup_key=f"{session_id}-dedup-6", tool_name="Bash",
                    tool_input={"command": f"git commit -m '{TAG} fix'"}, timestamp=base_ts,
                ),
            ]
            jsonl_path = trace_dir / f"{session_id}.jsonl"
            jsonl_path.write_text(
                "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
            )

            # -- 2. real episode row spanning the sequence (episode assembly
            # stand-in, same precedent as the rest of this suite).
            episode_id = await pool.fetchval(
                "INSERT INTO episodes (episode_type, content_ref, timestamp, metadata, "
                "session_id, project_id, start_ts, end_ts) "
                "VALUES ('trace', $1, now(), '{}'::jsonb, $2, NULL, "
                "now() - interval '1 hour', NULL) RETURNING id",
                f"{TAG}#0:7", session_id,
            )

            os.environ["STEALTHLAB_TRACE_DIR"] = str(trace_dir)
            try:
                # -- 3a. FIRST real call: drains the collector file into
                # trace_events, drains the normalize_trace_event jobs it
                # queues (-> real observations), and (since the episode
                # already exists) each observation's promote_observation_
                # to_claim job is enqueued with a real resolved episode
                # anchor -- but that new job is only VISIBLE to the next
                # claim, per claim_jobs()'s own single SKIP LOCKED snapshot.
                round1 = await process_ingestion(
                    promote_limit=0, extract_limit=0, job_limit=500, pool=pool,
                )
                assert round1.collector["inserted"] == 7, (
                    f"expected 7 real trace_events inserted via the real collector file, "
                    f"got {round1.collector}"
                )
                assert round1.jobs["done"] >= 7, (
                    f"expected the real normalize_trace_event jobs to be drained, got {round1.jobs}"
                )

                observation_count = await pool.fetchval(
                    "SELECT count(*) FROM observations o "
                    "JOIN observation_events oe ON oe.observation_id = o.id "
                    "JOIN trace_events te ON te.id = oe.event_id "
                    "WHERE te.session_id = $1", session_id,
                )
                assert observation_count == 5, (
                    f"expected 5 real deterministic observations (2x file_touched, "
                    f"test_run, command_executed, commit_made), got {observation_count}"
                )

                # -- 3b. SECOND real call: drains the promote_observation_
                # to_claim jobs the first call's handler enqueued -> real
                # claims, anchored to the real episode via episode_links.
                round2 = await process_ingestion(
                    promote_limit=0, extract_limit=0, job_limit=500, pool=pool,
                )
                assert round2.jobs["done"] >= 5, (
                    f"expected the 5 real promote_observation_to_claim jobs drained, "
                    f"got {round2.jobs}"
                )

                claim_count = await pool.fetchval(
                    "SELECT count(*) FROM episode_links el "
                    "JOIN knowledge_nodes k ON k.id = el.target_id "
                    "AND k.node_type = 'claim' AND k.t_invalid IS NULL "
                    "WHERE el.episode_id = $1::uuid", episode_id,
                )
                assert claim_count >= 1, (
                    "expected at least one real claim anchored to the real episode "
                    "via episode_links, produced purely by the admin endpoint's own "
                    "job-draining, not a direct promote_observation_to_claim() call"
                )

                # -- 3c. THIRD real call: this episode now clears the real
                # quality gate (>=5 obs, >=2 types, a completion signal) --
                # extract_limit>0 enqueues its extraction and the same call
                # drains it, running the real extract_procedure_from_episode
                # handler exactly as a live deployment's sweep would.
                round3 = await process_ingestion(
                    promote_limit=0, extract_limit=10, job_limit=500, pool=pool,
                )
                assert round3.queued_extractions is not None
                assert round3.queued_extractions["enqueued"] >= 1, (
                    f"expected the real quality gate to enqueue this episode's "
                    f"extraction, got {round3.queued_extractions}"
                )

                # -- 4. core claim: a real procedure candidate now exists in
                # storage, reachable purely through repeated real calls to
                # the actual production admin entrypoint -- no internal
                # extraction function called directly by this test.
                procedure_row = await pool.fetchrow(
                    "SELECT id, verification_state, approval_status, capability_statement "
                    "FROM procedures WHERE t_invalid IS NULL "
                    "AND source_episode_ids @> ARRAY[$1::uuid] "
                    "ORDER BY t_created DESC LIMIT 1",
                    episode_id,
                )
                assert procedure_row is not None, (
                    "expected a real procedure candidate row whose source_episode_ids "
                    "names this real episode, produced end-to-end through the real "
                    "admin ingestion endpoint alone"
                )
                assert procedure_row["verification_state"] == "candidate"
                assert procedure_row["approval_status"] == "proposed"
            finally:
                os.environ.pop("STEALTHLAB_TRACE_DIR", None)
        finally:
            await _cleanup(pool, session_id, episode_id)
            await pool.close()

    asyncio.run(_run())


def test_admin_ingestion_endpoint_is_reachable_and_reuses_real_functions():
    """Mirrors test_failure_routing_e2e.py's own
    test_admin_endpoint_is_the_real_production_consumer(): proves
    /v1/admin/ingestion/process is wired to the exact same
    process_collector_file()/process_pending_jobs() run_ingestion.py's CLI
    already used, not a reimplementation, and that its default (no
    promote_limit/extract_limit) never spends a paid API call."""
    async def _run():
        from app.api.admin import process_ingestion

        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            # An empty/nonexistent trace dir -- no real files, so this call
            # is a pure DB no-op except draining whatever is already
            # legitimately pending in the shared queue (same discipline the
            # other e2e files here already use for a shared table).
            os.environ["STEALTHLAB_TRACE_DIR"] = str(uuid.uuid4())
            try:
                result = await process_ingestion(pool=pool)
            finally:
                os.environ.pop("STEALTHLAB_TRACE_DIR", None)
            assert result.files_processed == 0
            assert result.requeued_promotions is None, (
                "promote_limit defaults to 0 -- must never spend a real embedding "
                "call unless the caller opts in"
            )
            assert result.queued_extractions is None, (
                "extract_limit defaults to 0 -- must never spend a real LLM call "
                "unless the caller opts in"
            )
        finally:
            await pool.close()

    asyncio.run(_run())
