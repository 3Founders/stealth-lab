"""
Zero-to-docket in one command -- demo.md §3's two-phase story, for real,
on a fresh database, in one command, with no live model calls.

Phase A: build an AgentRunEvidenceSource by hand -- the exact same shape
server.py's solve_task() builds at its own extract_procedure() call site
(goal_text, outcome="success", a small realistic observations list,
tool_sequence, session_id) -- then call extract_procedure(pool,
evidence_source, client=None, ...). client=None takes the deterministic
extractor path (DeterministicExtractor, procedure_extraction/
strategies.py), so this phase makes zero API calls. A real row lands in
`procedures`, with real preconditions derived from project_state()
(procedure_extraction/derive.py), not hand-authored.

Phase B: pick one of the extracted procedure's real preconditions and
genuinely invalidate the knowledge claim behind it -- the same
SUPERSEDES-edge mechanism check_procedure's own offline tests exercise
(claims.py's relate_claims()) -- so the claim's truth_state actually
flips IN -> OUT. Then call check_procedure_reuse() (the same decision
core the MCP check_procedure tool wraps) both before and after: ALLOW
first (explicit invocation of a fresh, unverified procedure -- ticket
13's own named exception), WOULD_REFUSE after, citing the two real claim
ids, exactly demo.md §3's pinned shape.

HONEST FINDING, not papered over (see bootstrap_demo wave's own
blocking-question note): the MCP `retrieve_precedent` tool's real
implementation (app.services.reuse_detection._vector_candidates) only
ever searches `task_nodes`/`knowledge_nodes` -- it structurally cannot
return a `procedures` row, regardless of verification state. And even
the function that DOES retrieve procedures (find_applicable_procedures)
is cold-start-gated OFF whenever fewer than
MIN_VERIFIED_PROCEDURES_TO_ENABLE_RETRIEVAL verified procedures exist
system-wide (applicability.py's should_disable_procedure_retrieval) --
true by construction on a fresh database, for ANY freshly-extracted
(unverified) procedure, no matter which retrieval function is called.
"Reuse you can see" for a not-yet-verified procedure is therefore
necessarily EXPLICIT invocation (check_procedure_reuse, which
deliberately bypasses both gates -- CHECK_PROCEDURE_REQUIRE_VERIFIED),
not automatic retrieval -- this script demonstrates exactly that, and
also calls find_applicable_procedures() for real, printing its honest
(empty, cold-start-gated) result rather than pretending it "surfaced"
something it structurally can't yet.

Usage (from the backend/ directory, with a populated .env):
    python scripts/bootstrap_demo.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()  # same .env the main app reads via pydantic-settings --
                # no manual shell export needed on any platform

from app.db.session import create_pool
from app.services.access import AccessScope
from app.services.applicability import check_procedure_reuse, find_applicable_procedures
from app.services.claims import ClaimProperties, relate_claims
from app.services.embeddings import Embedder, to_pgvector
from app.services.environment_probe import assert_environment_claims
from app.services.procedure_extraction import extract_procedure
from app.services.procedure_extraction.evidence import AgentRunEvidenceSource
from app.services.state import project_state

PROJECT_ID = "bootstrap-demo-project"
SUBJECT = f"project:{PROJECT_ID}"
GOAL_TEXT = "Add a pagination cursor to the reports endpoint"
SESSION_ID = "bootstrap-demo-session"
CREATED_BY = "bootstrap_demo"

# Real observations from a real (small, deliberately simple) episode:
# touching a .py file makes `language` load-bearing, a test_run
# observation makes `has_test_runner` load-bearing (derive.py's
# load_bearing_predicates) -- both real signatures, not fabricated ones.
OBSERVATIONS = [
    {
        "observation_type": "file_touched",
        "label": "Added cursor param to the reports handler",
        "properties": {"file_path": "app/api/reports.py"},
    },
    {
        "observation_type": "test_run",
        "label": "pytest run against the touched module",
        "properties": {"command": "pytest tests/test_reports.py -q", "passed": True},
    },
]
TOOL_SEQUENCE = ["Read", "Edit", "Bash"]


def select_target_precondition(preconditions: list[dict]) -> dict:
    """Pure, DB-free: which of the extracted procedure's real
    preconditions Phase B tries to break. Prefers `has_test_runner` when
    present (a believable "the project's test runner changed" story);
    falls back to whichever precondition derive.py actually produced
    first, rather than refusing to run just because this particular repo
    probe didn't yield that one predicate."""
    if not preconditions:
        raise ValueError("select_target_precondition requires at least one precondition")
    return next(
        (p for p in preconditions if p.get("predicate") == "has_test_runner"),
        preconditions[0],
    )


