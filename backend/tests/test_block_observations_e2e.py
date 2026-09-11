"""
G4 residual (audit doc): "Observations per artifact_block / block-span
citation". Proves compile_skill_artifact now emits one real Observation
per artifact_block, each addressable back to its own block id/span --
additive to the existing single whole-document Observation, which stays
untouched (still cites only the first block via
_attach_observation_block_ref, exactly as before).

Skips (never fails) without DATABASE_URL.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from app.db.session import create_pool
from app.services.embeddings import EmbeddingMetadata
from app.services.ingestion_sources import SourceArtifact, compute_content_hash
from app.services.skill_ingestion import compile_skill_artifact

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- live-database block-observation test"
)

_MARK = "test-block-observations-e2e"

_SKILL_MD = """---
name: {name}
description: A multi-section test skill for block-observation citation.
---

## Overview

This section explains the general idea behind the skill.

## Steps

1. Do the first thing.
2. Do the second thing.
3. Do the third thing.

## Notes

Some closing remarks that form their own block.
"""


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.1] * 1024

    async def embed_one_with_metadata(self, text, input_type="document"):
        vector = await self.embed_one(text, input_type=input_type)
        return vector, EmbeddingMetadata(
            provider="fake", model_id=self.embedding_model_id(),
            dimension=1024, input_type=input_type, text_sha256="0" * 64,
        )

    def embedding_model_id(self) -> str:
        return "fake:test-embedder"


def test_compile_skill_artifact_emits_one_observation_per_block():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        tag = uuid.uuid4().hex[:8]
        name = f"{_MARK}-{tag}"
        content = _SKILL_MD.format(name=name)
        artifact = SourceArtifact(
            source_type="document", uri=f"file:///{_MARK}/{tag}/SKILL.md",
            content=content, content_hash=compute_content_hash(content),
            path=f"{tag}/SKILL.md",
        )
        outcome = None
        try:
            outcome = await compile_skill_artifact(
                pool, artifact, embedder=FakeEmbedder(), created_by="tester",
            )
            assert outcome.status == "captured", outcome.reason
            assert outcome.artifact_block_ids, "expected real artifact_blocks to be persisted"
            assert outcome.block_observation_ids, "G4 residual: expected one Observation per block"
            assert len(outcome.block_observation_ids) == len(outcome.artifact_block_ids)

            rows = await pool.fetch(
                "SELECT o.id, o.observation_type, o.properties, o.ingestion_context_id "
                "FROM observations o WHERE o.id = ANY($1::uuid[])",
                outcome.block_observation_ids,
            )
            assert len(rows) == len(outcome.block_observation_ids)
            block_ids_cited = set()
            for row in rows:
                assert row["observation_type"] == "document_block"
                assert row["ingestion_context_id"] is not None
                props = row["properties"]
                assert "artifact_block_id" in props
                assert "block_index" in props
                block_ids_cited.add(props["artifact_block_id"])
            # every cited block id is a real, distinct artifact_block --
            # no two Observations cite the same block, none are fabricated
            assert block_ids_cited == set(outcome.artifact_block_ids)

            # the pre-existing single whole-document Observation is
            # untouched by this addition -- still exactly one, still only
            # citing the first block (not replaced or duplicated).
            doc_obs = await pool.fetchrow(
                "SELECT properties FROM observations WHERE id = $1::uuid",
                uuid.UUID(outcome.observation_id),
            )
            assert doc_obs["properties"]["artifact_block_id"] == outcome.artifact_block_ids[0]
        finally:
            if outcome is not None and outcome.ingestion_context_id:
                await pool.execute(
                    "DELETE FROM observations WHERE ingestion_context_id = $1::uuid",
                    uuid.UUID(outcome.ingestion_context_id),
                )
                await pool.execute("DELETE FROM artifact_blocks WHERE artifact_id = $1::uuid",
                                   uuid.UUID(outcome.artifact_id))
                await pool.execute("DELETE FROM procedures WHERE id = $1::uuid",
                                   uuid.UUID(outcome.version_row_id))
                await pool.execute("DELETE FROM ingested_artifacts WHERE id = $1::uuid",
                                   uuid.UUID(outcome.artifact_id))
                await pool.execute("DELETE FROM ingestion_contexts WHERE id = $1::uuid",
                                   uuid.UUID(outcome.ingestion_context_id))
                await pool.execute("DELETE FROM sources WHERE id = $1::uuid",
                                   uuid.UUID(outcome.source_id))
            await pool.close()

    asyncio.run(_run())
