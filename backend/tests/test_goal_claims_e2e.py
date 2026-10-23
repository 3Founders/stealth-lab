"""Database-backed proving tests for migration 153 (goal_claims) and services/goal_claims.py: a Goal's contract as linked claims, and
its status COMPUTED from the evidence on the guarantee claims (docs/verification_changes_prop.md section 6, minus composition across
the Goal tree and human review, which are next).

LIVE-DATABASE TEST, LOOPBACK ONLY, like test_local_check_evidence_e2e.py: it needs a DATABASE_URL on this machine with the whole
migration chain applied (the scratch cluster on 127.0.0.1:55432, database kel_evidence_test), refuses any other host, and skips
without a DATABASE_URL. The service runs as a plain role (a superuser bypasses row-level security); the owner connection prepares
data and writes claim evidence with the repository's real writer (claim_evidence.record_claim_evidence).
"""
from __future__ import annotations

import asyncio
import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

from app.db.session import create_pool
from app.services import goal_claims as gc
from app.services.access import AccessScope
from app.services.claim_evidence import record_claim_evidence

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires a loopback DATABASE_URL with migration 153 applied")

APP_ROLE = "kel_evidence_app"
A, B = AccessScope.for_user("user-a"), AccessScope.for_user("user-b")
CRITERIA = {"predicate": "the check exits 0"}


def _guard() -> str:
    host = urlsplit(DATABASE_URL).hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        pytest.exit(f"refusing to run: DATABASE_URL host {host!r} is not a loopback database", returncode=2)
    return DATABASE_URL


def _app_dsn(dsn: str) -> str:
    p = urlsplit(dsn)
    return urlunsplit((p.scheme, f"{APP_ROLE}@{p.hostname}" + (f":{p.port}" if p.port else ""), p.path, p.query, p.fragment))


async def _with_pools(scenario):
    dsn = _guard()
    admin = await create_pool(dsn)
    try:
        async with admin.acquire() as c:
            if not await c.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", APP_ROLE):
                await c.execute(f"CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS")
            await c.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
            await c.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
            await c.execute(f"GRANT INSERT, UPDATE ON goal_claims TO {APP_ROLE}")
            await c.execute("TRUNCATE goal_claims")
        app = await create_pool(_app_dsn(dsn))
        try:
            return await scenario(admin, app)
        finally:
            await app.close()
    finally:
        await admin.close()


def db(fn):
    def wrapper():
        return asyncio.run(_with_pools(fn))
    wrapper.__name__ = fn.__name__
    return wrapper


async def _goal(admin, visibility="public") -> str:
    return str(await admin.fetchval(
        "INSERT INTO goals (canonical_name, provenance, scope_type, scope_entity_id, visibility) "
        "VALUES ('goal ' || gen_random_uuid()::text, 'company_ingested', 'repository', 'r:0123456789abcdef', $1::visibility_level) RETURNING id",
        visibility))


async def _claim(admin, text="x holds", status="supported") -> str:
    return str(await admin.fetchval(
        "INSERT INTO knowledge_nodes (node_type, name, properties, provenance, claim_status, visibility) "
        "VALUES ('claim', $1, jsonb_build_object('statement', $1::text), 'company_ingested', $2, 'public') RETURNING id", text, status))


async def _pass(admin, claim, group=None, kind="execution_result"):
    return await record_claim_evidence(admin, claim_id=claim, evidence_type=kind, outcome_status="success", success_criteria=CRITERIA,
                                       direction="supports", independence_group=group)


async def _fail(admin, claim, group=None):
    return await record_claim_evidence(admin, claim_id=claim, evidence_type="execution_result", outcome_status="failure",
                                       success_criteria=None, direction="contradicts", independence_group=group)


async def _status(app, goal, scope=A):
    return await gc.goal_status(app, goal, scope=scope)