def verdict_cites_both_claims(evidence: list[str], claim_a: str, claim_b: str) -> bool:
    """Pure, DB-free: does a WOULD_REFUSE verdict's `evidence` list cite
    both the original (superseded) claim id and the new (superseding)
    claim id -- demo.md §3's exact pinned proof shape."""
    blob = " ".join(evidence)
    return claim_a in blob and claim_b in blob


async def phase_a(pool, embedder) -> dict:
    """Traces become a real procedure. Returns the persisted `procedures`
    row (dict) -- not just the id -- so phase_b has real preconditions to
    read without a second round trip."""
    repo_root = os.path.join(os.path.dirname(__file__), "..")
    written = await assert_environment_claims(
        pool, project_id=PROJECT_ID, repo_root=repo_root, embedder=embedder,
    )
    print(
        f"Phase A: probed the real repo at {os.path.abspath(repo_root)} -> "
        f"{len(written)} new environment claim(s) written under {SUBJECT!r} "
        f"(idempotent: 0 is expected on a rerun against an unchanged repo)"
    )

    # The DB's own clock, not this process's -- project_state()'s
    # as_of filter (t_valid <= as_of) must never race a claim just
    # written moments ago on a different clock.
    episode_started_at = await pool.fetchval("SELECT now()")

    evidence_source = AgentRunEvidenceSource(
        goal_text=GOAL_TEXT, outcome="success", observations=OBSERVATIONS,
        tool_sequence=TOOL_SEQUENCE, started_at=episode_started_at,
        project_id=PROJECT_ID, session_id=SESSION_ID, steps_used=len(TOOL_SEQUENCE),
    )
    extraction = await extract_procedure(pool, evidence_source, client=None)
    if not extraction.procedure_id:
        print(f"Phase A FAILED: extraction refused -- {extraction.validation_failures}")
        sys.exit(1)

    procedure_row = await pool.fetchrow(
        "SELECT id, procedure_id, name, preconditions FROM procedures WHERE id = $1::uuid",
        extraction.version_row_id,
    )
    preconditions = procedure_row["preconditions"] or []
    print(
        f"Phase A: real procedure captured -- procedure_id={procedure_row['procedure_id']} "
        f"version_row_id={procedure_row['id']} name={procedure_row['name']!r}"
    )
    print(f"Phase A: {len(preconditions)} real precondition(s) derived from project_state(): "
          f"{preconditions}")
    if not preconditions:
        print(
            "Phase A produced a procedure with ZERO preconditions -- Phase B has nothing "
            "real to break. This means OBSERVATIONS above didn't end up load-bearing "
            "(derive.py's load_bearing_predicates) against whatever the environment probe "
            "actually found. Not silently fabricating a precondition to continue."
        )
        sys.exit(1)
    return dict(procedure_row)


