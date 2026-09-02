"""
Live proving test for `app/local_agent/repo_docs_bootstrap.py` (core-a):
repo procedural docs (SKILL.md) bootstrap into the PRIVATE
LocalProcedureStore, never the shared Postgres `procedures` table.

Skips itself (never fails) when DATABASE_URL is unset -- same
`*_e2e.py`-style module-level skipif every other live proof in this repo
uses (CLAUDE.md commands section) -- since the negative-proof half of this
test needs a real asyncpg connection to confirm absence from Postgres.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from pathlib import Path

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set -- live proof needs a real Postgres connection",
)

SKILL_MD = """---
name: rotate-api-credentials
description: Rotate a leaked or expiring API credential safely without downtime.
---

Use when a credential needs rotating: leaked in a log, expiring soon, or a
scheduled security rotation.

1. Generate a new credential in the provider console, keeping the old one active.
2. Deploy the new credential to every consuming service's config.
3. Verify each service picks up the new credential (health check / smoke test).
4. Revoke the old credential only after every consumer is confirmed healthy.
5. Record the rotation in the incident/audit log.
"""


def test_repo_docs_bootstrap_lands_privately_not_in_postgres():
    asyncio.run(_run_proof())


async def _run_proof():
    import asyncpg

    from app.local_agent.local_store import LocalProcedureStore
    from app.local_agent.repo_docs_bootstrap import bootstrap_repo_docs

    with tempfile.TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        skill_dir = repo_root / "skills" / "rotate-api-credentials"
        skill_dir.mkdir(parents=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(SKILL_MD, encoding="utf-8")

        workspace = repo_root / "workspace"
        workspace.mkdir()
        store = LocalProcedureStore(str(workspace))

        summary = bootstrap_repo_docs(store, str(repo_root))

        # --- positive proof: a grounded candidate landed in the LOCAL store ---
        assert summary["candidates_formed"] >= 1
        assert summary["captured"] >= 1
        assert summary["skipped_unparseable"] == 0

        rows = store.list_local_procedures()
        matches = [r for r in rows if "rotate" in (r["goal"] or "").lower()
                   or "rotate" in (r["name"] or "").lower()]
        assert matches, f"expected a rotate-api-credentials row, got {rows}"
        row = matches[0]

        # provenance: source path + source type recorded, mirroring
        # git_history_bootstrap.py's evidence-ref shape.
        assert row["provenance"] == "prior_library"
        assert row["scope_type"] == "repository"
        scope = row["scope"] or {}
        repo_docs_scope = scope.get("repo_docs") or {}
        assert repo_docs_scope.get("source_type") == "skill_md"
        assert repo_docs_scope.get("path", "").endswith("SKILL.md")
        evidence_refs = row["evidence_refs"] or []
        assert evidence_refs, "expected at least one evidence_ref"
        assert evidence_refs[0]["source_type"] == "skill_md"
        assert evidence_refs[0]["source_location"].endswith("SKILL.md")
        assert len(row["steps"] or []) == 5

        # retrievable via search_local_procedures
        found = store.search_local_procedures("rotate credential")
        assert any(r["id"] == row["id"] for r in found), (
            f"row {row['id']} not retrievable via search_local_procedures: {found}"
        )

        # --- negative proof: real asyncpg query against Postgres `procedures` ---
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            marker = f"rotate-api-credentials-{uuid.uuid4().hex[:8]}"
            # sanity: the marker table/query itself works against a live DB
            version = await conn.fetchval("SELECT version()")
            assert version

            pg_matches = await conn.fetch(
                "SELECT procedure_id, name, goal FROM procedures "
                "WHERE name ILIKE $1 OR goal ILIKE $1",
                "%rotate-api-credentials%",
            )
            assert not pg_matches, (
                f"repo-doc candidate leaked into shared Postgres procedures: {pg_matches}"
            )

            # Confirm no row from THIS run's content hash landed globally either
            # (belt-and-suspenders: match on the distinctive goal text).
            pg_goal_matches = await conn.fetch(
                "SELECT procedure_id FROM procedures WHERE goal ILIKE $1",
                "%Rotate a leaked or expiring API credential safely%",
            )
            assert not pg_goal_matches, (
                f"repo-doc candidate's goal text leaked into Postgres: {pg_goal_matches}"
            )
        finally:
            await conn.close()
