"""
V1 contribution + verification + ranking + Credits system (migration 102).

Deliberately a separate package from app/services -- this is one cohesive
new feature area (submission review, usage/verification signals, Credits),
not a grab-bag of unrelated helpers, so it gets its own directory the same
way app/execution/ and app/mcp_server/ already do for their feature areas.

Modules:
  constants.py    reward amounts, caps, layer thresholds -- configuration,
                   never inlined into the logic that uses them.
  duplicates.py   near-duplicate scoring for a new submission, reusing the
                   existing pgvector cosine-similarity pattern.
  submissions.py  procedure + benchmark submission create/review, Layer 1
                   (deterministic) checks.
  verification.py Layer 2 (LLM/heuristic) evaluation and usage-event
                   recording (self-use vs independent reuse).
  ranking.py       contextual, per-goal procedure ranking (Wilson lower
                   bound on real evidence -- no new statistics engine).
  standing.py      contributor Standing -- computed on read, nothing stored
                   (same philosophy as app/services/contributors.py).
  credits.py       the append-only Credits ledger: rewards, balance,
                   history, caps, clawback.

Nothing here talks to Postgres directly except through asyncpg.Pool passed
in by the caller, matching every other service module in this codebase.
"""
