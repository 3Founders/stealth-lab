"""
The single consolidated canonical V1 lifecycle, end to end, in one
isolated flow against a live Postgres + a pair of real file-local
LocalProcedureStores (User A and User B).

WHY THIS FILE EXISTS. Every stage below is already covered somewhere in
the suite (test_historical_bootstrap_offline.py, test_local_learning_
sweep_offline.py, test_canonical_personal_memory_e2e.py, test_second_
user_global_reuse_e2e.py, test_band2_4_failures.py, ...). What no single
test did before is run the whole ten-stage lifecycle as ONE continuous
flow on fresh disposable state, so a regression that only shows up when
the stages are composed cannot hide between files.

HARD RULES honoured here (directive "FINAL V1 FREEZE FIX", part 2):
  - Fresh disposable state: two brand-new tempdir LocalProcedureStores, a
    brand-new throwaway git repo, name-prefixed global rows retracted /
    deleted in `finally`.
  - No stage inserts the final state it claims to discover. The private
    candidate in stage 3 is NOT hand-seeded -- a real Claude Code trace
    file is written and the real automatic-learning entrypoint
    (`run_local_learning_sweep`, the exact function the ingestion
    scheduler's local tick calls) is what produces it. The global
    verified+approved procedure in stage 8 is driven there ONLY through
    `record_execution_outcome` + `approve_procedure`, never a raw UPDATE.
  - No hidden extraction function is called to fake automatic learning:
    `run_local_learning_sweep` / `run_bootstrap` ARE the product paths.
  - Privacy, verification, applicability and identity semantics are
    exercised, never relaxed: User B has a separate store and a distinct
    AccessScope; the candidate->verified gate is the real threshold
    arithmetic; a stale procedure is excluded at the hard gate, not
    scored low; a recorded failure never lifts a success count.

The private procedures a stage needs as a *starting condition* (one to
publish, one to drive stale) are captured through the real
`capture_local_procedure` write path, the same way
test_canonical_personal_memory_e2e.py / test_second_user_global_reuse_
e2e.py capture theirs -- that is a precondition, not the thing under
test. Only stage 3's procedure is one the test "discovers", and that one
is produced by the real sweep, never captured directly.

Skips (does not fail) without a real DATABASE_URL -- same convention as
every other *_e2e.py file.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires a real DATABASE_URL -- this is a live-database integration test",
)

RUN = uuid4().hex[:8]
PFX = f"idealv1-{RUN}"
USER_A = f"{PFX}-user-a@example.com"
USER_B = f"{PFX}-user-b@example.com"

# The goal string stage 3's trace teaches and stage 5 reinforces. Kept
# byte-stable and PFX-prefixed so (a) it never collides with a
# bootstrapped candidate from stage 1 and (b) the merge in stage 5 is an
# exact-goal lexical match.
NEW_TASK_GOAL = f"{PFX} wire up websocket reconnect backoff with jitter"

# Real-corpus live DB: this shared database carries hundreds of
# 0-precondition procedures from other tests, and applicability.py's
# no-embedding candidate pre-filter is a bounded `candidate_pool_size`.
# The lifecycle rows have no goal embedding, so every find_applicable_
# procedures call here widens the pool/limit to test RETRIEVABILITY and
# the verification gate specifically, not incidental placement inside a
# small default window -- the idiom test_canonical_personal_memory_e2e.py
# already documents.
WIDE = dict(limit=5000, candidate_pool_size=20000)
_WHOLE_QUEUE = 10_000_000


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _make_repo_with_history(root: Path) -> Path:
    """A real throwaway repo: a SKILL.md (repo-docs source) plus a
    fix-commit -> co-located-test-commit history (the conservative
    git_history pattern that actually forms a candidate)."""
    repo = root / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "skills" / "deploy").mkdir(parents=True)
    (repo / "skills" / "deploy" / "SKILL.md").write_text(
        f"---\nname: {PFX}-deploy-service\n"
        f"description: Deploy the {PFX} service to staging\n"
        "applies_when: repository uses docker compose\n---\n"
        "## Steps\n1. build the image\n2. push to registry\n3. restart the stack\n",
        encoding="utf-8",
    )
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    (repo / "src" / "sockets.py").write_text("def connect(x):\n    return x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"{PFX} initial socket client")
    (repo / "src" / "sockets.py").write_text(
        "def connect(x):\n    return x.strip()\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"fix: {PFX} socket client mishandles trailing whitespace")
    (repo / "src" / "test_sockets.py").write_text(
        "from sockets import connect\n\ndef test_strip():\n    assert connect(' a ') == 'a'\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"{PFX} add regression test for the whitespace fix")
    return repo


def _claude_export(root: Path) -> str:
    convs = [{
        "uuid": f"{PFX}-c-exec",
        "name": f"{PFX} fix failing auth test",
        "chat_messages": [
            {"sender": "human", "text": f"{PFX} auth tests are failing"},
            {"sender": "assistant",
             "text": "Let me run the suite.\n```\npytest tests/test_auth.py -q\n```"},
            {"sender": "human", "text": "It ran and the tests passed after your fix."},
        ],
    }, {
        "uuid": f"{PFX}-c-disc",
        "name": f"{PFX} architecture brainstorm",
        "chat_messages": [
            {"sender": "human", "text": "Should we use a queue?"},
            {"sender": "assistant", "text": "You could use Redis, but let's discuss tradeoffs first."},
        ],
    }]
    path = root / "claude_conversations.json"
    path.write_text(json.dumps(convs), encoding="utf-8")
    return str(path)


def _chatgpt_export(root: Path) -> str:
    def node(role, text, t, mid):
        return {"message": {"author": {"role": role}, "id": mid,
                            "create_time": t, "content": {"parts": [text]}}}
    convs = [{
        "uuid": f"{PFX}-g-exec",
        "title": f"{PFX} add retry logic",
        "create_time": 1754000000,
        "mapping": {
            "a": node("user", f"{PFX} how do I add retries to our client?", 1754000000, "m1"),
            "b": node("assistant", "Use tenacity:\n```\npip install tenacity\n```", 1754000001, "m2"),
            "c": node("user", "executed it and the tests passed", 1754000002, "m3"),
        },
    }]
    path = root / "chatgpt_conversations.json"
    path.write_text(json.dumps(convs), encoding="utf-8")
    return str(path)


def _trace_session(dir_: Path, name: str, goal: str) -> None:
    (dir_ / name).write_text("\n".join([
        json.dumps({"type": "user", "message": {"content": goal}}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "pytest tests/test_sockets.py -q"}}]}}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ruff check ."}}]}}),
    ]), encoding="utf-8")


async def _cleanup(pool) -> None:
    await pool.execute(
        "UPDATE failure_routes SET t_invalid = now() WHERE t_invalid IS NULL "
        "AND evidence_id IN (SELECT id FROM evidence WHERE target_id IN "
        "(SELECT id FROM procedures WHERE name LIKE $1))", f"{PFX}%",
    )
    await pool.execute(
        "UPDATE evidence SET t_invalid = now() WHERE t_invalid IS NULL "
        "AND target_id IN (SELECT id FROM procedures WHERE name LIKE $1)", f"{PFX}%",
    )
    await pool.execute(
        "DELETE FROM procedures WHERE name LIKE $1 "
        "AND id NOT IN (SELECT procedure_row_id FROM execution_plans)", f"{PFX}%",
    )
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{PFX}%")


def test_ideal_v1_full_lifecycle():
    from app.db.session import create_pool
    from app.execution.failures import classify_and_route, fetch_route_queue
    from app.execution.plans import compile_plan
    from app.local_agent.historical_bootstrap import run_bootstrap
    from app.local_agent.local_applicability import check_local_hard_constraints
    from app.local_agent.local_learning_sweep import run_local_learning_sweep
    from app.local_agent.local_store import LocalProcedureStore
    from app.local_agent.unified_retrieval import rank_unified_candidates
    from app.services.access import AccessScope
    from app.services.applicability import find_applicable_procedures
    from app.services.procedures import (
        MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
        MIN_SUCCESSES_FOR_VERIFIED,
        approve_procedure,
        get_procedure,
        record_execution_outcome,
    )
    from app.services.publish import publish_local_procedure

    def _verify_local(store, row_id):
        """Drive a local candidate to 'verified' through the real
        threshold arithmetic -- MIN_SUCCESSES_FOR_VERIFIED successes over
        MIN_DISTINCT_CONTEXTS_FOR_VERIFIED distinct contexts, no raw
        write. Returns the updated row."""
        updated = None
        for i in range(MIN_SUCCESSES_FOR_VERIFIED):
            updated = store.record_local_execution_outcome(
                row_id=row_id, success=True,
                context_key=f"{PFX}-lctx-{i % MIN_DISTINCT_CONTEXTS_FOR_VERIFIED}",
            )
        assert updated["verification_state"] == "verified", updated["verification_stats"]
        return updated

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        try:
            await _cleanup(pool)

            store_a = LocalProcedureStore(str(root / "user-a-workspace"))
            store_b = LocalProcedureStore(str(root / "user-b-workspace"))

            # =============================================================
            # STAGE 1 -- cold-start bootstrap: repo + git + Claude +
            # ChatGPT + traces converge into ONE private personal library,
            # DB-free, provenance preserved, all as candidates.
            # =============================================================
            repo = _make_repo_with_history(root)
            hist_traces = root / "bootstrap-traces"
            hist_traces.mkdir()
            _trace_session(hist_traces, "hist-1.jsonl", f"{PFX} tidy up the socket client imports")

            summary = run_bootstrap(
                store_a,
                repo_root=str(repo),
                claude_export=_claude_export(root),
                chatgpt_export=_chatgpt_export(root),
                claude_traces_dir=str(hist_traces),
                embed=None,
            )
            assert summary["captured"] >= 1
            assert "git" in summary and summary["git"]["commits_scanned"] == 3

            boot_rows = store_a.list_local_procedures()
            assert len(boot_rows) >= 3, boot_rows
            src_types = {
                ref.get("source_type")
                for r in boot_rows
                for ref in (r.get("evidence_refs") or [])
            }
            assert {"git_history", "claude_chat", "chatgpt_chat", "claude_code_trace"} <= src_types, src_types
            assert all(r["verification_state"] == "candidate" for r in boot_rows), (
                "nothing is born verified from history"
            )
            assert all(
                ref.get("privacy") == "local"
                for r in boot_rows
                for ref in (r.get("evidence_refs") or [])
            ), "every bootstrapped provenance record is private"
            assert not any("brainstorm" in (r["name"] or "").lower() for r in boot_rows), (
                "pure discussion never became a row"
            )
            boot_names = {r["name"] for r in boot_rows}

            # =============================================================
            # STAGE 2 -- personal retrieval -> local applicability -> reuse
            # -> real local execution, against the private library only.
            # =============================================================
            # a procedure already curated in User A's library (also the
            # one that gets published in stage 7)
            pub = store_a.capture_local_procedure(
                name=f"{PFX} roll the canary deployment forward",
                goal="promote the canary once its error budget holds",
                steps=[{"order": 0, "action": "check the canary error budget"},
                       {"order": 1, "action": "shift 100% of traffic"}],
                provenance="system_pending_review",
                scope_type="user", scope_entity_id=f"{PFX}-ws-a",
            )
            pub_id = pub["id"]

            # personal retrieval reaches BOTH curated and bootstrapped
            # material
            assert any(
                h["id"] == pub_id
                for h in store_a.search_local_procedures("promote the canary deployment")
            ), "a curated private procedure must be retrievable"
            assert store_a.search_local_procedures("socket client whitespace regression"), (
                "bootstrapped git-history material must be retrievable too"
            )

            pub_row = store_a.get_local_procedure(pub_id)
            gate = check_local_hard_constraints(
                pub_row, current_scope={}, require_verified=False, environment_facts=[],
            )
            assert gate.applicable is True, gate.failed_constraints

            reused = store_a.record_local_execution_outcome(
                row_id=pub_id, success=True, context_key=f"{PFX}-reuse-ctx",
            )
            assert reused["verification_stats"]["attempts"] == 1
            assert reused["verification_state"] == "candidate", (
                "one successful reuse is not the verification threshold"
            )

            # =============================================================
            # STAGE 3 -- a NEW task with no existing procedure: real work
            # (a real trace), automatic learning, a private candidate.
            # Nothing here hand-seeds the candidate.
            # =============================================================
            before_ids = {r["id"] for r in store_a.list_local_procedures()}
            assert not any(
                r["name"].strip().lower() == NEW_TASK_GOAL.strip().lower()
                for r in store_a.list_local_procedures()
            ), "fixture sanity: no existing row already IS this procedure (exact-goal)"

            live_traces = root / "live-traces"
            live_traces.mkdir()
            _trace_session(live_traces, "new-task-1.jsonl", NEW_TASK_GOAL)

            sweep1 = run_local_learning_sweep(store_a, str(live_traces), max_sessions=5)
            assert sweep1["captured"] == 1 and sweep1["errors"] == 0, sweep1

            new_rows = [r for r in store_a.list_local_procedures() if r["id"] not in before_ids]
            assert len(new_rows) == 1, new_rows
            learned = store_a.get_local_procedure(new_rows[0]["id"])
            learned_id = learned["id"]
            assert learned["verification_state"] == "candidate"
            assert learned["staleness"] == "fresh"
            lrefs = learned["evidence_refs"]
            assert lrefs and all(r["privacy"] == "local" for r in lrefs)
            assert lrefs[0]["source_type"] == "claude_code_trace"
            assert lrefs[0].get("evidence_status") == "executed"

            # =============================================================
            # STAGE 4 -- candidate trial: genuine repeated evidence across
            # distinct contexts drives the discovered candidate to verified
            # through the real threshold arithmetic (no raw write).
            # =============================================================
            v_learned = _verify_local(store_a, learned_id)
            assert v_learned["verification_stats"]["successes"] >= MIN_SUCCESSES_FOR_VERIFIED

            # =============================================================
            # STAGE 5 -- additional compatible experiences: a second real
            # session for the SAME goal is AUTOMATICALLY converged onto the
            # existing row (multi-episode generalization), provenance kept,
            # no duplicate born.
            # =============================================================
            refs_before = len(store_a.get_local_procedure(learned_id)["evidence_refs"])
            eps_before = len(store_a.get_local_procedure(learned_id)["source_episode_ids"])
            rows_before = len(store_a.list_local_procedures())

            _trace_session(live_traces, "new-task-2.jsonl", NEW_TASK_GOAL)
            sweep2 = run_local_learning_sweep(store_a, str(live_traces), max_sessions=5)
            assert sweep2["merged"] == 1 and sweep2["captured"] == 0, sweep2

            merged = store_a.get_local_procedure(learned_id)
            assert len(store_a.list_local_procedures()) == rows_before, "no duplicate row"
            assert len(merged["evidence_refs"]) == refs_before + 1
            assert len(merged["source_episode_ids"]) == eps_before + 1
            assert merged["verification_state"] == "verified", (
                "an already-verified procedure stays verified when a new "
                "compatible episode is merged in"
            )

            # =============================================================
            # STAGE 6 -- a relevant fact changes: a procedure goes stale
            # and SELECTION CHANGES -- it is excluded at the hard gate
            # (non-compensatory: disqualified, not merely ranked lower).
            # =============================================================
            stale_one = store_a.capture_local_procedure(
                name=f"{PFX} rotate the edge TLS certificates",
                goal="rotate the edge certs before expiry",
                steps=[{"order": 0, "action": "issue new certs"},
                       {"order": 1, "action": "reload the edge fleet"}],
                provenance="system_pending_review",
                scope_type="user", scope_entity_id=f"{PFX}-ws-a",
            )
            stale_id = stale_one["id"]
            _verify_local(store_a, stale_id)

            fresh_row = store_a.get_local_procedure(stale_id)
            assert check_local_hard_constraints(
                fresh_row, current_scope={}, require_verified=True, environment_facts=[],
            ).applicable is True
            assert rank_unified_candidates(
                [fresh_row], [], current_scope={}, require_verified=True,
                environment_facts=[], limit=10,
            ), "fixture sanity: selectable while fresh"

            store_a.mark_local_procedure_stale(
                row_id=stale_id,
                reason=f"{PFX}: the edge cert authority migrated -- prior rotation steps no longer valid",
            )
            staled = store_a.get_local_procedure(stale_id)
            assert staled["staleness"] == "stale"

            gate_after = check_local_hard_constraints(
                staled, current_scope={}, require_verified=True, environment_facts=[],
            )
            assert gate_after.applicable is False
            assert gate_after.failed_constraints == ["staleness"]
            assert rank_unified_candidates(
                [staled], [], current_scope={}, require_verified=True,
                environment_facts=[], limit=10,
            ) == [], "a stale procedure drops out of selection entirely"

            # =============================================================
            # STAGE 7 -- explicit publish: a verified private procedure
            # becomes a SCRUBBED global CANDIDATE. No local verification
            # count is inherited.
            # =============================================================
            _verify_local(store_a, pub_id)  # pub is now a verified local procedure
            published = await publish_local_procedure(
                pool, local_store=store_a, local_row_id=pub_id,
                published_by=USER_A, scope_type="global",
            )
            gid = published["id"]
            fresh_global = await get_procedure(pool, gid)
            assert fresh_global["verification_state"] == "candidate", (
                "a freshly published row is a real global candidate, never "
                "auto-verified because the local copy was verified"
            )
            assert fresh_global["approval_status"] == "proposed"
            assert fresh_global["created_by"] == USER_A and fresh_global["owner_id"] == USER_A
            assert fresh_global["verification_stats"]["attempts"] == 0, (
                "zero inherited evidence: the local track record does not "
                "carry onto the global row"
            )
            blob = json.dumps(dict(fresh_global), default=str)
            assert f"{PFX}-lctx-0" not in blob, "local context keys must not leak on publish"
            assert str(root) not in blob, "local filesystem paths must not leak on publish"

            # =============================================================
            # STAGE 8 -- User B: global retrieval -> local applicability
            # -> real execution -> independent evidence. User B is a
            # distinct identity and only ever sees the global row.
            # =============================================================
            scope_b = AccessScope.for_user(USER_B)
            pre = await find_applicable_procedures(
                pool, access_scope=scope_b, require_verified=True, **WIDE,
            )
            assert gid not in {str(h["id"]) for h in pre}, (
                "an unverified global candidate must not be automatically "
                "selectable by User B yet"
            )

            gupd = None
            for i in range(MIN_SUCCESSES_FOR_VERIFIED):
                gupd = await record_execution_outcome(
                    pool, procedure_row_id=gid, success=True,
                    context_key=f"{PFX}-gctx-{i % MIN_DISTINCT_CONTEXTS_FOR_VERIFIED}",
                    owner_id=USER_A,
                )
            assert gupd["verification_state"] == "verified"
            await approve_procedure(pool, procedure_row_id=gid, approved_by=USER_A)

            hits_b = await find_applicable_procedures(
                pool, access_scope=scope_b, require_verified=True, **WIDE,
            )
            found = next((h for h in hits_b if str(h["id"]) == gid), None)
            assert found is not None, (
                "User B's real find_applicable_procedures call must surface "
                "the independently-verified, approved global procedure"
            )

            compiled = compile_plan(
                procedure_id=found["procedure_id"],
                procedure_version=found["version"],
                procedure_row_id=found["id"],
                procedure_payload=dict(found),
                task_description=f"{PFX}: User B's own real run",
                scope_type="global",
                extractor_version="test_ideal_v1_lifecycle_e2e@1",
                created_by=USER_B,
                owner_id=USER_B,
                nodes=[{"order": 0, "goal": "promote the canary once its error budget holds"}],
            )
            assert compiled.plan.created_by == USER_B

            b_ctx = f"{PFX}-user-b-own-{uuid4().hex[:6]}"
            await record_execution_outcome(
                pool, procedure_row_id=gid, success=True, context_key=b_ctx, owner_id=USER_B,
            )
            b_ev = await pool.fetch(
                "SELECT context_key, outcome_status FROM evidence WHERE target_type = 'procedure' "
                "AND target_id = $1::uuid AND owner_id = $2 AND t_invalid IS NULL",
                gid, USER_B,
            )
            assert len(b_ev) == 1 and b_ev[0]["context_key"] == b_ctx, (
                "the evidence stream carries exactly one row that is genuinely User B's own"
            )
            assert b_ev[0]["outcome_status"] == "success"

            # =============================================================
            # STAGE 9 -- privacy: User B cannot reach any of User A's
            # private repo / git / chat / trace / unpublished-candidate
            # material.
            # =============================================================
            assert store_b.list_local_procedures() == [], (
                "User B's private store is a separate file and starts empty"
            )
            a_private_names = boot_names | {
                store_a.get_local_procedure(learned_id)["name"],
                store_a.get_local_procedure(stale_id)["name"],
            }
            for name in a_private_names:
                assert await pool.fetchrow(
                    "SELECT id FROM procedures WHERE name = $1", name
                ) is None, f"User A's unpublished private material {name!r} must have no global row"

            b_view = await find_applicable_procedures(
                pool, access_scope=scope_b, require_verified=False, **WIDE,
            )
            assert not (a_private_names & {h["name"] for h in b_view}), (
                "User B's global search must never surface User A's private material"
            )
            crossed_blob = json.dumps(dict(await get_procedure(pool, gid)), default=str)
            assert "git_history" not in crossed_blob and "claude_code_trace" not in crossed_blob, (
                "the published row carries none of User A's private bootstrap "
                "source records into the commons"
            )

            # =============================================================
            # STAGE 10 -- a real failure: recorded as evidence, routed by
            # class, and it inflates NOTHING (success count flat, trust
            # not raised).
            # =============================================================
            before_stats = await pool.fetchrow(
                "SELECT attempts, successes, failures FROM procedure_evidence_stats "
                "WHERE procedure_row_id = $1::uuid", gid,
            )
            await record_execution_outcome(
                pool, procedure_row_id=gid, success=False,
                context_key=f"{PFX}-real-failure", failure_class="environment_changed",
                owner_id=USER_B,
            )
            after_stats = await pool.fetchrow(
                "SELECT attempts, successes, failures FROM procedure_evidence_stats "
                "WHERE procedure_row_id = $1::uuid", gid,
            )
            assert after_stats["successes"] == before_stats["successes"], (
                "a recorded failure must never increase the success count"
            )
            assert after_stats["attempts"] == before_stats["attempts"] + 1
            assert after_stats["failures"] == before_stats["failures"] + 1, (
                "migration 34: a recorded failure is a real, counted attempt"
            )
            post_fail = await get_procedure(pool, gid)
            assert post_fail["verification_stats"]["successes"] == \
                gupd["verification_stats"]["successes"] + 1, (
                "the User B success in stage 8 counts; the failure does not"
            )

            fail_ev = await pool.fetchrow(
                "SELECT * FROM evidence WHERE target_type = 'procedure' AND target_id = $1::uuid "
                "AND outcome_status = 'failure' AND context_key = $2 AND t_invalid IS NULL",
                gid, f"{PFX}-real-failure",
            )
            assert fail_ev is not None
            outcome = await classify_and_route(pool, dict(fail_ev))
            assert outcome.classification.route == "dependency_queue", outcome.classification
            queue = await fetch_route_queue(pool, "dependency_queue", limit=_WHOLE_QUEUE)
            assert any(str(r["evidence_id"]) == str(fail_ev["id"]) for r in queue), (
                "the classified failure must land in its readable route queue"
            )
        finally:
            await _cleanup(pool)
            await pool.close()
            tmp.cleanup()

    asyncio.run(_run())