def test_the_gate_and_the_writer_land_together():
    @db
    async def scenario(admin, app):
        goal, claim = await _goal(admin), await _claim(admin)
        node = str(await admin.fetchval("INSERT INTO knowledge_nodes (node_type, name, properties, provenance) "
                                        "VALUES ('entity', 'not a claim', '{}', 'company_ingested') RETURNING id"))
        out = await gc.link_goal_claim(app, goal_id=goal, claim_id=claim, role="guarantee", scope=A)
        assert out["linked"] is True and out["duplicate"] is False
        again = await gc.link_goal_claim(app, goal_id=goal, claim_id=claim, role="guarantee", scope=A)
        assert again["duplicate"] is True and again["id"] == out["id"]
        # the writer refuses a non-claim, a bad role, an anonymous caller; the engine refuses a non-claim from direct SQL
        with pytest.raises(gc.GoalClaimError, match="not found"):
            await gc.link_goal_claim(app, goal_id=goal, claim_id=node, role="guarantee", scope=A)
        with pytest.raises(gc.GoalClaimError, match="role"):
            await gc.link_goal_claim(app, goal_id=goal, claim_id=claim, role="goal", scope=A)
        with pytest.raises(gc.GoalClaimError, match="sign in"):
            await gc.link_goal_claim(app, goal_id=goal, claim_id=claim, role="guarantee", scope=AccessScope.anonymous())
        with pytest.raises(asyncpg.RaiseError, match="is not a claim"):
            await admin.execute("INSERT INTO goal_claims (goal_id, claim_id, role) VALUES ($1::uuid, $2::uuid, 'guarantee')", goal, node)
        with pytest.raises(asyncpg.CheckViolationError):
            await admin.execute("INSERT INTO goal_claims (goal_id, claim_id, role) VALUES ($1::uuid, $2::uuid, 'goal')", goal, claim)
        # invalidate-and-append: a link is ended, never edited or deleted
        with pytest.raises(asyncpg.RaiseError, match="only ending the link"):
            await admin.execute("UPDATE goal_claims SET role = 'assumption' WHERE id = $1::uuid", out["id"])
        with pytest.raises(asyncpg.RaiseError, match="DELETE rejected"):
            await admin.execute("DELETE FROM goal_claims WHERE id = $1::uuid", out["id"])
        assert (await gc.unlink_goal_claim(app, link_id=out["id"], scope=A))["ended"] is True
        with pytest.raises(gc.GoalClaimError):
            await gc.unlink_goal_claim(app, link_id=out["id"], scope=A)
        assert await admin.fetchval("SELECT count(*) FROM goal_claims") == 1, "nothing was deleted"
        relinked = await gc.link_goal_claim(app, goal_id=goal, claim_id=claim, role="guarantee", scope=A)
        assert relinked["linked"] is True and relinked["id"] != out["id"], "a changed contract is a new link"

    scenario()


