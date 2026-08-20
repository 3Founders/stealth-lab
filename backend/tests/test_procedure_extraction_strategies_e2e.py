"""
Real, live-database tests for procedure_extraction/strategies.py. Same
pattern as the other e2e test files: requires a real DATABASE_URL, skips
(not fails) without one. LLM calls are scripted (FakeClient, same
convention test_htn_agent.py uses) -- no real network call is verified
here, matching every other LLM-calling function in this codebase this
session (extract_model_observation has the same honest caveat).
"""
import asyncio
import os
import types

import asyncpg
import pytest

from app.db.session import create_pool as _real_create_pool
from app.services.environment_probe import assert_environment_claims
from app.services.procedure_extraction.evidence import ProcedureEvidence
from app.services.procedure_extraction.strategies import (
    DeterministicExtractor,
    GroundedHybridExtractor,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PROJECT_ID = "stratexy-test-project-001"
SUBJECT = f"project:{PROJECT_ID}"


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.6] * 1024


class FakeClient:
    """Same convention as tests/test_htn_agent.py's FakeClient -- a
    scripted response list, records every request."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        content = self.script.pop(0) if self.script else "ABSTAIN"
        msg = types.SimpleNamespace(content=content)
        choice = types.SimpleNamespace(message=msg)
        return types.SimpleNamespace(choices=[choice])


async def _cleanup(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM edges WHERE source_id IN "
            "(SELECT id FROM knowledge_nodes WHERE properties->>'subject' = $1) "
            "OR target_id IN (SELECT id FROM knowledge_nodes WHERE properties->>'subject' = $1)",
            SUBJECT,
        )
        await conn.execute("DELETE FROM knowledge_nodes WHERE properties->>'subject' = $1", SUBJECT)


def _evidence(project_id, started_at) -> ProcedureEvidence:
    return ProcedureEvidence(
        goal_text="fix the failing login test",
        outcome="success",
        tool_sequence=["Read", "Read", "Edit", "Bash"],
        observations=[
            {"observation_type": "file_touched", "properties": {"file_path": "auth/login.py"}},
            {"observation_type": "test_run", "properties": {"passed": True}},
        ],
        project_id=project_id, started_at=started_at,
    )


def test_deterministic_extractor_produces_a_valid_procedure_with_no_llm(tmp_path):
    import json
    from datetime import datetime, timedelta, timezone
    (tmp_path / "requirements.txt").write_text("pytest\n")

    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            now = datetime.now(timezone.utc)
            await assert_environment_claims(
                pool, project_id=PROJECT_ID, repo_root=str(tmp_path), embedder=FakeEmbedder(),
            )
            ev = _evidence(PROJECT_ID, now + timedelta(seconds=2))

            extractor = DeterministicExtractor()
            proc = await extractor.extract(pool, ev)

            assert proc.steps, "deterministic extractor must always produce steps from a real tool sequence"
            assert any(p.predicate == "has_test_runner" for p in proc.preconditions), (
                "deterministic extractor's preconditions must be real, derived facts"
            )
            # Literal, not abstracted -- this extractor makes no claim of generalization.
            assert proc.capability_statement == ev.goal_text[:200]
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_grounded_hybrid_extractor_uses_llm_output_when_well_formed():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            ev = _evidence(None, None)  # no project_id -- preconditions/scope stay empty, fine here
            client = FakeClient([
                "CAPABILITY: locate the failing test's source file and apply a targeted fix\n"
                "STEPS: read the relevant files; apply a fix; run the test suite",
            ])
            extractor = GroundedHybridExtractor(client)
            proc = await extractor.extract(pool, ev)

            assert "locate the failing test" in proc.capability_statement
            assert len(proc.steps) == 3
            assert len(client.requests) == 1
        finally:
            await pool.close()

    asyncio.run(_run())


def test_grounded_hybrid_extractor_falls_back_on_malformed_llm_response():
    """Degradation is explicit: a response that doesn't parse must fall
    back to DeterministicExtractor's real output, never propagate a
    malformed procedure."""
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            ev = _evidence(None, None)
            client = FakeClient(["this is not the expected format at all"])
            extractor = GroundedHybridExtractor(client)
            proc = await extractor.extract(pool, ev)

            # Fallback signature: literal capability_statement, same as
            # DeterministicExtractor's own direct output for this evidence.
            assert proc.capability_statement == ev.goal_text[:200]
        finally:
            await pool.close()

    asyncio.run(_run())


def test_grounded_hybrid_extractor_falls_back_with_no_client():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            ev = _evidence(None, None)
            extractor = GroundedHybridExtractor(None)
            proc = await extractor.extract(pool, ev)
            assert proc.capability_statement == ev.goal_text[:200]
        finally:
            await pool.close()

    asyncio.run(_run())


def test_grounded_hybrid_extractor_falls_back_on_step_count_mismatch():
    async def _run():
        pool = await _real_create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            ev = _evidence(None, None)  # skeleton has 3 groups: Read, Edit, Bash
            client = FakeClient([
                "CAPABILITY: do a thing\nSTEPS: only one step",
            ])
            extractor = GroundedHybridExtractor(client)
            proc = await extractor.extract(pool, ev)
            assert proc.capability_statement == ev.goal_text[:200], (
                "a STEPS list that doesn't match the real skeleton's group count must fall back"
            )
        finally:
            await pool.close()

    asyncio.run(_run())
