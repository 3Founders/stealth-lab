"""
Real, live-database, end-to-end proof of the trajectory ingestion
pipeline (trajectory-ingestion-hardening task, Sec 20/24): raw OpenHands
trajectory file -> `trace_events`/`agent_traces` (via
`ingestion_sources.dispatch.ingest_openhands_trajectories`, the exact
function `POST /v1/trajectories/ingest` calls) -> `episodes` (structural
segmentation) -> LLM semantic extraction (`trajectory_semantics.
extract_trajectory_semantics`, with a scripted fake client -- no live
paid model call, matching every other e2e test's "no live LLM" rule) ->
real Goal/Claim/Procedure/Implementation rows, each with a
`trajectory_extraction_objects` citation row.

Every production function used here is the REAL one -- nothing in this
file re-implements ingestion/extraction logic; this is proof the wiring
this session built actually holds together against a real database, not
just against mocks (see the sibling offline test files for the
component-level proofs with mocked service layers).

Same pattern as every other e2e file in this suite: requires a real
DATABASE_URL, skips (not fails) without one. NEVER point this at the
Supabase DATABASE_URL in backend/.env -- see backend/TESTING_DB.md.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

from app.db.session import create_pool as _real_create_pool
from app.services.access import AccessScope

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test",
)

TAG = "traj-pipeline-e2e"


class _FakeChoice:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeClient:
    """Same OpenAI-compatible `.chat.completions.create(...)` shape every
    real caller in this codebase uses -- scripted, never a live call."""

    def __init__(self, response_text: str):
        self._response_text = response_text

        class _Completions:
            def create(_self, **kwargs):
                return _FakeResponse(self._response_text)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def _semantic_response(event_count: int) -> str:
    return json.dumps({
        "primary_goal": {
            "text": f"{TAG}: fix the failing test",
            "event_indices": [1, min(2, event_count)],
            "epistemic_status": "inferred", "confidence": 0.8,
        },
        "subgoals": [],
        "candidate_procedures": [{
            "capability_statement": f"{TAG}: reproduce then fix a failing test",
            "steps": [
                {"description": "reproduce the failure", "subgoal_text": f"{TAG}: reproduce failure", "tool_name": "run",
                 "event_indices": [1]},
            ],
            "event_indices": [1], "epistemic_status": "inferred", "confidence": 0.6,
        }],
        "claims": [{
            "text": f"{TAG}: the fix was verified by a passing test run",
            "event_indices": [min(2, event_count)], "epistemic_status": "observed", "confidence": 0.9,
        }],
        "preconditions": [], "failure_modes": [], "recovery_patterns": [],
        "verification_actions": [], "outcome": "success", "reusable_elements": [],
        "uncertainties": [],
    })


async def _cleanup(pool, trace_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM trajectory_extraction_objects WHERE extraction_id IN "
            "(SELECT id FROM trajectory_extractions WHERE episode_id IN "
            " (SELECT id FROM episodes WHERE session_id = $1))",
            trace_id,
        )
        await conn.execute(
            "DELETE FROM trajectory_extractions WHERE episode_id IN "
            "(SELECT id FROM episodes WHERE session_id = $1)",
            trace_id,
        )
        await conn.execute("DELETE FROM ingestion_jobs WHERE payload::text LIKE $1", f"%{trace_id}%")
        await conn.execute(
            "DELETE FROM observation_events WHERE event_id IN "
            "(SELECT id FROM trace_events WHERE trace_id = $1)", trace_id,
        )
        await conn.execute(
            "DELETE FROM observations WHERE id NOT IN "
            "(SELECT observation_id FROM observation_events)"
        )
        await conn.execute("DELETE FROM trace_events WHERE trace_id = $1", trace_id)
        await conn.execute("DELETE FROM agent_traces WHERE trace_id = $1", trace_id)
        # episodes/knowledge_nodes/procedures/implementations/evidence are
        # append-only in this codebase (see test_ingestion_admin_endpoint_
        # e2e.py's own cleanup comment) -- left in place, tagged with TAG
        # in their text so they're identifiable/reviewable, same precedent.


def test_openhands_trajectory_full_pipeline_produces_cited_knowledge(tmp_path):
    async def _run():
        from app.services.ingestion_sources.dispatch import ingest_openhands_trajectories
        from app.services.trajectory_semantics import extract_trajectory_semantics

        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        trace_id = f"{TAG}-{uuid.uuid4().hex[:12]}"
        try:
            fixture = json.loads(
                (
                    __import__("pathlib").Path(__file__).parent
                    / "fixtures" / "trajectories" / "openhands_success.json"
                ).read_text(encoding="utf-8")
            )
            fixture["instance_id"] = trace_id  # unique per test run
            src_dir = tmp_path / "src"
            src_dir.mkdir()
            (src_dir / "trajectory.json").write_text(json.dumps(fixture), encoding="utf-8")

            # -- 1. real ingest: file -> trace_events/agent_traces/episodes.
            result = await ingest_openhands_trajectories(
                pool, str(src_dir), owner_id=None, visibility="public",
            )
            assert result["trajectories_ingested"] == 1
            assert result["objects_quarantined"] == 0
            assert result["events_normalized"] == 6  # matches test_openhands_adapter_offline.py's fixture count

            # -- 2. idempotency: re-ingesting the SAME file inserts zero new events.
            result_again = await ingest_openhands_trajectories(pool, str(src_dir))
            assert result_again["events_normalized"] == 0, "re-ingest of an unchanged file must be a no-op"

            # -- 3. raw event count preserved, Read not silently discarded.
            event_rows = await pool.fetch(
                "SELECT canonical_event_type FROM trace_events WHERE trace_id = $1 ORDER BY sequence", trace_id,
            )
            assert len(event_rows) == 6
            assert any(r["canonical_event_type"] == "READ" for r in event_rows)

            episode_row = await pool.fetchrow(
                "SELECT id FROM episodes WHERE session_id = $1 ORDER BY start_ts LIMIT 1", trace_id,
            )
            assert episode_row is not None, "structural episode assembly must have produced at least one episode"
            episode_id = str(episode_row["id"])

            # -- 4. real semantic extraction (scripted client, no live LLM call).
            client = _FakeClient(_semantic_response(event_count=6))
            extraction = await extract_trajectory_semantics(
                pool, episode_id, client=client, model="test-model",
            )
            assert extraction["goals"] >= 1
            assert extraction["claims"] == 1
            assert extraction["procedures"] == 1

            # -- 5. every produced object cites real source events (Sec 6/12).
            link_rows = await pool.fetch(
                "SELECT object_type, object_id, event_refs FROM trajectory_extraction_objects "
                "WHERE extraction_id = $1::uuid",
                extraction["extraction_id"],
            )
            assert len(link_rows) >= 4
            real_event_ids = {str(r["id"]) for r in await pool.fetch(
                "SELECT id FROM trace_events WHERE trace_id = $1", trace_id,
            )}
            for row in link_rows:
                assert row["event_refs"], f"{row['object_type']} must cite at least one event"
                for ref in row["event_refs"]:
                    assert str(ref) in real_event_ids

            # -- 6. re-extraction coexists with the first (Sec 13): a second
            # pass over the SAME episode produces a SEPARATE extraction row.
            extraction_v2 = await extract_trajectory_semantics(
                pool, episode_id, client=_FakeClient(_semantic_response(event_count=6)), model="test-model-v2",
            )
            assert extraction_v2["extraction_id"] != extraction["extraction_id"]
            still_present = await pool.fetchrow(
                "SELECT id FROM trajectory_extractions WHERE id = $1::uuid", extraction["extraction_id"],
            )
            assert still_present is not None, "the first extraction must not be deleted/overwritten"

            # -- 7. the claim this extraction produced carries the correct
            # single-trajectory generalization tier (Sec 9).
            claim_link = next(r for r in link_rows if r["object_type"] == "claim")
            claim_row = await pool.fetchrow(
                "SELECT properties FROM knowledge_nodes WHERE id = $1::uuid", claim_link["object_id"],
            )
            props = claim_row["properties"]
            if isinstance(props, str):
                props = json.loads(props)
            assert props.get("generalization_level") == "single_trace_observation"

        finally:
            await _cleanup(pool, trace_id)
            await pool.close()

    import asyncio
    asyncio.run(_run())


def test_malformed_trajectory_is_quarantined_and_never_reaches_extraction(tmp_path):
    async def _run():
        from app.services.ingestion_sources.dispatch import ingest_openhands_trajectories

        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            src_dir = tmp_path / "bad"
            src_dir.mkdir()
            (src_dir / "broken.json").write_text("{not valid json", encoding="utf-8")

            result = await ingest_openhands_trajectories(pool, str(src_dir))
            assert result["trajectories_ingested"] == 0
            assert result["objects_quarantined"] == 1

            row = await pool.fetchrow(
                "SELECT source_type, reason, resolved FROM quarantined_records "
                "WHERE source_uri LIKE $1 ORDER BY detected_at DESC LIMIT 1",
                f"%{src_dir.name}%",
            )
            assert row is not None
            assert row["resolved"] is False
        finally:
            await pool.execute(
                "DELETE FROM quarantined_records WHERE source_uri LIKE $1", f"%{tmp_path.name}%",
            )
            await pool.close()

    import asyncio
    asyncio.run(_run())


def test_private_scope_extraction_does_not_leak_to_another_viewer(tmp_path):
    """Task Sec 20: private/local scope does not leak globally."""
    async def _run():
        from app.services.ingestion_sources.dispatch import ingest_openhands_trajectories
        from app.services.trajectory_semantics import extract_trajectory_semantics

        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        trace_id = f"{TAG}-priv-{uuid.uuid4().hex[:12]}"
        owner_id = f"{TAG}-owner-{uuid.uuid4().hex[:8]}"
        try:
            fixture = json.loads(
                (
                    __import__("pathlib").Path(__file__).parent
                    / "fixtures" / "trajectories" / "openhands_success.json"
                ).read_text(encoding="utf-8")
            )
            fixture["instance_id"] = trace_id
            src_dir = tmp_path / "priv"
            src_dir.mkdir()
            (src_dir / "trajectory.json").write_text(json.dumps(fixture), encoding="utf-8")

            await ingest_openhands_trajectories(
                pool, str(src_dir), owner_id=owner_id, visibility="private",
                scope_type="user", scope_entity_id=owner_id,
            )
            episode_row = await pool.fetchrow(
                "SELECT id FROM episodes WHERE session_id = $1 ORDER BY start_ts LIMIT 1", trace_id,
            )
            extraction = await extract_trajectory_semantics(
                pool, str(episode_row["id"]), client=_FakeClient(_semantic_response(6)),
                model="test-model", owner_id=owner_id, visibility="private",
                scope_type="user", scope_entity_id=owner_id,
            )
            claim_link = await pool.fetchrow(
                "SELECT object_id FROM trajectory_extraction_objects "
                "WHERE extraction_id = $1::uuid AND object_type = 'claim' LIMIT 1",
                extraction["extraction_id"],
            )
            assert claim_link is not None

            from app.services.access import visibility_predicate
            other_scope = AccessScope.for_user(f"{TAG}-other-viewer")
            vis_sql, vis_params = visibility_predicate(other_scope, alias="", param_index=2)
            visible_to_other = await pool.fetchrow(
                f"SELECT id FROM knowledge_nodes WHERE id = $1::uuid AND {vis_sql}",
                claim_link["object_id"], *vis_params,
            )
            assert visible_to_other is None, "a private claim must not be visible to a different viewer"

            owner_scope = AccessScope.for_user(owner_id)
            vis_sql2, vis_params2 = visibility_predicate(owner_scope, alias="", param_index=2)
            visible_to_owner = await pool.fetchrow(
                f"SELECT id FROM knowledge_nodes WHERE id = $1::uuid AND {vis_sql2}",
                claim_link["object_id"], *vis_params2,
            )
            assert visible_to_owner is not None, "the owner must still see their own private claim"
        finally:
            await _cleanup(pool, trace_id)
            await pool.close()

    import asyncio
    asyncio.run(_run())
