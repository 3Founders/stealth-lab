"""
`app.stealth` -- the `.stealth/` local working-set projection.

G13 / spec A11-A12 / B35, extended per the ratified local-architecture
decision (recorded as a deliberate frozen-spec deviation -- see the board
note): the local side is a **filesystem-native working set**, not a
second database. Global Postgres stays authoritative for semantic
retrieval, the Claim graph, ranking, permissions, and publication; by the
time knowledge reaches `.stealth/` the candidate set is already small
enough that `grep` + exact line-range reads replace a query engine.

Layout produced by `app.stealth.generator.generate_projection`:

    .stealth/
      context.md              -- compact B35 router (kept for compatibility)
      run.json                -- machine-readable current run (kept)
      meta.json               -- revisions + cursors + staleness signal
      claims.md               -- addressable Claim blocks (working set only)
      procedures.md           -- addressable Procedure blocks
      implementations.md      -- addressable Implementation blocks
      run.md                  -- richer per-node execution state
      index/
        root.idx              -- the tiny router: name|target|hint
        claims.idx            -- id|version|scope|status|tags|file|start|end|summary
        procedures.idx
        implementations.idx
        run.idx               -- node_id|status|owner|deps|globs|file|start|end|summary

Navigation model (a "knowledge page fault" when local misses -- P3):

    need knowledge -> grep index/*.idx -> object id + exact line range
    -> sed -n 'start,end p' <file>.md -> continue

Everything here is a pure, idempotent regeneration from canonical state.
Nothing in this codebase reads `.stealth/` back and trusts it as truth;
a missing or stale projection is always rehydrated from Postgres.
"""
from app.stealth.errors import StealthProjectionError
from app.stealth.generator import generate_projection

__all__ = ["StealthProjectionError", "generate_projection"]
