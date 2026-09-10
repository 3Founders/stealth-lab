"""
T2 -- per-migration proving tests for the V4-hardening ingestion+knowledge
migrations 50..55, run against a real Postgres.

For each file the spec's T2 asks for: fresh/representative apply, idempotent
re-run, constraint verification, and a rollback statement. Concretely here:

  * IDEMPOTENT RE-RUN -- re-execute the exact `db/5N_*.sql` text against a
    DB that already has it. Must not raise (proves the `IF NOT EXISTS` /
    guarded-DO discipline for real, not just by eye).
  * REPRESENTATIVE ROW + CONSTRAINTS -- insert one realistic row into each
    new table and assert the identity unique + a named CHECK actually
    rejects bad input. Proves the constraints are enforced, not just
    declared.
  * ADDITIVE-ONLY (rollback posture) -- assert each 5N file contains no
    statement that destroys pre-existing data (`DROP TABLE` of a table it
    did not create, `DELETE`, `TRUNCATE`, `ALTER ... DROP COLUMN`). The
    one allowed exception is migration 53's deliberate, documented
    widening of `procedure_implementations` (drop a UNIQUE constraint /
    a NOT NULL) -- an additive relaxation, no row loss. Rollback is
    therefore "drop the new objects"; it is intentionally NOT automated
    (fresh-start rule 1).
  * SCHEMA DRIFT -- `test_schema_drift.py` is the repo's own live
    introspection check; run it separately against the same DB.

Skips (never fails) without DATABASE_URL. Run:
  python scripts/dbtarget.py local -- python -m pytest tests/test_migrations_50_55_t2_e2e.py -q
"""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from pathlib import Path

import pytest

from app.db.session import create_pool

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- live-database migration test"
)

DB_DIR = Path(__file__).resolve().parents[1] / "db"
FILES = {
    50: "50_sources.sql",
    51: "51_ingestion_contexts.sql",
    52: "52_procedure_claim_refs.sql",
    53: "53_procedure_implementation_relation.sql",
    54: "54_screening_decisions.sql",
    55: "55_artifact_blocks.sql",
}


def _sql(n: int) -> str:
    return (DB_DIR / FILES[n]).read_text(encoding="utf-8")


# ------------------------------------------------------------------ additive-only

_DESTRUCTIVE = re.compile(
    r"\b(DROP\s+TABLE|TRUNCATE|DELETE\s+FROM|ALTER\s+TABLE\s+\w+\s+DROP\s+COLUMN)\b",
    re.IGNORECASE,
)
# migration 53 deliberately relaxes procedure_implementations (created by
# migration 39): drop the 2-col UNIQUE, drop a NOT NULL. Additive relaxation.
_ALLOWED_53 = re.compile(
    r"DROP\s+CONSTRAINT\s+procedure_implementations_procedure_id_implementation_id_key"
    r"|ALTER\s+COLUMN\s+resource_path\s+DROP\s+NOT\s+NULL",
    re.IGNORECASE,
)


def test_all_six_migrations_are_additive_only():
    problems = {}
    for n, name in FILES.items():
        body = _sql(n)
        # strip -- line comments so a destructive keyword in prose doesn't trip it
        code = "\n".join(line.split("--", 1)[0] for line in body.splitlines())
        hits = [h.group(0) for h in _DESTRUCTIVE.finditer(code)]
        if n == 53:
            hits = [h for h in hits if not _ALLOWED_53.search(h)]
            # the guarded DROP CONSTRAINT / DROP NOT NULL still shows as an
            # ALTER ... but our regex targets DROP COLUMN specifically, so
            # only genuinely destructive statements remain here
        assert not hits, f"{name}: destructive statement(s): {hits}"
        _ = problems  # keep name for clarity


def test_each_migration_file_re_runs_cleanly():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            for n in FILES:
                body = _sql(n)
                # migration files may contain multiple statements; asyncpg
                # executes a script fine. A guarded/idempotent file must be
                # a no-op the second time.
                async with pool.acquire() as conn:
                    await conn.execute(body)
        finally:
            await pool.close()

    asyncio.run(_run())


# ------------------------------------------------- representative rows + CHECKs

