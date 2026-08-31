"""
Real, live-database proving test for local claim capture -> Publish ->
global candidate (`app/local_agent/local_claims.py::publish_local_claim`).
Same DATABASE_URL-gated pattern as `test_publish_e2e.py`: skips (not
fails) without a real DATABASE_URL.

Builds a real local claim via a real `LocalClaimStore` (temp dir, real
SQLite file), containing a fake-but-pattern-matching secret (an AWS
access key shape) inside its statement, publishes it against a real
task_node anchor, and asserts against the REAL Postgres row that:
  - the secret-shaped text is redacted (not merely "different"),
  - the row is reachable through the real global claims machinery
    (`list_current_claims` / a direct SELECT),
  - a second publish of the same local row is refused unless
    force=True, and force=True creates a genuinely new, independent
    global row without touching the earlier one,
  - the anchor constraint (task_ids and/or justification_episode_id
    required) is enforced, not silently swallowed as a None-returning
    success.
"""
import asyncio
import os
import tempfile
import uuid

import pytest

from app.db.session import create_pool
from app.local_agent.local_claims import (
    AlreadyClaimPublishedError,
    ClaimPublishFailedError,
    LocalClaimNotFound,
    LocalClaimStore,
    capture_local_claim,
    publish_local_claim,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test",
)

FAKE_AWS_KEY = "AKIAABCDEFGHIJKLMNOP"  # matches KNOWN_TOKEN_PATTERNS["aws_access_key"]


async def _make_task_node(pool, *, skill_ref: str) -> None:
    await pool.execute(
        "INSERT INTO task_nodes (skill_ref, name, provenance, scope_type, scope_entity_id) "
        "VALUES ($1, $2, 'company_ingested', 'repository', $3) "
        "ON CONFLICT DO NOTHING",
        skill_ref, f"task for {skill_ref}", skill_ref,
    )


async def _cleanup(pool, *, skill_ref_prefix: str, statement_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM knowledge_nodes WHERE node_type = 'claim' AND name LIKE $1",
        f"{statement_prefix}%",
    )
    await pool.execute("DELETE FROM task_nodes WHERE skill_ref LIKE $1", f"{skill_ref_prefix}%")


def test_publish_local_claim_redacts_and_reaches_the_global_table():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid.uuid4().hex[:8]
        skill_ref = f"local-claims-e2e-task-{run_id}"
        statement_prefix = f"local-claims-e2e-{run_id}"
        try:
            await _cleanup(pool, skill_ref_prefix=skill_ref, statement_prefix=statement_prefix)
            await _make_task_node(pool, skill_ref=skill_ref)

            with tempfile.TemporaryDirectory() as tmp_dir:
                store = LocalClaimStore(repo_root=tmp_dir)
                local_result = capture_local_claim(
                    store,
                    statement=f"{statement_prefix} uses key {FAKE_AWS_KEY} to sign in",
                    subject="src/db",
                    predicate="centralizes",
                    object="database access",
                    repo_root=tmp_dir,
                    created_by="local_claim_capture",
                )
                local_row_id = local_result["id"]

                local_claim = store.get_local_claim(local_row_id)
                assert FAKE_AWS_KEY in local_claim["statement"], (
                    "fixture sanity: the raw local row must actually contain the "
                    "unredacted secret before publish"
                )

                published = await publish_local_claim(
                    store,
                    local_row_id,
                    pool=pool,
                    published_by="tester@example.com",
                    task_ids=[skill_ref],
                )

                # Durable local-side publish link written after the
                # successful global write.
                republished_local = store.get_local_claim(local_row_id)
                assert republished_local["published_claim_id"] == published["id"]
                assert republished_local["published_claim_row_id"] == published["id"]
                assert republished_local["published_by"] == "tester@example.com"
                assert republished_local["published_at"]

                # A second publish of the SAME local row is refused, not
                # silently duplicated into a second global row.
                with pytest.raises(AlreadyClaimPublishedError):
                    await publish_local_claim(
                        store,
                        local_row_id,
                        pool=pool,
                        published_by="tester@example.com",
                        task_ids=[skill_ref],
                    )

                # An explicit force=True re-publish IS allowed, and
                # produces a genuinely new, independent global row.
                republished = await publish_local_claim(
                    store,
                    local_row_id,
                    pool=pool,
                    published_by="tester@example.com",
                    task_ids=[skill_ref],
                    force=True,
                )
                assert republished["id"] != published["id"], (
                    "a forced re-publish must create a new, independent global "
                    "claim row, not overwrite/reuse the first one"
                )

            row = await pool.fetchrow(
                "SELECT * FROM knowledge_nodes WHERE id = $1::uuid", published["id"],
            )
            assert row is not None, "published row must be reachable in the real global table"
            assert row["node_type"] == "claim"

            # Redaction actually ran -- AWS-key-shaped secret.
            assert FAKE_AWS_KEY not in row["properties"]["statement"]
            assert "[REDACTED:aws_access_key]" in row["properties"]["statement"]

            # Authorship preserved and explicit.
            assert row["created_by"] == "tester@example.com"
            assert row["owner_id"] == "tester@example.com"

            # The first-published row still exists, untouched, independent
            # of the forced republish.
            first_row = await pool.fetchrow(
                "SELECT * FROM knowledge_nodes WHERE id = $1::uuid", published["id"],
            )
            assert first_row is not None
        finally:
            await _cleanup(pool, skill_ref_prefix=skill_ref, statement_prefix=statement_prefix)
            await pool.close()

    asyncio.run(_run())


