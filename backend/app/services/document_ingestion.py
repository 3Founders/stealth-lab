"""
Orchestrates the DOCUMENT ingestion path end-to-end (Prompt 2):

    source -> DocumentSourceAdapter -> CanonicalDocument -> [this module]
        -> ingested_artifacts row (existing table, migration 32)
        -> artifact_blocks rows (existing table, migration 69, via the
           existing structural splitter `artifact_blocks.normalize_markdown`)
        -> sources row (existing table, migration 64, via `sources.register_source`)

Deliberately reuses three existing writers instead of adding a second
store for any of them:
  - `skill_ingestion._write_artifact_row`'s generic (non-"skill_package")
    branch, by constructing the `SourceArtifact` shape it already expects
    -- this is the SAME `ingested_artifacts` table every ingestion path
    writes, keyed by the SAME `UNIQUE (source_type, uri, content_hash)`.
  - `artifact_blocks.normalize_markdown` + `persist_artifact_blocks` for
    structure -- the same block model claims/observations already cite.
  - `sources.register_source` for the shared Source-origin row.

IDEMPOTENCY / VERSIONING
    Mirrors `skill_ingestion.compile_skill_artifact`'s own staleness
    precheck: same `(source_type, uri, content_hash, extractor_version)`
    seen before -> no-op that only bumps `last_seen` (`status="unchanged"`).
    A changed `content_hash` for the same `(source_type, uri)` falls
    through to a fresh INSERT -- a NEW `ingested_artifacts` row/version,
    never an overwrite of the old one's provenance.

QUARANTINE
    This module does not run the LLM admission gate `skill_ingestion`
    uses (that gate is scoped to skill-package trust screening). A
    `CanonicalDocument` that carries a `quarantine:*` extraction warning
    (malformed/partially-parseable source) is still written -- content is
    never silently dropped -- but the outcome's `status="quarantined"`
    tells the caller to route it for review before anything downstream
    treats it as trusted.

KNOWN GAP (disclosed, not silently punted)
    `ingested_artifacts.parsed_metadata` exists and is populated for
    `skill_package` rows, but this module's generic INSERT branch does not
    yet write `CanonicalDocument.metadata` / `.structured` /
    `.extraction_warnings` into it -- those survive in the returned
    `CanonicalDocument` and in `canonical_markdown`/blocks (nothing is
    lost), but are not yet queryable off the `ingested_artifacts` row
    itself. Wiring that in is a small additive change to
    `_write_artifact_row`'s generic branch, left for a follow-up so this
    task does not also touch the SKILL.md write path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import asyncpg

from app.services.artifact_blocks import normalize_markdown, persist_artifact_blocks
from app.services.ingestion_sources.base import SourceArtifact
from app.services.ingestion_sources.canonical import CanonicalDocument
from app.services.skill_ingestion import _write_artifact_row
from app.services.sources import register_source

EXTRACTOR_VERSION = "document_ingestion@v1"

# `CanonicalDocument.source_type` (adapter identity, free text on
# ingested_artifacts) -> `sources.source_kind` (the fixed origin-kind
# enum). Any unlisted adapter source_type registers as "document", the
# safe default for a text-shaped origin.
_SOURCE_KIND_BY_ADAPTER: dict[str, str] = {
    "document_markdown": "document",
    "document_skill_markdown": "document",
    "document_html": "document",
    "document_pdf": "document",
    "document_docx": "document",
    "document_github_file": "repository",
    "document_github_repo_docs": "repository",
    "document_api": "api",
}


@dataclass(frozen=True)
class DocumentIngestOutcome:
    status: str  # "ingested" | "unchanged" | "quarantined"
    artifact_id: Optional[str]
    source_ref: Optional[str]
    block_ids: tuple[str, ...]
    warnings: tuple[str, ...]


async def _find_existing(pool: asyncpg.Pool, doc: CanonicalDocument) -> Optional[dict]:
    rows = await pool.fetch(
        "SELECT id FROM ingested_artifacts "
        "WHERE source_type = $1 AND uri = $2 AND content_hash = $3 "
        "AND extractor_version = $4 AND t_invalid IS NULL",
        doc.source_type, doc.source_uri, doc.content_hash, EXTRACTOR_VERSION,
    )
    return dict(rows[0]) if rows else None


async def ingest_canonical_document(
    pool: asyncpg.Pool,
    doc: CanonicalDocument,
    *,
    created_by: str,
    owner_id: Optional[str] = None,
    run_id: Optional[str] = None,
    register_as_source: bool = True,
) -> DocumentIngestOutcome:
    """Write one `CanonicalDocument` through the existing artifact/block
    substrate. Safe to call twice with byte-identical input (idempotent);
    a changed `content_hash` for the same `(source_type, source_uri)`
    creates a new version rather than overwriting the old row."""
    warnings = tuple(f"{w.code}: {w.message}" for w in doc.extraction_warnings)

    existing = await _find_existing(pool, doc)
    if existing is not None:
        artifact_id = str(existing["id"])
        await pool.execute(
            "UPDATE ingested_artifacts SET last_seen = now() WHERE id = $1::uuid", artifact_id,
        )
        return DocumentIngestOutcome(
            status="unchanged", artifact_id=artifact_id, source_ref=None,
            block_ids=(), warnings=warnings,
        )

    source_ref: Optional[str] = None
    if register_as_source:
        result = await register_source(
            pool,
            source_type=_SOURCE_KIND_BY_ADAPTER.get(doc.source_type, "document"),
            locator=doc.source_uri,
            publisher=doc.repository or doc.author,
            title=doc.title,
            license=doc.license,
            created_by=created_by,
            visibility=doc.visibility if doc.visibility in ("public", "private") else "public",
            owner_id=owner_id,
        )
        source_ref = result["id"]

    artifact = SourceArtifact(
        source_type=doc.source_type,
        uri=doc.source_uri,
        content=doc.canonical_markdown,
        content_hash=doc.content_hash,
        repository=doc.repository,
        path=doc.path,
        commit=doc.version,
        discovered_at=doc.fetched_at,
        source_id=doc.source_id,
        license_metadata={"license": doc.license} if doc.license else {},
    )
    artifact_id = await _write_artifact_row(
        pool, artifact,
        run_id=run_id, procedure_id=None, procedure_row_id=None,
        extractor_version=EXTRACTOR_VERSION, owner_id=owner_id,
        admission=None, source_ref=source_ref,
    )

    blocks = normalize_markdown(doc.canonical_markdown)
    block_ids = await persist_artifact_blocks(
        pool,
        artifact_id=artifact_id,
        artifact_content_hash=doc.content_hash,
        blocks=blocks,
        created_by=created_by,
        owner_id=owner_id,
        visibility=doc.visibility if doc.visibility in ("public", "private") else "public",
    )

    status = "quarantined" if doc.quarantined() else "ingested"
    return DocumentIngestOutcome(
        status=status, artifact_id=artifact_id, source_ref=source_ref,
        block_ids=tuple(block_ids), warnings=warnings,
    )