def test_sources_identity_unique_and_reliability_check():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        marker = f"t2://sources/{uuid.uuid4()}"
        try:
            await pool.execute(
                "INSERT INTO sources (id, source_type, locator, publisher, provenance, "
                "created_by, visibility, scope_type) "
                "VALUES (gen_random_uuid(), 'document', $1, 'acme', 'prior_library', "
                "'t2', 'public', 'global')", marker,
            )
            # identity = (source_type, locator, publisher) -- a second insert
            # with the same triple must violate the UNIQUE.
            with pytest.raises(Exception) as exc:
                await pool.execute(
                    "INSERT INTO sources (id, source_type, locator, publisher, provenance, "
                    "created_by, visibility, scope_type) "
                    "VALUES (gen_random_uuid(), 'document', $1, 'acme', 'prior_library', "
                    "'t2', 'public', 'global')", marker,
                )
            assert "unique" in str(exc.value).lower() or "duplicate" in str(exc.value).lower()
            # reliability_score CHECK [0,1]
            with pytest.raises(Exception):
                await pool.execute(
                    "INSERT INTO sources (id, source_type, locator, provenance, created_by, "
                    "visibility, scope_type, reliability_score) "
                    "VALUES (gen_random_uuid(), 'document', $1, 'prior_library', 't2', "
                    "'public', 'global', 9.0)", marker + "/bad",
                )
        finally:
            await pool.execute("DELETE FROM sources WHERE locator LIKE $1", marker + "%")
            await pool.close()

    asyncio.run(_run())


def test_ingestion_contexts_scope_and_status_checks():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        marker = f"t2:ctx:{uuid.uuid4()}"
        try:
            await pool.execute(
                "INSERT INTO ingestion_contexts (id, source_type, actor_id, scope_type, "
                "scope_entity_id, extractor_id, extractor_version, source_uri) "
                "VALUES (gen_random_uuid(), 'trace', 't2', 'session', 's1', 'x', 'v1', $1)",
                marker,
            )
            # non-global scope with no entity_id -> ingestion_contexts_scope_entity_chk
            with pytest.raises(Exception):
                await pool.execute(
                    "INSERT INTO ingestion_contexts (id, source_type, actor_id, scope_type, "
                    "extractor_id, extractor_version, source_uri) "
                    "VALUES (gen_random_uuid(), 'trace', 't2', 'project', 'x', 'v1', $1)",
                    marker + "/bad",
                )
            # bad status -> the status CHECK
            with pytest.raises(Exception):
                await pool.execute(
                    "INSERT INTO ingestion_contexts (id, source_type, actor_id, scope_type, "
                    "extractor_id, extractor_version, source_uri, status) "
                    "VALUES (gen_random_uuid(), 'trace', 't2', 'global', 'x', 'v1', $1, 'weird')",
                    marker + "/bad2",
                )
        finally:
            await pool.execute("DELETE FROM ingestion_contexts WHERE source_uri LIKE $1", marker + "%")
            await pool.close()

    asyncio.run(_run())


def test_procedure_claim_refs_role_vocab_and_identity():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        pid, cid = uuid.uuid4(), uuid.uuid4()
        try:
            await pool.execute(
                "INSERT INTO procedure_claim_refs (id, procedure_id, procedure_version, "
                "claim_id, role, ref_origin, created_by) "
                "VALUES (gen_random_uuid(), $1, 1, $2, 'PRECONDITION', 'derived', 't2')",
                pid, cid,
            )
            # same (proc, version, claim, role) -> UNIQUE violation
            with pytest.raises(Exception):
                await pool.execute(
                    "INSERT INTO procedure_claim_refs (id, procedure_id, procedure_version, "
                    "claim_id, role, ref_origin, created_by) "
                    "VALUES (gen_random_uuid(), $1, 1, $2, 'PRECONDITION', 'derived', 't2')",
                    pid, cid,
                )
            # bad role -> the role CHECK
            with pytest.raises(Exception):
                await pool.execute(
                    "INSERT INTO procedure_claim_refs (id, procedure_id, procedure_version, "
                    "claim_id, role, ref_origin, created_by) "
                    "VALUES (gen_random_uuid(), $1, 1, $2, 'NONSENSE', 'derived', 't2')",
                    pid, cid,
                )
        finally:
            await pool.execute("DELETE FROM procedure_claim_refs WHERE procedure_id = $1", pid)
            await pool.close()

    asyncio.run(_run())


