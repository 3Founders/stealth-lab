"""
Real, live-database, end-to-end proving test for the spoofing-proof
guarantee `_resolve_caller_identity` (app/mcp_server/server.py) exists to
make: a caller-supplied, identity-shaped tool PARAMETER must never win over
a resolved, real identity when one is present.

test_mcp_server_identity_offline.py already proves the resolution PRIORITY
order of `_resolve_caller_identity` in isolation (fallback / SDK token /
authn actor / SDK-wins-over-authn). What that file does NOT prove: that
calling an actual TOOL (decide_procedure) with both a real resolved
identity AND an attacker-supplied `approver_id` present at the same time
never lets the spoofed value reach the PERSISTED row. This file closes
that gap for real, against a real Postgres, exercising the real
`decide_procedure` tool and the real `approve_procedure`/`reject_procedure`
write path underneath it -- no mocking of those functions.

Same live-DB convention as test_procedures_e2e.py: requires a real
DATABASE_URL, skips (not fails) without one.
"""
import asyncio
import os

import pytest

from app.db.session import create_pool
from app.services.procedures import capture_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


class _FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class _FakeContext:
    def __init__(self, pool):
        self.request_context = _FakeRequestContext(pool)


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{name_prefix}%")


def test_decide_procedure_resolved_identity_overrides_spoofed_approver_id():
    """The real spoofing-proof proof: a mocked-real SDK access-token
    identity (the same real contextvar `_resolve_caller_identity` reads
    via mcp.server.auth.middleware.auth_context.get_access_token(), set
    here exactly the way test_mcp_server_identity_offline.py's own
    `_set_access_token` helper does) is present at the same time as a
    deliberately DIFFERENT, attacker-controlled `approver_id` parameter.

    Calls the real `decide_procedure` tool (not a mock of
    approve_procedure) and asserts the PERSISTED `procedures.approved_by`
    row shows the RESOLVED identity ("real-reviewer-42"), never the
    spoofed one ("attacker-claims-to-be-admin")."""
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken

    import app.mcp_server.server as srv

    RESOLVED_IDENTITY = "real-reviewer-42"
    SPOOFED_APPROVER_ID = "attacker-claims-to-be-admin"

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-identity-spoof")
            result = await capture_procedure(
                pool, name="proc-test-identity-spoof-1", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            procedure_id = result["procedure_id"]
            row_id = result["id"]

            token = AccessToken(
                token="irrelevant-in-this-test",
                client_id="stealthlab-local",
                scopes=["stealthlab:tools"],
                subject=RESOLVED_IDENTITY,
            )
            cv_token = auth_context_var.set(AuthenticatedUser(token))
            try:
                ctx = _FakeContext(pool)
                response = await srv.decide_procedure(
                    procedure_id=procedure_id,
                    approver_id=SPOOFED_APPROVER_ID,
                    decision="approved",
                    ctx=ctx,
                )
            finally:
                auth_context_var.reset(cv_token)

            assert "REFUSED" not in response

            row = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", row_id)
            assert row["approval_status"] == "approved"
            assert row["approved_by"] == RESOLVED_IDENTITY, (
                f"approved_by must be the resolved real identity "
                f"({RESOLVED_IDENTITY!r}), never the caller-supplied, "
                f"self-asserted approver_id ({SPOOFED_APPROVER_ID!r}) -- "
                f"got {row['approved_by']!r}"
            )
            assert row["approved_by"] != SPOOFED_APPROVER_ID

            # The audit trail (changeset_record's real ChangeSet, written by
            # approve_procedure itself) must carry the same real identity as
            # its author -- not just the procedures row.
            cs_row = await pool.fetchrow(
                "SELECT author FROM change_sets WHERE reason = $1 ORDER BY created_at DESC LIMIT 1",
                "procedure approval_status -> approved",
            )
            if cs_row is not None:
                assert cs_row["author"] == RESOLVED_IDENTITY
                assert cs_row["author"] != SPOOFED_APPROVER_ID
        finally:
            await _cleanup(pool, "proc-test-identity-spoof")
            await pool.close()

    asyncio.run(_run())


def test_decide_procedure_falls_back_to_self_asserted_approver_when_no_real_identity():
    """Honest counterpart to the spoofing-proof test above: with NO real
    identity resolvable (no SDK access token, no authn actor -- the
    documented stdio/local-dev posture), the caller-supplied approver_id
    is used as-is, unchanged from today's behaviour. This is the fallback
    degrading honestly, not silently blanking attribution."""
    APPROVER = "human-reviewer-self-asserted"

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool, "proc-test-identity-fallback")
            result = await capture_procedure(
                pool, name="proc-test-identity-fallback-1", goal="g",
                provenance="system_pending_review", scope_type="global",
            )
            procedure_id = result["procedure_id"]
            row_id = result["id"]

            import app.mcp_server.server as srv

            ctx = _FakeContext(pool)
            response = await srv.decide_procedure(
                procedure_id=procedure_id, approver_id=APPROVER,
                decision="approved", ctx=ctx,
            )
            assert "REFUSED" not in response

            row = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", row_id)
            assert row["approved_by"] == APPROVER
        finally:
            await _cleanup(pool, "proc-test-identity-fallback")
            await pool.close()

    asyncio.run(_run())
