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

Also proves (this pass): the local row's own durable publish link is
written after a successful publish, a second publish of the same local
row is refused (not silently duplicated) unless `force=True`, a second
private-token shape (a GitHub personal access token) is also redacted,
and that neither the local candidate-capture loop nor the runner/
learning-loop modules ever call `publish_local_procedure` themselves.
"""
import asyncio
import os
import tempfile

import pytest

from app.db.session import create_pool
from app.local_agent.local_store import LocalProcedureNotFound, LocalProcedureStore
from app.services.procedures import get_procedure
from app.services.publish import (
    AlreadyPublishedError,
    PUBLISHED_PROVENANCE,
    publish_local_procedure,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test",
)

FAKE_AWS_KEY = "AKIAABCDEFGHIJKLMNOP"  # matches KNOWN_TOKEN_PATTERNS["aws_access_key"]
FAKE_GITHUB_TOKEN = "ghp_" + "a" * 36  # matches KNOWN_TOKEN_PATTERNS["github_token"]
FAKE_WINDOWS_PATH = r"C:\Users\chait\Prog\secret-repo\deploy.ps1"
FAKE_POSIX_PATH = "/home/chait/secret-repo/deploy.sh"


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
                        {"action": "deploy", "detail": f"push via git using token {FAKE_GITHUB_TOKEN}"},
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
                    local_store=store,
                    local_row_id=local_row_id,
                    actor_subject="tester@example.com",
                    scope_type="global",
                )

                # Durable local-side publish link written after the
                # successful global write.
                republished_local = store.get_local_procedure(local_row_id)
                assert republished_local["published_procedure_id"] == published["procedure_id"]
                assert republished_local["published_procedure_row_id"] == published["id"]
                assert republished_local["published_by"] == "tester@example.com"
                assert republished_local["published_at"]

                # A second publish of the SAME local row is refused, not
                # silently duplicated into a second global row.
                with pytest.raises(AlreadyPublishedError):
                    await publish_local_procedure(
                        pool,
                        local_store=store,
                        local_row_id=local_row_id,
                        actor_subject="tester@example.com",
                        scope_type="global",
                    )

                # An explicit force=True re-publish IS allowed, and
                # produces a genuinely new, independent global row (not
                # the same id as the first publish).
                republished = await publish_local_procedure(
                    pool,
                    local_store=store,
                    local_row_id=local_row_id,
                    actor_subject="tester@example.com",
                    scope_type="global",
                    force=True,
                )
                assert republished["id"] != published["id"], (
                    "a forced re-publish must create a new, independent global "
                    "candidate row, not overwrite/reuse the first one"
                )

            row = await get_procedure(pool, published["id"])
            assert row is not None, "published row must be reachable via the real get_procedure()"

            # Redaction actually ran -- AWS-key-shaped secret.
            assert FAKE_AWS_KEY not in row["steps"][0]["detail"]
            assert "[REDACTED:aws_access_key]" in row["steps"][0]["detail"]
            assert FAKE_AWS_KEY not in row["name"]
            assert FAKE_AWS_KEY not in row["goal"]

            # Redaction actually ran -- second, distinct secret shape
            # (GitHub personal access token) in a different step, proving
            # this isn't a single-pattern coincidence.
            assert FAKE_GITHUB_TOKEN not in row["steps"][1]["detail"]
            assert "[REDACTED:github_token]" in row["steps"][1]["detail"]

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
                # Hardening pass: an empty/anonymous actor_subject is now
                # an authorization failure (UnauthorizedPublication), not
                # a generic ValueError -- checked BEFORE the local row is
                # even read.
                from app.services.publish import UnauthorizedPublication

                with pytest.raises(UnauthorizedPublication):
                    await publish_local_procedure(
                        pool,
                        local_store=store,
                        local_row_id=local_result["id"],
                        actor_subject="",
                    )
        finally:
            await pool.close()

    asyncio.run(_run())


def test_publish_local_procedure_unknown_row_raises_not_found():
    """publish_local_procedure resolves the local row itself (via the
    store + row id) rather than trusting a caller-supplied dict -- an id
    that doesn't resolve to a live local row must raise, not silently
    publish nothing or crash on a KeyError deep inside redaction."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                store = LocalProcedureStore(repo_root=tmp_dir)
                with pytest.raises(LocalProcedureNotFound):
                    await publish_local_procedure(
                        pool,
                        local_store=store,
                        local_row_id="does-not-exist",
                        actor_subject="tester@example.com",
                    )
        finally:
            await pool.close()

    asyncio.run(_run())


