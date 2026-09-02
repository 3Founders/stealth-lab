"""
Task spec §11 -- live-DB proof of the canonical admission boundary
(compile_skill_artifact/run_skill_ingestion) for the four repo-procedural
source families tests/test_skill_ingestion_e2e.py does not cover
(AGENTS.md/CLAUDE.md, RUNBOOK, CI workflow -- that file only exercises
SKILL.md). Does not re-prove what that file and
tests/evaluation/security/test_injection_adversarial_offline.py already
establish about the compiler itself; this file's own question is whether
the OTHER four adapters land correctly through the SAME shared compiler:
idempotency (byte-identical re-ingestion is a no-op), provenance
(ingested_artifacts row + run_id survives), and scope (an entity-scoped
ingestion is recorded as entity-scoped, not silently promoted to global).

client=None throughout -- compile_skill_artifact/_abstract_capability
degrade to capability_abstained=True with no model call (see
skill_ingestion.py's own docstring: "no client" is one of the documented
None-returning paths), so this file makes zero live LLM calls.

A deterministic _FakeEmbedder is used instead of a real Embedder() --
matching tests/test_product_model_e2e.py and
tests/evaluation/durable/test_gold_durable_e2e.py's own established
convention -- because this dev environment's real Voyage key has no
payment method attached and is capped at 3 RPM (confirmed directly: back-
to-back real Embedder() calls across this file's 4 tests hit that cap and
every provider in the fallback chain failed). This file's own question is
the admission boundary's idempotency/provenance/scope contract, not
embedding-model quality -- real embedding-level retrieval quality is a
separate, later phase's concern (task spec §17), not this one's.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="e2e: DATABASE_URL unset"
)

from app.services.ingestion_sources.repo_procedural import (  # noqa: E402
    LocalDirAgentsMdSource,
    LocalDirCIWorkflowSource,
    LocalDirRunbookSource,
)
from app.db.session import create_pool  # noqa: E402
from app.services.skill_ingestion import run_skill_ingestion  # noqa: E402


class _FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        h = abs(hash(text))
        return [((h >> (i % 40)) & 1) * 0.1 + 0.01 for i in range(1024)]


def _tag() -> str:
    return uuid.uuid4().hex[:8]


async def _cleanup(pool, name_like: str) -> None:
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"%{name_like}%")
    await pool.execute("DELETE FROM ingested_artifacts WHERE uri LIKE $1", f"%{name_like}%")


@pytest.mark.asyncio
async def test_agents_md_full_lifecycle_accept_idempotent_reingest_and_provenance(tmp_path):
    """The one full-lifecycle proof for this file's own family
    (AGENTS.md) -- accepted, then byte-identical re-ingestion is a no-op,
    matching test_skill_ingestion_e2e.py's own SKILL.md proof of the same
    shared-compiler contract, now shown for a different adapter."""
    tag = _tag()
    (tmp_path / "AGENTS.md").write_text(
        f"---\nname: agents-gold-{tag}\ndescription: How agents work in this repo, gold case {tag}\n---\n"
        "1. Read the plan before editing\n2. Run the tests before committing\n",
        encoding="utf-8",
    )
    pool = await create_pool(statement_cache_size=0)
    try:
        await _cleanup(pool, f"agents-gold-{tag}")
        adapter = LocalDirAgentsMdSource(tmp_path)
        embedder = _FakeEmbedder()

        result = await run_skill_ingestion(
            pool, adapter, embedder=embedder, client=None,
            created_by=f"gold_ingestion_sources_{tag}",
        )
        assert result["metrics"]["accepted"] == 1
        assert result["metrics"]["errors"] == 0

        row = await pool.fetchrow(
            "SELECT id, procedure_id, provenance, capability_statement, "
            "verification_state, domain_payload "
            "FROM procedures WHERE name = $1", f"agents-gold-{tag}",
        )
        assert row is not None
        assert row["provenance"] == "prior_library"
        assert row["capability_statement"] is None  # client=None -> abstained, never fabricated
        assert row["verification_state"] == "candidate"
        assert row["domain_payload"]["source"]["source_type"] == "agents_md"

        artifact = await pool.fetchrow(
            "SELECT content_hash, run_id, source_type FROM ingested_artifacts "
            "WHERE procedure_row_id = $1", row["id"],
        )
        assert artifact is not None
        assert artifact["source_type"] == "agents_md"
        assert str(artifact["run_id"]) == result["run_id"]

        # Byte-identical re-ingestion is a no-op -- idempotency where promised.
        result2 = await run_skill_ingestion(
            pool, adapter, embedder=embedder, client=None,
            created_by=f"gold_ingestion_sources_{tag}",
        )
        assert result2["metrics"]["unchanged"] == 1
        assert result2["metrics"]["accepted"] == 0
        count_after = await pool.fetchval(
            "SELECT count(*) FROM procedures WHERE name = $1", f"agents-gold-{tag}",
        )
        assert count_after == 1, "byte-identical re-ingestion must not create a duplicate row"
    finally:
        await _cleanup(pool, f"agents-gold-{tag}")
        await pool.close()


@pytest.mark.asyncio
async def test_runbook_accepted_with_real_provenance(tmp_path):
    tag = _tag()
    (tmp_path / "RUNBOOK.md").write_text(
        f"---\nname: restart-worker-gold-{tag}\ndescription: Restart the worker, gold case {tag}\n---\n"
        "1. Stop it: `sudo systemctl stop worker`\n2. Start it: `sudo systemctl start worker`\n",
        encoding="utf-8",
    )
    pool = await create_pool(statement_cache_size=0)
    try:
        await _cleanup(pool, f"restart-worker-gold-{tag}")
        adapter = LocalDirRunbookSource(tmp_path)
        result = await run_skill_ingestion(
            pool, adapter, embedder=_FakeEmbedder(), client=None,
            created_by=f"gold_ingestion_sources_{tag}",
        )
        assert result["metrics"]["accepted"] == 1

        row = await pool.fetchrow(
            "SELECT domain_payload FROM procedures WHERE name = $1",
            f"restart-worker-gold-{tag}",
        )
        assert row is not None
        assert row["domain_payload"]["source"]["source_type"] == "runbook"
    finally:
        await _cleanup(pool, f"restart-worker-gold-{tag}")
        await pool.close()


@pytest.mark.asyncio
async def test_ci_workflow_job_accepted_with_real_provenance(tmp_path):
    tag = _tag()
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf_dir.joinpath("ci.yml").write_text(
        f"""
