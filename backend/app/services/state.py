"""
State projection (ticket 10, memory-substrate map): no state table.
State is a read-time query over the claim graph -- "state at T = facts
whose validity interval contains T", the event-calculus frame axiom
operationalized as a query, backed by the real GiST index
(db/16_state_projection_index.sql).

Filters BOTH axes deliberately: t_valid/t_invalid (bi-temporal existence
-- is this row still the live version) AND truth_state (epistemic belief
-- do we still believe it, per claims.py's relate_claims()). A claim
that's been SUPERSEDED/CONTRADICTED keeps t_invalid NULL (the row still
exists, per claims.py's own real design -- "what did we once believe" and
"what do we believe now" stay separately answerable) but flips
truth_state to 'OUT'. For state-projection purposes that must be treated
the same as "no claim found" -- matching ticket 10's own closed-world
stance and ticket 12's fail-closed precondition handling ("no claim
found" and "precondition not satisfied" are the same answer under CWA).

Granularity is not a storage decision (ticket 10's own words): an
episode's state and a procedure execution's state are two evaluations of
the SAME function below, just with a different `subjects` scope --
episode-level scope from the episode's domain_payload (ticket 02),
procedure-execution scope from the procedure's required_state (ticket
05). Ticket 12's applicability(P, S_current) is meant to call this same
function too, with P.required_state as scope and now() as the timestamp.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional, Union

import asyncpg
from pydantic import BaseModel

from app.services.access import AccessScope, visibility_predicate


class GitSha(BaseModel):
    """A git-native, content-addressed reference -- commits, blobs --
    already immutable and addressable by the git object itself, no
    separate storage needed."""
    kind: Literal["git_sha"] = "git_sha"
    sha: str
    repo: Optional[str] = None


class BlobUri(BaseModel):
    """A large artifact (tool output, screenshot, etc.) too big to
    inline -- same real idiom episodes.content_ref already uses, named
    explicitly here rather than left as an untyped string. CAS-style:
    the URI plus its own content hash, so a moved/re-hosted blob is
    still verifiable against the recorded hash."""
    kind: Literal["blob_uri"] = "blob_uri"
    uri: str
    content_hash: Optional[str] = None


class DbId(BaseModel):
    """A reference to another row in this database -- a claim, an
    observation, a trace_event -- named by table and id rather than a
    bare UUID with no indication of what it points at."""
    kind: Literal["db_id"] = "db_id"
    table: str
    id: str


ArtifactRef = Union[GitSha, BlobUri, DbId]
"""
Ticket 10's own explicit requirement: "the reference must be a typed
union that names its addressing scheme... rather than an untyped string
mixing modes" -- mixing addressing modes without a discriminator is what
produces cache-invalidation bugs and unresolvable provenance. Real type,
not yet wired into any write path -- no current caller needs an artifact
reference field, so this is available for whoever builds the first one
(most likely evidence/procedure-execution records) rather than forced
into a field nothing populates yet.
"""


async def project_state(
    pool: asyncpg.Pool,
    *,
    subjects: list[str],
    as_of: Optional[datetime] = None,
    scope: Optional[AccessScope] = None,
) -> list[dict[str, Any]]:
    """
    Real, direct implementation of ticket 10's core decision. Returns
    every currently-believed claim (truth_state='IN') whose validity
    interval contains `as_of` (defaults to now()), for each subject
    given.

    Closed-world, deliberately: a subject with no matching claim simply
    doesn't appear in the result -- no null sentinel, no special-casing.
    If "unknown" (as distinct from "false"/"absent") is ever needed, per
    ticket 10's own decision it becomes an explicit status value on a
    claim, never introduced here as a NULL-shaped return.

    `scope` is access-scoped per ticket 09's non-negotiable rule ("every
    new query goes through access.py's visibility_predicate() -- no
    hand-written filters, no exceptions"). knowledge_nodes is one of the
    four tables 03_access.sql already covers, so a claim's real
    visibility/owner_id columns exist; this function was simply never
    filtering on them. Defaults to unrestricted() to preserve the
    previous (internal-caller) behaviour rather than silently break
    existing callers -- request paths must pass a real scope, same
    convention as GraphStore.
    """
    if as_of is None:
        as_of = datetime.now(timezone.utc)
    if not subjects:
        return []

    scope = scope or AccessScope.unrestricted()
    vis_sql, vis_params = visibility_predicate(scope, param_index=3)

    rows = await pool.fetch(
        "SELECT id, properties, t_valid, t_invalid FROM knowledge_nodes "
        "WHERE node_type = 'claim' "
        "AND properties->>'subject' = ANY($1::text[]) "
        "AND properties->>'truth_state' = 'IN' "
        "AND t_valid <= $2 "
        "AND (t_invalid IS NULL OR t_invalid > $2) "
        f"AND {vis_sql}",
        subjects, as_of, *vis_params,
    )
    return [
        {
            "id": str(r["id"]),
            "subject": r["properties"].get("subject"),
            "predicate": r["properties"].get("predicate"),
            "object": r["properties"].get("object"),
            "properties": dict(r["properties"]),
            "t_valid": r["t_valid"],
            "t_invalid": r["t_invalid"],
        }
        for r in rows
    ]


async def state_delta(
    pool: asyncpg.Pool,
    *,
    subjects: list[str],
    before: datetime,
    after: datetime,
    scope: Optional[AccessScope] = None,
) -> dict[str, list[dict[str, Any]]]:
    """
    Computed on demand, never stored (ticket 10's own decision):
    state_delta is the set difference between two evaluations of
    project_state() at two timestamps, not a separately maintained log.

    Returns 'added' (claims live at `after` but not at `before`) and
    'removed' (claims live at `before` but not at `after`), keyed by
    claim id. A claim present at both timestamps is unchanged and
    appears in neither list -- this is a delta, not a snapshot pair.

    `scope` threaded through to both project_state() calls -- a delta
    computed from two differently-scoped snapshots would leak existence
    information (a claim "disappearing" because the viewer lost
    visibility to it, not because it stopped being true).
    """
    before_state = await project_state(pool, subjects=subjects, as_of=before, scope=scope)
    after_state = await project_state(pool, subjects=subjects, as_of=after, scope=scope)

    before_ids = {c["id"] for c in before_state}
    after_ids = {c["id"] for c in after_state}

    return {
        "added": [c for c in after_state if c["id"] not in before_ids],
        "removed": [c for c in before_state if c["id"] not in after_ids],
    }