def test_verification_promotion_never_auto_publishes():
    """Proves the spec's 'publication remains EXPLICIT' requirement for
    real: drives a local procedure across the real candidate->verified
    threshold (MIN_SUCCESSES_FOR_VERIFIED successes, MIN_DISTINCT_
    CONTEXTS_FOR_VERIFIED distinct contexts, 0 failures -- the exact
    real arithmetic `record_local_execution_outcome` implements) and
    asserts the row's durable publish link is still completely unset --
    nothing in the local candidate/verification path calls
    publish_local_procedure on its own."""
    from app.services.procedures import (
        MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
        MIN_SUCCESSES_FOR_VERIFIED,
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        store = LocalProcedureStore(repo_root=tmp_dir)
        local_result = store.capture_local_procedure(
            name="publish-test-no-autopublish", goal="g",
            provenance="system_pending_review", scope_type="user",
            scope_entity_id="local-workspace-1",
        )
        row_id = local_result["id"]

        record = None
        for i in range(MIN_SUCCESSES_FOR_VERIFIED):
            context_key = f"ctx-{i % MIN_DISTINCT_CONTEXTS_FOR_VERIFIED}"
            record = store.record_local_execution_outcome(
                row_id=row_id, success=True, context_key=context_key,
            )

        assert record["verification_state"] == "verified", (
            "fixture sanity: the real promotion threshold must actually "
            "have been crossed for this test to prove anything"
        )
        assert record["published_procedure_id"] is None
        assert record["published_procedure_row_id"] is None
        assert record["published_by"] is None
        assert record["published_at"] is None

    # Independent, static confirmation: no module in the local candidate/
    # verification/runner path imports or CALLS publish_local_procedure.
    # (Live-DB test above already proves the durable link stays unset
    # across the real threshold crossing; this closes the "and no other
    # code path calls it either" half of the claim.) Checked as an actual
    # call/import pattern, not a bare substring -- these modules'
    # `local_store.py` legitimately mentions the NAME in comments (e.g.
    # documenting when `mark_local_procedure_published` is written), which
    # is not the same claim as the function being invoked.
    import inspect
    import re

    from app.local_agent import local_learning
    from app.local_agent import local_store as local_store_module
    from app.local_agent import runner as runner_module

    call_or_import_re = re.compile(
        r"(?<!def )(?:import\s+publish_local_procedure|from\s+\S*publish\s+import\s+[^\n]*publish_local_procedure"
        r"|publish_local_procedure\s*\()"
    )
    for module in (local_learning, local_store_module, runner_module):
        source = inspect.getsource(module)
        # Check only the CODE portion of each line (before any '#') --
        # a bare substring/regex search over the full source also matches
        # inside comments, and a comment is legitimately allowed to name
        # this function (e.g. explaining why a nearby value is deliberately
        # NOT forwarded to it -- runner.py does exactly this around its
        # own local-store embedding capture). That is documentation, not
        # an import or a call.
        code_only = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
        assert not call_or_import_re.search(code_only), (
            f"{module.__name__} must never import/call publish_local_procedure "
            "itself -- publication is explicit-only, never automatic"
        )


def test_publish_local_procedure_scrubs_absolute_paths_everywhere():
    """Proves the GAP CLOSED THIS PASS fix for real: an absolute Windows
    path AND an absolute POSIX path, planted in steps/preconditions/
    scope/exclusions (not just name/goal/steps), must not survive
    unchanged onto the published global row -- machine-specific
    filesystem locations are exactly the kind of private, non-portable
    detail publication must scrub."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        name_prefix = "publish-test-paths"
        try:
            await _cleanup(pool, name_prefix)

            with tempfile.TemporaryDirectory() as tmp_dir:
                store = LocalProcedureStore(repo_root=tmp_dir)
                local_result = store.capture_local_procedure(
                    name=f"{name_prefix}-1",
                    goal="run the deploy script",
                    steps=[
                        {"action": "run", "detail": f"execute {FAKE_WINDOWS_PATH}"},
                        {"action": "run", "detail": f"then execute {FAKE_POSIX_PATH}"},
                    ],
                    preconditions=[{"check": "file_exists", "path": FAKE_WINDOWS_PATH}],
                    scope={"repo_root": FAKE_POSIX_PATH},
                    exclusions=[f"do not touch {FAKE_WINDOWS_PATH}"],
                    provenance="system_pending_review",
                    scope_type="user",
                    scope_entity_id="local-workspace-1",
                )
                local_row_id = local_result["id"]

                local_procedure = store.get_local_procedure(local_row_id)
                assert FAKE_WINDOWS_PATH in local_procedure["steps"][0]["detail"], (
                    "fixture sanity: raw local row must contain the unredacted "
                    "absolute path before publish"
                )

                published = await publish_local_procedure(
                    pool,
                    local_store=store,
                    local_row_id=local_row_id,
                    actor_subject="tester@example.com",
                    scope_type="global",
                )

            row = await get_procedure(pool, published["id"])
            assert row is not None

            payload = str(row["steps"]) + str(row["preconditions"]) + str(row["scope"]) + str(row["exclusions"])
            assert FAKE_WINDOWS_PATH not in payload, "absolute Windows path leaked into published global row"
            assert FAKE_POSIX_PATH not in payload, "absolute POSIX path leaked into published global row"
            assert "[REDACTED:absolute_path]" in payload
            # Steps/preconditions/scope/exclusions all actually got scrubbed,
            # not just steps.
            assert "[REDACTED:absolute_path]" in str(row["steps"])
            assert "[REDACTED:absolute_path]" in str(row["preconditions"])
            assert "[REDACTED:absolute_path]" in str(row["scope"])
            assert "[REDACTED:absolute_path]" in str(row["exclusions"])
        finally:
            await _cleanup(pool, name_prefix)
            await pool.close()

    asyncio.run(_run())