name: gold-ci-{tag}
on: [push]
jobs:
  test-{tag}:
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Run tests
        run: pytest -q
""",
        encoding="utf-8",
    )
    pool = await create_pool(statement_cache_size=0)
    try:
        await _cleanup(pool, f"ci-{tag}")
        adapter = LocalDirCIWorkflowSource(tmp_path)
        result = await run_skill_ingestion(
            pool, adapter, embedder=_FakeEmbedder(), client=None,
            created_by=f"gold_ingestion_sources_{tag}",
        )
        assert result["metrics"]["accepted"] == 1

        row = await pool.fetchrow(
            "SELECT domain_payload FROM procedures WHERE name = $1", f"ci-test-{tag}",
        )
        assert row is not None
        assert row["domain_payload"]["source"]["source_type"] == "ci_workflow"
    finally:
        await _cleanup(pool, f"ci-{tag}")
        await pool.close()


@pytest.mark.asyncio
async def test_entity_scoped_ingestion_is_recorded_as_entity_not_silently_promoted_to_global(tmp_path):
    """The scope half of "private source data cannot become global data
    accidentally" for this admission path: passing `domain=` records the
    procedure as scope_type='entity' with that exact scope_entity_id, not
    'global'. Note (real, documented, not a bug): compile_skill_artifact
    has no `visibility` parameter of its own -- every procedure it writes
    is capture_procedure's visibility default ('public'); the privacy
    control this specific admission path actually offers is entity scoping
    (domain), not viewer-private visibility. This test proves the scoping
    contract that DOES exist here rather than asserting an owner-private
    guarantee this code path has never claimed."""
    tag = _tag()
    (tmp_path / "AGENTS.md").write_text(
        f"---\nname: scoped-gold-{tag}\ndescription: entity-scoped case {tag}\n---\n"
        "1. Do the scoped thing\n",
        encoding="utf-8",
    )
    pool = await create_pool(statement_cache_size=0)
    domain = f"repo-{tag}"
    try:
        await _cleanup(pool, f"scoped-gold-{tag}")
        adapter = LocalDirAgentsMdSource(tmp_path)
        result = await run_skill_ingestion(
            pool, adapter, embedder=_FakeEmbedder(), client=None, domain=domain,
            created_by=f"gold_ingestion_sources_{tag}",
        )
        assert result["metrics"]["accepted"] == 1

        row = await pool.fetchrow(
            "SELECT scope_type, scope_entity_id, visibility FROM procedures WHERE name = $1",
            f"scoped-gold-{tag}",
        )
        assert row is not None
        assert row["scope_type"] == "entity"
        assert row["scope_entity_id"] == domain
        assert row["visibility"] == "public", (
            "documented real behavior, not a gap this test invents: this admission path "
            "has no visibility parameter, every ingested procedure is capture_procedure's "
            "public default regardless of domain scoping"
        )
    finally:
        await _cleanup(pool, f"scoped-gold-{tag}")
        await pool.close()
