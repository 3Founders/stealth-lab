"""Ingestion pipelines, rebuilt from scratch (2026-09-29) against docs/ingestion_sources_plan.md.

Each pipeline reads one source at a pinned revision and turns every item into knowledge through the core writers
(`capture_procedure`, `capture_claim`, `find_or_create_goal`, the trace writer, the evidence writer, the routing
store). What is new here is everything the old step 0-8 pipelines got wrong, enforced the same way for every
source:

  * one run at a time, on a named target; a run meant for the local database cannot reach any hosted database,
    and production needs an explicit, recorded approval (`common/target.py`);
  * the run refuses to start on pending migrations, on a stored vector made by a different embedding model than
    the configured one, or on a job queue nobody is draining (`common/preflight.py`);
  * every item is recorded in `ingest_ledger` with a closed reason code -- written, rejected by policy, or
    failed -- so nothing is lost silently, a killed run resumes where it stopped, and one identity is written once
    across sources (`common/ledger.py`);
  * held-out exclusion fails closed, the license is decided per item, and credit is recorded wherever a license
    asks for it.

Pipelines: `openhands` (SWE-rebench OpenHands trajectories), `skills` (SkillMD-138K).
"""
