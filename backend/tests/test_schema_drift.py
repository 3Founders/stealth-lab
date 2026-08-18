"""
Real, live-database drift check (ticket 17, memory-substrate map). Targets
exactly the two known drifts named there, and no others -- this is
deliberately narrow, not a generic "add more tests" gesture.

Per ticket 17's own addendum: checking code against the DDL *files* is not
enough, because a database can have a column/enum value that was added by
hand and never captured in any committed migration -- exactly what happened
to `embedding_joint`. So these tests query the live database's real
information_schema/pg_enum, not the SQL files, and require a real
DATABASE_URL to run at all. They skip (not fail) when one isn't configured,
since a live DB isn't available in every environment this test suite runs
in -- but they must run for real in CI against a real database, or they
are not doing their job.

Run manually against the local test database with:
    DATABASE_URL=postgresql://postgres:stealthlab@localhost:5432/stealthlab_local \
        python3 -m pytest tests/test_schema_drift.py -v
"""
import asyncio
import os

import asyncpg
import pytest

from app.models.ontology import ProvenanceSource
from app.services.retrieval import VALID_EMBEDDING_COLUMNS

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires a real DATABASE_URL -- this check is only meaningful against a live database",
)


async def _real_columns(conn: asyncpg.Connection, table: str) -> set[str]:
    rows = await conn.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = $1", table
    )
    return {r["column_name"] for r in rows}


async def _real_enum_values(conn: asyncpg.Connection, type_name: str) -> set[str]:
    rows = await conn.fetch(
        "SELECT e.enumlabel FROM pg_enum e "
        "JOIN pg_type t ON e.enumtypid = t.oid WHERE t.typname = $1",
        type_name,
    )
    return {r["enumlabel"] for r in rows}


def test_embedding_column_literals_are_real_columns():
    """
    The real drift this test targets: retrieval.py's VALID_EMBEDDING_COLUMNS
    accepted 'embedding_joint' as a valid value, but no DDL file in this
    repo creates that column -- it passed validation and failed only at
    query time, and only for whoever happened to hit that code path.
    """
    async def _check():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            task_node_columns = await _real_columns(conn, "task_nodes")
            knowledge_node_columns = await _real_columns(conn, "knowledge_nodes")
            # embedding_joint is deliberately task_nodes-only (retrieval.py's
            # own comment: "knowledge_nodes has no alt column") -- so each
            # accepted literal only needs to be real on AT LEAST ONE of the
            # two tables HybridRetriever actually queries, not both.
            all_real_columns = task_node_columns | knowledge_node_columns
            for col in VALID_EMBEDDING_COLUMNS:
                assert col in all_real_columns, (
                    f"retrieval.py accepts embedding_column={col!r}, but no real "
                    f"column named {col!r} exists on task_nodes or knowledge_nodes "
                    f"in the live database. This is the embedding_joint ghost-column "
                    f"drift -- fix by either adding the column via a real migration "
                    f"file, or removing it from VALID_EMBEDDING_COLUMNS if it's no "
                    f"longer meant to be valid."
                )
        finally:
            await conn.close()

    asyncio.run(_check())


def test_provenance_source_matches_real_db_enum():
    """
    The real drift this test targets: ProvenanceSource (the Python model)
    omitted 'public_generated', even though the real DB enum has it and
    knowledge_update.py's apply_generated() writes it on every call --
    hydrating those rows via from_row() raised a real ValidationError.

    Checked BOTH directions deliberately: Python missing a real DB value
    (the actual historical bug) and DB missing a Python-declared value
    (a different, real drift the same mechanism would also catch).
    """
    async def _check():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            real_values = await _real_enum_values(conn, "provenance_source")
            python_values = set(ProvenanceSource.__args__)

            missing_from_python = real_values - python_values
            assert not missing_from_python, (
                f"The real DB enum provenance_source has value(s) "
                f"{missing_from_python} that ProvenanceSource "
                f"(app/models/ontology.py) does not declare -- hydrating a row "
                f"with one of these values via from_row() will raise."
            )

            missing_from_db = python_values - real_values
            assert not missing_from_db, (
                f"ProvenanceSource declares value(s) {missing_from_db} that the "
                f"real DB enum provenance_source does not have -- writing one of "
                f"these will fail at the database, not at validation time."
            )
        finally:
            await conn.close()

    asyncio.run(_check())
