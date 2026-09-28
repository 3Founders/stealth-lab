# Q-STEP7-OPENREWRITE-STATIC: may OpenRewrite recipes enter with a static check only?

**Raised:** 2026-09-29, from the read-only review of step 7 (`.scratch/ingestion/review_step_7.md`).
**Blocking:** only the OpenRewrite half of step 7. Node.js codemods are unaffected.

## The question
Step 7's spec says each codemod enters as a Procedure whose check is its **before/after tests, executed by a
sandboxed runner**. The OpenRewrite reader (`backend/app/services/ingestion_sources/openrewrite.py`) instead
marks recipes `check_tier="static"`: the recipe declaration is parsed and validated, but its `RewriteTest`
before/after tests are never executed. The reason is cost and dependencies: running them needs a JDK,
Gradle/Maven, network access for dependency resolution, and in some modules a Moderne token.

May OpenRewrite recipes be ingested with a static check, labelled as such, or must they wait for an executed check?

## Proposed default
Ingest them as **candidates only** (not verified Procedures): `check_tier="static"` is kept and surfaced, the
item cannot earn the verified status or appear as a verified solution, and it is excluded from anything served
as "checked" to routing. Revisit when step 4's sandboxed check runner can run a JVM job with pinned
dependencies and no network (a pre-fetched dependency cache).

## Related gaps found in the same review
- `license_metadata["commit"]` is always `None` for OpenRewrite. The license check must record the exact commit
  it read (hard rule 2: provenance).
- Check subprocesses run in place against the pinned checkout. They should run in an isolated temp copy.