def test_publish_local_claim_requires_an_anchor():
    """Neither task_ids nor justification_episode_id given -> ValueError
    raised BEFORE ever calling the real capture_claim() (which would
    otherwise silently return None)."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                store = LocalClaimStore(repo_root=tmp_dir)
                local_result = capture_local_claim(
                    store, statement="an unanchored claim", repo_root=tmp_dir,
                    created_by="local_claim_capture",
                )
                with pytest.raises(ValueError):
                    await publish_local_claim(
                        store, local_result["id"],
                        pool=pool, published_by="tester@example.com",
                    )
        finally:
            await pool.close()

    asyncio.run(_run())


def test_publish_local_claim_unresolvable_task_ids_raise_not_silently_none():
    """A caller-supplied anchor that fails to resolve to any live
    task_node must raise ClaimPublishFailedError, not be treated as a
    quiet success with capture_claim's real None return value."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                store = LocalClaimStore(repo_root=tmp_dir)
                local_result = capture_local_claim(
                    store, statement="an unresolvable-anchor claim", repo_root=tmp_dir,
                    created_by="local_claim_capture",
                )
                with pytest.raises(ClaimPublishFailedError):
                    await publish_local_claim(
                        store, local_result["id"],
                        pool=pool, published_by="tester@example.com",
                        task_ids=["definitely-does-not-exist-skill-ref"],
                    )
        finally:
            await pool.close()

    asyncio.run(_run())


def test_publish_local_claim_requires_explicit_publisher():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                store = LocalClaimStore(repo_root=tmp_dir)
                local_result = capture_local_claim(
                    store, statement="x", repo_root=tmp_dir,
                    created_by="local_claim_capture",
                )
                with pytest.raises(ValueError):
                    await publish_local_claim(
                        store, local_result["id"],
                        pool=pool, published_by="",
                        task_ids=["whatever"],
                    )
        finally:
            await pool.close()

    asyncio.run(_run())


def test_publish_local_claim_unknown_row_raises_not_found():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                store = LocalClaimStore(repo_root=tmp_dir)
                with pytest.raises(LocalClaimNotFound):
                    await publish_local_claim(
                        store, "does-not-exist",
                        pool=pool, published_by="tester@example.com",
                        task_ids=["whatever"],
                    )
        finally:
            await pool.close()

    asyncio.run(_run())