def test_procedure_implementations_generalized_columns_and_role_check():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        # implementations has a real FK target; make a throwaway impl row.
        impl_id = uuid.uuid4()
        pid = uuid.uuid4()
        try:
            await pool.execute(
                "INSERT INTO implementations (id, name, kind, provider, version, created_by, "
                "visibility) VALUES ($1, $2, 'tool', 't2', 1, 't2', 'public')",
                impl_id, f"t2-impl-{impl_id}",
            )
            await pool.execute(
                "INSERT INTO procedure_implementations (id, procedure_id, implementation_id, "
                "role, status, created_by) "
                "VALUES (gen_random_uuid(), $1, $2, 'supporting', 'active', 't2')",
                pid, impl_id,
            )
            # migration 53 columns exist and take JSON
            await pool.execute(
                "UPDATE procedure_implementations SET applicability = $2::jsonb, "
                "evidence_refs = $3::jsonb WHERE procedure_id = $1",
                pid, '{"os": "linux"}', '["ev-1"]',
            )
            # bad role -> the role CHECK
            with pytest.raises(Exception):
                await pool.execute(
                    "INSERT INTO procedure_implementations (id, procedure_id, implementation_id, "
                    "role, created_by) VALUES (gen_random_uuid(), $1, $2, 'sidekick', 't2')",
                    uuid.uuid4(), impl_id,
                )
        finally:
            await pool.execute("DELETE FROM procedure_implementations WHERE procedure_id = $1", pid)
            await pool.execute("DELETE FROM implementations WHERE id = $1", impl_id)
            await pool.close()

    asyncio.run(_run())


def test_screening_decisions_decision_and_detector_checks():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        marker = f"t2://scr/{uuid.uuid4()}"
        try:
            await pool.execute(
                "INSERT INTO screening_decisions (id, decision, check_type, detector, "
                "detector_version, artifact_uri, created_by, visibility) "
                "VALUES (gen_random_uuid(), 'ALLOW', 'prompt_injection', 'd', 'v1', $1, "
                "'t2', 'public')", marker,
            )
            with pytest.raises(Exception):
                await pool.execute(
                    "INSERT INTO screening_decisions (id, decision, check_type, detector, "
                    "detector_version, artifact_uri, created_by, visibility) "
                    "VALUES (gen_random_uuid(), 'MAYBE', 'prompt_injection', 'd', 'v1', $1, "
                    "'t2', 'public')", marker + "/bad",
                )
        finally:
            await pool.execute("DELETE FROM screening_decisions WHERE artifact_uri LIKE $1", marker + "%")
            await pool.close()

    asyncio.run(_run())


def test_artifact_blocks_offset_checks_and_identity():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        art_id = uuid.uuid4()
        h = "deadbeef" * 8
        try:
            await pool.execute(
                "INSERT INTO artifact_blocks (id, artifact_id, artifact_content_hash, "
                "block_index, block_type, depth, text, source_start, source_end, created_by, "
                "visibility) VALUES (gen_random_uuid(), $1, $2, 0, 'paragraph', 0, 'hi', "
                "0, 2, 't2', 'public')", art_id, h,
            )
            # same (artifact_id, hash, block_index) -> UNIQUE
            with pytest.raises(Exception):
                await pool.execute(
                    "INSERT INTO artifact_blocks (id, artifact_id, artifact_content_hash, "
                    "block_index, block_type, depth, text, source_start, source_end, created_by, "
                    "visibility) VALUES (gen_random_uuid(), $1, $2, 0, 'paragraph', 0, 'x', "
                    "0, 1, 't2', 'public')", art_id, h,
                )
            # source_end < source_start -> the CHECK
            with pytest.raises(Exception):
                await pool.execute(
                    "INSERT INTO artifact_blocks (id, artifact_id, artifact_content_hash, "
                    "block_index, block_type, depth, text, source_start, source_end, created_by, "
                    "visibility) VALUES (gen_random_uuid(), $1, $2, 1, 'paragraph', 0, 'x', "
                    "10, 3, 't2', 'public')", art_id, h,
                )
        finally:
            await pool.execute("DELETE FROM artifact_blocks WHERE artifact_id = $1", art_id)
            await pool.close()

    asyncio.run(_run())