async def phase_b(pool, embedder, procedure_row: dict) -> None:
    """Break a real precondition, get a real WOULD_REFUSE."""
    target = select_target_precondition(procedure_row["preconditions"])
    subject, predicate, expected_object = target["subject"], target["predicate"], target["object"]
    print(f"Phase B: targeting precondition subject={subject!r} predicate={predicate!r} "
          f"object={expected_object!r}")

    # ALLOW first, via EXPLICIT invocation (ticket 13's own named
    # exception -- CHECK_PROCEDURE_REQUIRE_VERIFIED=False) -- this is the
    # real "reuse you can see" proof for a fresh, unverified procedure.
    # See this file's module docstring for why automatic retrieval
    # (retrieve_precedent / find_applicable_procedures) cannot play that
    # role here: both are cold-start-gated off system-wide until >=1
    # VERIFIED procedure exists, which is never true immediately after a
    # fresh extraction, regardless of which retrieval function is called.
    # A real current_scope satisfying whatever `scope` derive_scope() put
    # on this procedure (language derived from the repo probe, ticket 12
    # is python here) -- without this, check_hard_constraints' scope gate
    # (which runs BEFORE the precondition cascade) would always fail
    # first against a real scoped procedure, hiding Phase B's actual
    # precondition-break story behind an unrelated scope mismatch.
    current_scope = {"language": ["python"]}
    before = await check_procedure_reuse(
        pool, procedure_id=procedure_row["procedure_id"], current_scope=current_scope,
        access_scope=AccessScope.unrestricted(),
    )
    print(f"Phase B: check_procedure BEFORE breaking anything -> {before.verdict} "
          f"({before.reason})")

    candidates = await find_applicable_procedures(pool, current_scope={}, require_verified=False)
    surfaced = any(str(p["id"]) == str(procedure_row["id"]) for p in candidates)
    print(
        f"Phase B: find_applicable_procedures() {'surfaces' if surfaced else 'does NOT surface'} "
        f"this procedure ({len(candidates)} candidate(s) total) -- honest result, not "
        f"claimed as proof either way; see module docstring's cold-start-gate note"
    )

    current_claims = await project_state(pool, subjects=[subject])
    original_claim = next(
        (c for c in current_claims if c["predicate"] == predicate and c["object"] == expected_object),
        None,
    )
    if original_claim is None:
        print(
            "Phase B FAILED: can't find the live claim backing this precondition via "
            "project_state() -- can't genuinely invalidate something that isn't there."
        )
        sys.exit(1)
    original_claim_id = str(original_claim["id"])

    # Genuinely invalidate it: a real, separately-captured claim stating
    # WHY the old one no longer holds, then claims.relate_claims()'s real
    # SUPERSEDES edge -- the same mechanism check_procedure's own offline
    # tests exercise (test_check_procedure_reuse_offline.py's
    # test_precondition_claim_superseded_names_both_claims_demo_md_style).
    #
    # Deliberately a DIFFERENT predicate than the one being invalidated,
    # not a re-probe asserting a new object for the SAME predicate:
    # applicability.py's own claim lookup (_precondition_narrative) finds
    # the newest LIVE claim for (subject, predicate) by t_valid DESC --
    # sharing the predicate would make that lookup find the NEW claim
    # instead of the superseded OLD one, hiding the exact "superseded by"
    # narrative this phase exists to prove end to end.
    retirement_statement = (
        f"{subject}: the {predicate}={expected_object} claim no longer holds -- "
        f"environment changed since it was asserted (bootstrap_demo Phase B)"
    )
    retirement_props = ClaimProperties(
        statement=retirement_statement, subject=subject,
        predicate=f"{predicate}_retired", truth_state="IN",
        claim_type="environment_fact_retraction", epistemic_status="observed",
    )
    retirement_embedding = await embedder.embed_one(retirement_statement, input_type="document")
    async with pool.acquire() as conn:
        retirement_claim_id = await conn.fetchval(
            "INSERT INTO knowledge_nodes "
            "(node_type, name, properties, embedding, created_by, provenance) "
            "VALUES ('claim', $1, $2, $3::vector, $4, 'company_ingested') RETURNING id",
            retirement_statement[:200], retirement_props.model_dump(exclude_none=True),
            to_pgvector(retirement_embedding), CREATED_BY,
        )
    retirement_claim_id = str(retirement_claim_id)

    await relate_claims(
        pool, from_claim_id=retirement_claim_id, to_claim_id=original_claim_id,
        relation="SUPERSEDES", created_by=CREATED_BY,
    )
    print(f"Phase B: real claim {original_claim_id} SUPERSEDED by real claim "
          f"{retirement_claim_id} (SUPERSEDES edge written, truth_state flipped IN -> OUT)")

    after = await check_procedure_reuse(
        pool, procedure_id=procedure_row["procedure_id"], current_scope=current_scope,
        access_scope=AccessScope.unrestricted(),
    )
    print(f"Phase B: check_procedure AFTER breaking the precondition -> {after.verdict}")
    print(f"  reason: {after.reason}")
    print(f"  evidence: {after.evidence}")
    print(f"  capability_note: {after.capability_note}")

    if after.verdict != "WOULD_REFUSE":
        print(
            "Phase B FAILED: expected WOULD_REFUSE after genuinely invalidating a "
            "precondition claim, got ALLOW."
        )
        sys.exit(1)
    if not verdict_cites_both_claims(after.evidence, original_claim_id, retirement_claim_id):
        print(
            "Phase B WARNING: WOULD_REFUSE fired, but `evidence` doesn't cite both real "
            "claim ids the way demo.md §3 expects -- reason/evidence above, check by hand."
        )

    print("\ndemo.md §3 AUDIT LOG LINE (the refusal, with real claim ids):")
    print(f"  WOULD_REFUSE {procedure_row['procedure_id']}: {after.reason}")


async def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL not found. Either:")
        print("  - set it in backend/.env, or")
        print("  - export it in your shell first")
        sys.exit(1)

    pool = await create_pool(database_url)
    embedder = Embedder()
    try:
        procedure_row = await phase_a(pool, embedder)
        await phase_b(pool, embedder, procedure_row)
    finally:
        await pool.close()

    print("\nbootstrap_demo.py: both phases complete on this run.")


if __name__ == "__main__":
    asyncio.run(main())
