"""
Source registry -- one addressable row per distinct knowledge origin.

WHY THIS EXISTS
    `schema.md`'s `Source [V]` object has always been specified and never
    backed: `evidence.source_id` (migration 24) and `knowledge_nodes`
    provenance carried a bare handle with the note "-> Source ... have no
    tables yet". Every ingestion path recorded provenance ad hoc
    (`ingested_artifacts`, `claim_sources`, `evidence_refs` JSONB). Migration
    49 finally created `sources`; this module is its first writer.

    A Source is the ORIGIN's stable identity. Its `reliability_score` /
    `reliability_method` answer "how much do we trust THIS origin" and are
    kept deliberately SEPARATE from claim belief -- an unassessed source is
    "unknown", never "trusted" (schema.md: "Source reliability is
    represented separately from claim confidence").

HONEST SCOPE LIMITS
    - Identity dedup is `UNIQUE (source_type, locator, publisher)` (migration
      49). Postgres treats NULL as distinct in a UNIQUE index, so a Source
      registered with `publisher=None` will NOT dedup against a later
      identical call -- callers that want dedup must pass a stable
      publisher. The skill-md ingestion path always passes the repository.
    - `provenance_source` (the pg enum on this column) only permits
      `company_ingested | company_debate | prior_library`. The wider V0
      vocabulary values `public_generated` / `system_pending_review` belong
      on the DERIVED rows (procedures/observations), never on the origin --
      a vetted external document is `prior_library`.
    - No retraction path yet: the `t_invalid` tombstone column exists but
      nothing here closes a validity window. Add that with the first real
      source-retraction caller (same sequencing note migration 50 carries).
"""
from __future__ import annotations

from typing import Optional

import asyncpg

from app.services.access import TenantScope, tenant_transaction
from app.services.v0_gate import validate_provenance, validate_scope
from app.utils.ids import uuid7

# The three values the `provenance_source` pg enum actually permits. The V0
# gate's PROVENANCE_VALUES is wider (it also covers derived-row provenance);
# this column cannot store those, so we re-check against the real enum set
# here rather than letting asyncpg raise an opaque InvalidTextRepresentation.
SOURCE_PROVENANCE_VALUES = ("company_ingested", "company_debate", "prior_library")

# Named greppable writer stamp, same idiom as claim_evidence.CLAIM_EVIDENCE_WRITER_STAMP.
SOURCE_WRITER_STAMP = "sources.register_source@v1"


async def register_source(
    pool: asyncpg.Pool,
    *,
    source_type: str,
    locator: str,
    publisher: Optional[str] = None,
    title: Optional[str] = None,
    license: Optional[str] = None,
    discovered_via: Optional[str] = None,
    provenance: str = "prior_library",
    created_by: str,
    visibility: str = "public",
    owner_id: Optional[str] = None,
    scope_type: str = "global",
    scope_entity_id: Optional[str] = None,
    reliability_score: Optional[float] = None,
    reliability_method: Optional[str] = None,
) -> dict:
    """
    Register (or reuse) one Source row. Returns
    ``{"id": <uuid str>, "reused": <bool>}`` -- ``reused`` is True when an
    existing row matched the ``(source_type, locator, publisher)`` identity
    key and was refreshed rather than inserted.

    Scope + provenance are V0-gated here (the storage columns are nullable
    only so this layer owns the error message). ``provenance`` is
    additionally checked against the three values the ``provenance_source``
    pg enum permits -- see ``SOURCE_PROVENANCE_VALUES``.

    Write goes through ``tenant_transaction(pool, TenantScope.commons())``
    per the repo write-path convention: the tenant is bound as the first
    statement after BEGIN so no statement runs unscoped-by-setting.
    """
    validate_scope(scope_type, scope_entity_id)
    validate_provenance(provenance)
    if provenance not in SOURCE_PROVENANCE_VALUES:
        raise ValueError(
            f"sources.register_source: provenance {provenance!r} is not storable on "
            f"a Source row (provenance_source enum permits {SOURCE_PROVENANCE_VALUES}); "
            "derived-row provenance belongs on the derived object, not the origin"
        )
    if visibility not in ("public", "private"):
        raise ValueError(f"visibility must be 'public' or 'private', got {visibility!r}")

    new_id = uuid7()
    scope = TenantScope.commons()
    async with tenant_transaction(pool, scope) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO sources (
                id, source_type, locator, publisher, title, license,
                reliability_score, reliability_method, discovered_via,
                provenance, created_by, visibility, owner_id, tenant_id,
                scope_type, scope_entity_id
            ) VALUES (
                $1::uuid, $2::source_kind, $3, $4, $5, $6,
                $7, $8, $9,
                $10::provenance_source, $11, $12::visibility_level, $13, $14::uuid,
                $15, $16
            )
            ON CONFLICT (source_type, locator, publisher) DO UPDATE SET
                title = EXCLUDED.title,
                license = COALESCE(EXCLUDED.license, sources.license),
                reliability_score = COALESCE(EXCLUDED.reliability_score, sources.reliability_score),
                reliability_method = COALESCE(EXCLUDED.reliability_method, sources.reliability_method),
                discovered_via = COALESCE(EXCLUDED.discovered_via, sources.discovered_via)
            RETURNING id, (xmax = 0) AS inserted
            """,
            new_id, source_type, locator, publisher, title, license,
            reliability_score, reliability_method, discovered_via,
            provenance, created_by, visibility, owner_id, scope.tenant_id,
            scope_type, scope_entity_id,
        )
    return {"id": str(row["id"]), "reused": not row["inserted"]}


async def get_source(pool: asyncpg.Pool, source_id: str) -> Optional[dict]:
    """One Source row by id as a plain dict, or None. Live rows only
    (``t_invalid IS NULL``) -- a tombstoned origin is not a valid origin."""
    row = await pool.fetchrow(
        "SELECT * FROM sources WHERE id = $1::uuid AND t_invalid IS NULL",
        source_id,
    )
    return dict(row) if row else None
