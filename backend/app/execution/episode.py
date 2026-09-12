"""
Local Episode[H] glue: one execution_runs row <-> one episodes row.

Local execution's Event[H]/Trace[H] machinery already exists and is real
(`execution_run_events`, ordered by `seq`, keyed by `execution_run_id` --
migrations 61/63/70/72). What was missing was the Episode[H] boundary
itself: a bounded, semantically coherent piece of work with a start, an
end, an outcome, and a place for the common Observation/Claim/Evidence/
Procedure machinery to anchor its provenance (schema.md's Episode[H]).
This module is exactly that seam and nothing else -- it does not
re-implement Event/Trace capture, and it does not touch Observation/
Claim/Evidence/Procedure creation (that lives in
`app.services.ingestion_jobs::handle_consolidate_local_episode`, which
reads what this module writes).

`open_episode_for_run()` is called once, inside the SAME transaction as
`execution_runs`' own row creation (`durable_run.start_run()`) -- an
Episode never exists without the run it describes, and vice versa.

`close_episode_for_run()` is called once, inside the SAME transaction as
the run's terminal status UPDATE (`durable_run._persist_success_transition`
/ `_finalize` / `finalize_after_verification`), and ONLY from inside those
callers' own `tag != "UPDATE 0"` guard -- so a duplicate finalize (retry,
duplicate `verify_completion`) never even reaches this function twice in
the common case. The `end_ts IS NULL` guard below is a second,
independent line of defense: idempotent even if ever called directly.
"""
from __future__ import annotations

from typing import Any, Optional

_Executor = Any  # asyncpg.Connection or asyncpg.Pool -- same convention as recorder.py


async def open_episode_for_run(
    conn: _Executor,
    run_id: str,
    *,
    created_by: Optional[str] = None,
    scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    parent_run_id: Optional[str] = None,
) -> str:
    """Create the `episodes` row for a freshly-created execution_run, or
    return the existing one. Idempotent via the partial unique index on
    `episodes(execution_run_id)` (migration 79) -- a concurrent double
    call for the same run_id (e.g. `start_run`'s own UniqueViolationError
    retry path) is a real possibility, not just a defensive guard.
    """
    parent_episode_id = None
    if parent_run_id is not None:
        parent_episode_id = await conn.fetchval(
            "SELECT id FROM episodes WHERE execution_run_id = $1", parent_run_id,
        )

    # B19: local execution starts PRIVATE, never implicitly public -- the
    # same rule already enforced at every other local-learning write site
    # in this codebase (report_execution's extraction call, close_
    # exploration's Claim capture). Only when there is no caller identity
    # at all does this fall back to 'public': there is no owner to scope
    # a private row to, and the V0 gate already refuses an owner-less
    # private write elsewhere -- this mirrors that, rather than inventing
    # a different rule here.
    visibility = "private" if created_by else "public"
    project_id = scope_entity_id if scope_type in ("project", "repository") else None

    episode_id = await conn.fetchval(
        """
        INSERT INTO episodes (
            episode_type, execution_run_id, session_id, start_ts,
            owner_id, visibility, scope_type, scope_entity_id, project_id,
            parent_episode_id, metadata
        )
        VALUES ('execution', $1, $2, now(), $3, $4, $5, $6, $7, $8, '{}'::jsonb)
        ON CONFLICT (execution_run_id) WHERE execution_run_id IS NOT NULL DO NOTHING
        RETURNING id
        """,
        run_id, trace_id, created_by, visibility, scope_type, scope_entity_id,
        project_id, parent_episode_id,
    )
    if episode_id is None:
        episode_id = await conn.fetchval(
            "SELECT id FROM episodes WHERE execution_run_id = $1", run_id,
        )
    return str(episode_id)


async def close_episode_for_run(
    conn: _Executor, run_id: str, *, outcome: str,
) -> Optional[str]:
    """Close the run's Episode exactly once (schema.md: 'a terminal
    Episode MUST NOT be reopened'). Returns the episode id iff THIS call
    is the one that actually closed it; returns None if the episode was
    already closed (duplicate finalize -- the caller must not re-enqueue
    consolidation in that case) or was never opened (a run created before
    migration 79 landed, or a caller that skipped `open_episode_for_run`).
    """
    episode_id = await conn.fetchval(
        """
        UPDATE episodes
           SET end_ts = now(),
               metadata = metadata || jsonb_build_object('outcome', $2::text)
         WHERE execution_run_id = $1 AND end_ts IS NULL
        RETURNING id
        """,
        run_id, outcome,
    )
    return str(episode_id) if episode_id is not None else None