def test_there_is_no_stored_status_and_goals_status_keeps_its_meaning():
    @db
    async def scenario(admin, app):
        cols = {r["column_name"] for r in await admin.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'goal_claims'")}
        assert "status" not in cols and "state" not in cols
        definition = await admin.fetchval("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'goals_status_chk'")
        assert all(w in definition for w in ("candidate", "active", "deprecated", "merged")), "goals.status is the record's lifecycle, unchanged"
        goal = await _goal(admin)
        row = await admin.fetchrow("SELECT status FROM goals WHERE id = $1::uuid", goal)
        assert row["status"] == "candidate"

    scenario()


def test_a_goal_with_a_required_claim_and_no_evidence_is_open_never_verified():
    @db
    async def scenario(admin, app):
        goal = await _goal(admin)
        empty = await _status(app, goal)
        assert empty["status"] == "open" and "empty" in empty["why"]
        c1, c2 = await _claim(admin), await _claim(admin)
        await gc.link_goal_claim(app, goal_id=goal, claim_id=c1, role="guarantee", scope=A)
        await gc.link_goal_claim(app, goal_id=goal, claim_id=c2, role="guarantee", scope=A)
        s = await _status(app, goal)
        assert s["status"] == "open" and s["counts"]["guarantees"] == 2 and s["counts"]["verified_claims"] == 0
        await _pass(admin, c1)
        s = await _status(app, goal)
        assert s["status"] == "open", "one of two guarantees verified is not a verified Goal"
        assert "1 of 2" in s["why"]
        await _pass(admin, c2)
        s = await _status(app, goal)
        assert s["status"] == "verified" and s["counts"]["verified_claims"] == 2

    scenario()


def test_a_self_report_never_makes_a_claim_or_the_goal_verified():
    @db
    async def scenario(admin, app):
        goal, claim = await _goal(admin), await _claim(admin)
        await gc.link_goal_claim(app, goal_id=goal, claim_id=claim, role="guarantee", scope=A)
        # testimony is recorded (the writer for outcomes only builds execution_result and reproduction rows, so these go in directly, as
        # ingestion does): observations, documents, a human review (typed human evidence is the next item) and a half-done outcome
        for kind in ("observation", "document", "artifact", "human_review", "external_source"):
            await admin.execute(
                "INSERT INTO evidence (evidence_type, target_type, target_id, direction, strength_score, strength_method) "
                "VALUES ($1::evidence_kind, 'claim', $2::uuid, 'supports', 1.0, 'asserted')", kind, claim)
        await admin.execute(
            "INSERT INTO evidence (evidence_type, target_type, target_id, direction, strength_score, strength_method, outcome_status) "
            "VALUES ('execution_result', 'claim', $1::uuid, 'supports', 1.0, 'recorded_outcome', 'needs_rework')", claim)
        assert await admin.fetchval("SELECT count(*) FROM evidence WHERE target_id = $1::uuid", claim) == 6, "the testimony IS recorded"
        s = await _status(app, goal)
        assert s["status"] == "open", "six supporting rows, none of them a machine-checked pass: still open"
        assert s["claims"][0]["pass_groups"] == 0
        await _pass(admin, claim)
        assert (await _status(app, goal))["status"] == "verified"
        # a reproduction counts as a machine-checked pass, as it does for a procedure's verified gate
        goal2, c2 = await _goal(admin), await _claim(admin)
        await gc.link_goal_claim(app, goal_id=goal2, claim_id=c2, role="guarantee", scope=A)
        await _pass(admin, c2, kind="reproduction")
        assert (await _status(app, goal2))["status"] == "verified"

    scenario()


def test_a_failed_check_fails_the_goal_and_a_later_contradiction_makes_it_contested():
    @db
    async def scenario(admin, app):
        goal, claim = await _goal(admin), await _claim(admin)
        await gc.link_goal_claim(app, goal_id=goal, claim_id=claim, role="guarantee", scope=A)
        await _fail(admin, claim, group="run-1")
        s = await _status(app, goal)
        assert s["status"] == "failed" and s["counts"]["failed_claims"] == 1
        await _pass(admin, claim, group="run-2")                # an independent pass on the same claim
        s = await _status(app, goal)
        assert s["status"] == "contested", "a pass and a fail on the same claim"
        assert s["claims"][0]["pass_groups"] == 1 and s["claims"][0]["fail_groups"] == 1

    scenario()


def test_independent_groups_are_counted_once_each():
    @db
    async def scenario(admin, app):
        goal, claim = await _goal(admin), await _claim(admin)
        await gc.link_goal_claim(app, goal_id=goal, claim_id=claim, role="guarantee", scope=A)
        for _ in range(3):
            await _pass(admin, claim, group="same-source")
        s = await _status(app, goal)
        assert s["claims"][0]["pass_groups"] == 1, "three rows from one source are one piece of evidence"
        await _pass(admin, claim, group="other-source")
        assert (await _status(app, goal))["claims"][0]["pass_groups"] == 2

    scenario()


def test_a_goal_verified_becomes_expired_when_its_claim_is_superseded_after_the_evidence():
    @db
    async def scenario(admin, app):
        goal, claim, assumption = await _goal(admin), await _claim(admin, "guarantee"), await _claim(admin, "assumption")
        await gc.link_goal_claim(app, goal_id=goal, claim_id=claim, role="guarantee", scope=A)
        await gc.link_goal_claim(app, goal_id=goal, claim_id=assumption, role="assumption", scope=A)
        await _pass(admin, claim)
        assert (await _status(app, goal))["status"] == "verified"
        await asyncio.sleep(0.05)
        await admin.execute("UPDATE knowledge_nodes SET claim_status = 'superseded', t_invalid = now() WHERE id = $1::uuid", claim)
        s = await _status(app, goal)
        assert s["status"] == "expired", "the guarantee the evidence was about no longer holds as written"
        # an assumption superseded after the evidence expires it too
        goal2, c2, a2 = await _goal(admin), await _claim(admin), await _claim(admin)
        await gc.link_goal_claim(app, goal_id=goal2, claim_id=c2, role="guarantee", scope=A)
        await gc.link_goal_claim(app, goal_id=goal2, claim_id=a2, role="assumption", scope=A)
        await _pass(admin, c2)
        assert (await _status(app, goal2))["status"] == "verified"
        await asyncio.sleep(0.05)
        await admin.execute("UPDATE knowledge_nodes SET claim_status = 'stale' WHERE id = $1::uuid", a2)
        assert (await _status(app, goal2))["status"] == "expired"

    scenario()


def test_a_link_to_a_claim_closed_before_any_evidence_is_a_dead_link_not_an_expiry():
    @db
    async def scenario(admin, app):
        goal, dead = await _goal(admin), await _claim(admin, "old", status="superseded")
        await admin.execute("UPDATE knowledge_nodes SET t_invalid = now() WHERE id = $1::uuid", dead)
        await admin.execute("INSERT INTO goal_claims (goal_id, claim_id, role, visibility, owner_id) VALUES ($1::uuid, $2::uuid, 'guarantee', 'public', 'user-a')", goal, dead)
        s = await _status(app, goal)
        assert s["status"] == "open" and s["counts"]["guarantees"] == 0 and s["counts"]["closed_links"] == 1
        fresh = await _claim(admin, "new")
        await gc.link_goal_claim(app, goal_id=goal, claim_id=fresh, role="guarantee", scope=A)
        await _pass(admin, fresh)
        assert (await _status(app, goal))["status"] == "verified", "the new claim carries the Goal; the dead link is ignored"

    scenario()


def test_a_retracted_evidence_row_stops_counting():
    @db
    async def scenario(admin, app):
        goal, claim = await _goal(admin), await _claim(admin)
        await gc.link_goal_claim(app, goal_id=goal, claim_id=claim, role="guarantee", scope=A)
        row = await _pass(admin, claim)
        assert (await _status(app, goal))["status"] == "verified"
        await admin.execute("UPDATE evidence SET t_invalid = now() WHERE id = $1::uuid", row)
        assert (await _status(app, goal))["status"] == "open"

    scenario()


def test_links_are_isolated_by_owner_and_a_public_link_is_seen_by_everyone():
    @db
    async def scenario(admin, app):
        goal, c1, c2 = await _goal(admin), await _claim(admin), await _claim(admin)
        await gc.link_goal_claim(app, goal_id=goal, claim_id=c1, role="guarantee", scope=A)                       # private to user-a
        await gc.link_goal_claim(app, goal_id=goal, claim_id=c2, role="guarantee", scope=A, visibility="public")  # public
        await _pass(admin, c1)
        await _pass(admin, c2)
        sa, sb, anon = await _status(app, goal, A), await _status(app, goal, B), await _status(app, goal, AccessScope.anonymous())
        assert sa["counts"]["guarantees"] == 2 and sa["status"] == "verified", "the owner sees both links"
        assert sb["counts"]["guarantees"] == 1, "another account sees only the public link"
        assert anon["counts"]["guarantees"] == 1
        # the same claim linked privately by two accounts: each keeps its own link
        again = await gc.link_goal_claim(app, goal_id=goal, claim_id=c1, role="guarantee", scope=B)
        assert again["linked"] is True
        assert (await _status(app, goal, B))["counts"]["guarantees"] == 2
        # a public link needs a public Goal
        private_goal = await _goal(admin, visibility="private")
        with pytest.raises(gc.GoalClaimError):
            await gc.link_goal_claim(app, goal_id=private_goal, claim_id=c1, role="guarantee", scope=A, visibility="public")
        # an account cannot end another account's link
        with pytest.raises(gc.GoalClaimError):
            await gc.unlink_goal_claim(app, link_id=again["id"], scope=A)

    scenario()
