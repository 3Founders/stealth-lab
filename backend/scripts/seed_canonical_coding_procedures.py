"""
Phase 1 (global-procedural-memory audit, .scratch/research/
global-procedural-memory-architecture-audit-2026-08-30.md, section L):
seed the first real canonical coding procedure corpus.

Honest about what this is NOT: this does not fabricate verification.
Every row lands via capture_procedure() exactly as any other caller would
-- verification_state='candidate', staleness='fresh', availability='active'
(schema defaults, ticket 13's "nothing is born verified" discipline,
same as every other real capture_procedure() caller in this codebase).
Preconditions are left empty rather than invented: these are genuine,
domain-general "how to work in a codebase" primitives, not repo-specific
rules with real checkable conditions yet -- exactly the honest gap
applicability.py's own DDL comment warns against filling with fabricated
structure. They earn `verified` the same way every other procedure does:
real reuse, via record_execution_outcome(), accruing real evidence.

Usage (from backend/):
    python scripts/seed_canonical_coding_procedures.py [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.session import create_pool
from app.services.procedures import capture_procedure

# Each procedure: (name, goal, [step goals]). Steps are planner-neutral
# goal descriptions (db/18_procedures.sql's own convention -- "never
# carries deps/requires scheduling fields"), not literal tool-call
# scripts. Composable primitives (per the audit's §D) reference each
# other only informally in goal text for now -- real subprocedure_ref
# composition is Phase 3 (needs a schema change), not this seed.
CANONICAL_PROCEDURES: list[tuple[str, str, list[str]]] = [
    ("explore_repo",
     "Build a working mental model of an unfamiliar repository's layout and purpose",
     ["List top-level directories and identify the primary language/framework",
      "Read the package manifest (package.json/pyproject.toml/go.mod/Cargo.toml)",
      "Identify the entrypoint(s) and how the project is normally run or tested"]),

    ("understand_architecture",
     "Identify the major components of a codebase and how they depend on each other",
     ["Locate architecture documentation if any exists (README, ARCHITECTURE.md, docs/)",
      "Identify the main module/package boundaries",
      "Trace one real request or data flow end to end through those boundaries"]),

    ("find_entrypoint",
     "Locate the concrete entrypoint(s) that start program execution",
     ["Search for main()/if __name__ == '__main__'/index.js-style entry conventions",
      "Check the manifest's declared scripts/bin/entrypoint fields",
      "Confirm by tracing what actually runs when the project's own start command is invoked"]),

    ("trace_feature",
     "Follow one named feature or behavior from its entrypoint to its implementation",
     ["Identify the user-facing trigger for the feature (a route, a CLI flag, an event)",
      "Follow the call chain from that trigger into the implementing code",
      "Note every file touched along the way"]),

    ("locate_relevant_code",
     "Find the specific files/functions relevant to a described task, in an unfamiliar repo",
     ["Search for literal keywords/identifiers from the task description",
      "Search for structurally related symbols (callers/callees of the first hits)",
      "Narrow to the smallest set of files that plausibly need to change"]),

    ("identify_change_surface",
     "Determine the full set of files that must change to implement a specific fix or feature",
     ["Locate the relevant code (see locate_relevant_code)",
      "Trace callers of anything that would change to catch indirect impact",
      "Check for tests, docs, or config that reference the same behavior"]),

    ("debug_failing_test",
     "Diagnose why a specific test is failing and identify the root cause",
     ["Run the failing test in isolation and capture the real error/traceback",
      "Read the test's own assertions to understand what it actually expects",
      "Trace backward from the failure point to the code that produced the wrong value",
      "Form a specific, falsifiable hypothesis for the root cause before changing anything"]),

    ("investigate_regression",
     "Determine what changed to cause previously-working behavior to break",
     ["Reproduce the regression with a minimal, real repro case",
      "Identify the last known-good state (a commit, a version, a config value)",
      "Bisect or diff between known-good and known-bad to narrow the causing change"]),

    ("review_pr",
     "Review a pull request for correctness, scope, and risk before approval",
     ["Read the PR description and confirm the diff actually matches its stated intent",
      "Check for untested edge cases in the changed logic",
      "Check whether the change's scope stayed within what the description claims"]),

    ("write_tests_for_existing_code",
     "Add test coverage for code that currently has none",
     ["Identify the code's real observable behavior by reading it, not guessing from its name",
      "Identify edge cases the current implementation may mishandle",
      "Write tests that would fail against a plausible wrong implementation, not just the current one"]),

    ("safe_refactor",
     "Change a piece of code's internal structure without changing its external behavior",
     ["Confirm real test coverage exists for the code being refactored before changing it",
      "Make the change in the smallest reviewable increments",
      "Re-run the existing tests after each increment, not only at the end"]),

    ("find_related_implementations",
     "Find other places in the codebase that solve a similar problem to the one at hand",
     ["Search for structurally similar function signatures or naming patterns",
      "Search for similar test fixtures, which often reveal similar production code",
      "Compare found candidates for whether they represent the SAME pattern or a coincidental match"]),

    ("implement_feature",
     "Implement a new feature in an existing codebase, from understanding to verified completion",
     ["Explore the repo and understand the relevant architecture (see explore_repo, understand_architecture)",
      "Identify the full change surface (see identify_change_surface)",
      "Implement the change",
      "Write or extend tests covering the new behavior",
      "Run the full relevant test suite and confirm it passes"]),

    ("fix_reported_bug",
     "Fix a bug described by a user report or issue, with a verifiable regression test",
     ["Reproduce the reported bug with a minimal real repro case",
      "Investigate the regression or root cause (see investigate_regression, debug_failing_test)",
      "Implement the fix",
      "Add a test that would have caught this bug before the fix existed",
      "Confirm the new test fails on the pre-fix code and passes on the post-fix code"]),

    ("add_dependency_safely",
     "Add a new third-party dependency to a project without breaking existing behavior",
     ["Check the dependency's license and maintenance status before adding it",
      "Add it via the project's real dependency manifest (not an ad hoc install)",
      "Run the full test suite after adding it to catch any transitive conflict"]),

    ("upgrade_dependency",
     "Upgrade an existing dependency to a newer version safely",
     ["Read the dependency's changelog between the current and target version for breaking changes",
      "Upgrade in the project's real manifest, one dependency at a time where feasible",
      "Run the full test suite and specifically re-check any code known to use that dependency's changed API"]),

    ("diagnose_flaky_test",
     "Determine why a test passes sometimes and fails other times",
     ["Run the test repeatedly in isolation to confirm it is genuinely flaky, not environment-specific",
      "Check for shared mutable state, timing assumptions, or unseeded randomness in the test or the code it exercises",
      "Form a specific hypothesis and confirm it by forcing the suspected condition deliberately"]),

    ("optimize_hot_path",
     "Improve the performance of a specific, measured hot path without changing its behavior",
     ["Measure the current real performance before changing anything",
      "Profile to find where time is actually spent, not where it is assumed to be spent",
      "Make one change at a time and re-measure after each"]),

    ("audit_security_surface",
     "Identify the security-relevant surface of a component (inputs, trust boundaries, secrets handling)",
     ["Identify every point where external/untrusted input enters the component",
      "Check how each input is validated or sanitized before use",
      "Check for secrets or credentials handled in that code path and how they are protected"]),

    ("write_migration",
     "Write a database schema migration that is safe to apply to a live system",
     ["Identify the exact schema change needed and whether it is additive or breaking",
      "Write the migration to be idempotent and safely re-runnable",
      "Confirm the migration does not require the application to be offline, if avoidable"]),

    ("reproduce_reported_issue",
     "Turn a vague user-reported problem into a concrete, minimal, reliable repro case",
     ["Extract every concrete detail from the report (versions, inputs, environment)",
      "Attempt the simplest possible reproduction first before adding complexity",
      "Confirm the repro is reliable by triggering it more than once"]),

    ("document_undocumented_behavior",
     "Write accurate documentation for existing code that currently has none",
     ["Read the actual implementation rather than inferring behavior from its name",
      "Identify real edge cases and failure modes by reading the code, not guessing",
      "Write documentation that describes what the code actually does, including its real limitations"]),

    ("triage_error_log",
     "Determine which errors in a large log/monitoring stream are worth investigating",
     ["Group errors by root cause signature, not just by message text",
      "Rank groups by real frequency and real impact, not by recency alone",
      "Identify which groups are new/regressions versus long-standing known issues"]),

    ("validate_config_change",
     "Confirm a configuration change behaves as intended before it reaches production",
     ["Identify every code path that reads the changed configuration value",
      "Test the change against both its old and new value to confirm the actual difference in behavior",
      "Check for any place the old value was hardcoded as a fallback or default"]),

    ("consolidate_duplicated_logic",
     "Identify and safely merge genuinely duplicated logic in a codebase",
     ["Confirm the duplicated implementations are actually equivalent, not superficially similar",
      "Identify every real caller of each duplicate before merging",
      "Merge behind the existing tests for all callers, re-running them after the merge"]),
]

DOMAIN = "coding"
PROVENANCE = "prior_library"  # curated reference set, same convention onboarding/seed.py uses
CREATED_BY = "seed_canonical_coding_procedures"


async def seed(dry_run: bool = False) -> None:
    if dry_run:
        for name, goal, steps in CANONICAL_PROCEDURES:
            print(f"[dry-run] would capture: {name} ({len(steps)} steps) -- {goal}")
        print(f"\n[dry-run] {len(CANONICAL_PROCEDURES)} procedures, none written")
        return

    pool = await create_pool()
    try:
        written = 0
        for name, goal, step_goals in CANONICAL_PROCEDURES:
            steps = [{"order": i, "goal": g} for i, g in enumerate(step_goals)]
            result = await capture_procedure(
                pool,
                name=name,
                goal=goal,
                steps=steps,
                provenance=PROVENANCE,
                domain=DOMAIN,
                domain_payload={"canonical": True, "seed_batch": "2026-08-30"},
                created_by=CREATED_BY,
                # NOT scope_type="global": capture_procedure()'s own real
                # behavior (procedures.py) falls back scope_entity_id to
                # `domain` when none is given -- combined with
                # scope_type="global" that trips v0_gate.py's real,
                # correct rule ("global scope cannot carry an entity_id").
                # scope_type="entity" is the honest fit: these procedures
                # are scoped to the coding domain entity, not literally
                # global-with-no-locality.
                scope_type="entity",
            )
            print(f"captured: {name} -> procedure_id={result['procedure_id']}")
            written += 1
        print(f"\n{written}/{len(CANONICAL_PROCEDURES)} procedures captured, "
              f"all verification_state='candidate' (schema default -- nothing "
              f"fabricated as verified)")
    finally:
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                         help="print what would be captured without touching the DB")
    args = parser.parse_args()
    asyncio.run(seed(dry_run=args.dry_run))
