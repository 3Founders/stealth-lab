"""
Real, live-database proving test for Phase 19 (V1 spec): personal
procedure -> Publish -> global candidate
(`app/services/publish.py::publish_local_procedure`). Same
DATABASE_URL-gated pattern as test_procedures_e2e.py -- skips (not
fails) without a real DATABASE_URL.

Builds a real local procedure via a real `LocalProcedureStore` (temp
dir, real SQLite file), containing a fake-but-pattern-matching secret
(an AWS access key shape -- `KNOWN_TOKEN_PATTERNS["aws_access_key"]` in
trace_redaction.py) inside one of its steps, publishes it, and asserts
against the REAL Postgres row that:
  - the secret-shaped text is redacted (not merely "different"),
  - provenance / created_by / owner_id / domain_payload's publish-audit
    fields are exactly right,
  - the row starts candidate/fresh/active with EMPTY verification_stats
    (not inherited from the local row's own accumulated track record),
  - the row is reachable through the existing real `get_procedure()`.
"""
import asyncio
import os
import tempfile

import pytest

from app.db.session import create_pool
from app.local_agent.local_store import LocalProcedureStore
from app.services.procedures import get_procedure
from app.services.publish import PUBLISHED_PROVENANCE, publish_local_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test",
)

FAKE_AWS_KEY = "AKIAABCDEFGHIJKLMNOP"  # matches KNOWN_TOKEN_PATTERNS["aws_access_key"]


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{name_prefix}%")


def test_publish_local_procedure_redacts_scrubs_and_starts_fresh_candidate():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        name_prefix = "publish-test-proc"
        try:
            await _cleanup(pool, name_prefix)

            with tempfile.TemporaryDirectory() as tmp_dir:
                store = LocalProcedureStore(repo_root=tmp_dir)
                local_result = store.capture_local_procedure(
                    name=f"{name_prefix}-1",
                    goal="deploy the service using the saved credentials",
                    steps=[
                        {"action": "authenticate", "detail": f"use key {FAKE_AWS_KEY} to sign in"},
                        {"action": "deploy"},
                    ],
                    provenance="system_pending_review",
                    scope_type="user",
                    scope_entity_id="local-workspace-1",
                )
                local_row_id = local_result["id"]

                # Give the local row a real accumulated local track record --
                # this MUST NOT be carried over onto the published global row.
                store.record_local_execution_outcome(
                    row_id=local_row_id, success=True, context_key="ctx-a",
                )
                store.record_local_execution_outcome(
                    row_id=local_row_id, success=True, context_key="ctx-b",
                )

                local_procedure = store.get_local_procedure(local_row_id)
                assert local_procedure is not None
                assert local_procedure["verification_stats"]["attempts"] == 2, (
                    "fixture sanity: the local row must carry a real local track "
                    "record for the no-carry-over assertion below to mean anything"
                )
                assert FAKE_AWS_KEY in local_procedure["steps"][0]["detail"], (
                    "fixture sanity: the raw local row must actually contain the "
                    "unredacted secret before publish"
                )

                published = await publish_local_procedure(
                    pool,
                    local_procedure=local_procedure,
                    published_by="tester@example.com",
                    scope_type="global",
                )

            row = await get_procedure(pool, published["id"])
            assert row is not None, "published row must be reachable via the real get_procedure()"

            # Redaction actually ran.
            assert FAKE_AWS_KEY not in row["steps"][0]["detail"]
            assert "[REDACTED:aws_access_key]" in row["steps"][0]["detail"]
            assert FAKE_AWS_KEY not in row["name"]
            assert FAKE_AWS_KEY not in row["goal"]

            # Provenance / authorship preserved and explicit.
            assert row["provenance"] == PUBLISHED_PROVENANCE
            assert row["created_by"] == "tester@example.com"
            assert row["owner_id"] == "tester@example.com"

            # Publish-event audit trail lives in domain_payload.
            assert row["domain_payload"]["published_from_local_procedure_id"] == local_row_id
            assert row["domain_payload"]["published_by"] == "tester@example.com"
            assert row["domain_payload"]["published_at"]

            # Fresh candidate state -- NOT inherited from the local row's
            # own real 2-success track record.
            assert row["verification_state"] == "candidate"
            assert row["staleness"] == "fresh"
            assert row["availability"] == "active"
            assert row["verification_stats"]["attempts"] == 0
            assert row["verification_stats"]["successes"] == 0
        finally:
            await _cleanup(pool, name_prefix)
            await pool.close()

    asyncio.run(_run())


def test_publish_local_procedure_requires_explicit_publisher():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                store = LocalProcedureStore(repo_root=tmp_dir)
                local_result = store.capture_local_procedure(
                    name="publish-test-anon", goal="g",
                    provenance="system_pending_review", scope_type="user",
                    scope_entity_id="local-workspace-1",
                )
                local_procedure = store.get_local_procedure(local_result["id"])

                with pytest.raises(ValueError):
                    await publish_local_procedure(
                        pool, local_procedure=local_procedure, published_by="",
                    )
        finally:
            await pool.close()

    asyncio.run(_run())
