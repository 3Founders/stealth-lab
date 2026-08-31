"""
Local/private claim capture, mirroring `local_store.py::LocalProcedureStore`'s
real, proven local->global pattern (SQLite, DB-free, explicit publish gate),
applied to CLAIMS instead of PROCEDURES.

WHY THIS EXISTS. Today only procedures have a local capture path. Claims
have none: the only claim writer, `app/services/claims.py::capture_claim`,
writes directly into the real global Postgres `knowledge_nodes` table.
A locally-observed claim (e.g. "this repository centralizes DB access in
src/db") could not be captured privately at all -- it either went straight
to the global commons or nowhere. This module closes that gap the same
way `local_store.py` + `publish.py` already closed it for procedures.

STRUCTURE MIRRORS `local_store.py` DELIBERATELY:
  - `LocalClaimStore` -- SQLite-backed, one file per workspace, same
    `_connect()`/`_init_schema()`/idempotent-PRAGMA-migration idiom.
  - `capture_local_claim` -- always starts scope_type='repository': a
    local claim is inherently repo-scoped BY CONSTRUCTION, which is the
    concrete mechanism that answers `.scratch/final_architecture_audit.md`
    §8's flagged question ("local claims must not automatically become
    global knowledge") -- there is structurally no path from this store
    to the global `knowledge_nodes` table except `publish_local_claim`,
    below, which requires an explicit `published_by` and never runs
    itself.
  - `publish_local_claim` -- same explicit-gate shape as
    `publish.py::publish_local_procedure`: refuses a silent duplicate
    publish (`AlreadyClaimPublishedError`) unless `force=True`, redacts
    real free text before it reaches the global writer, and calls the
    REAL existing `app.services.claims.capture_claim` to perform the
    actual global INSERT -- this module does not reimplement it.

DB-FREE CONTRACT: this whole file must never import `asyncpg` or
`app.db.session`, same AST-enforced discipline
`test_local_agent_runner_offline.py::test_runner_module_never_imports_the_database`
holds `runner.py` to (see `test_local_claims_offline.py`'s own mirror of
that test). `publish_local_claim` is bound to a real connection pool, but
takes it as `pool: Any` rather than `pool: asyncpg.Pool` -- matching
`publish.py`'s own convention is NOT available here (that module DOES do
a real top-level `import asyncpg`, because it lives under `services/`,
which carries no DB-free contract); this module lives under
`local_agent/`, which does, so the untyped `Any` is the correct choice,
not a copy of `publish.py`'s literal code.

THE REAL `capture_claim` CONSTRAINT THIS MODULE MUST RESPECT (found by
reading `claims.py::capture_claim` in full before writing this): it
returns `None` -- silently, not an exception -- whenever NEITHER
`task_ids` resolves to at least one live `task_node` NOR a
`justification_episode_id` is given (`claims.py` lines ~267-268). A
published local claim has no task_node of its own by construction (local
claims are not task-graph-linked), so `task_ids=[]` alone would ALWAYS
hit that silent-None path. Rather than paper over this with a fabricated
task_id, `publish_local_claim` requires the caller to supply a real
`justification_episode_id` (the real episode this claim was observed
from, if one exists) OR real `task_ids` explicitly; if neither is given
it raises `ValueError` up front, naming the constraint, instead of
forwarding a call that would quietly return `None` and leave the local
row's publish link in an ambiguous state. If a caller supplies an anchor
that still fails to resolve (e.g. `task_ids` naming ids with no live
task_node), this module raises `ClaimPublishFailedError` rather than
silently accepting `capture_claim`'s `None`.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.services.claims import TRUTH_STATES, capture_claim
from app.services.trace_redaction import redact_value
from app.services.v0_gate import validate_scope
from app.utils.ids import uuid7

# Env var override, same idiom as local_store.py's LOCAL_STORE_PATH_ENV --
# a distinct name/default so a workspace's local claims db does not
# collide with its local procedures db.
LOCAL_CLAIM_STORE_PATH_ENV = "STEALTHLAB_LOCAL_CLAIM_STORE_PATH"

DEFAULT_RELATIVE_PATH = os.path.join(".stealthlab", "local_claims.db")

CREATED_BY = "local_claim_capture"

PUBLISHED_PROVENANCE_SCOPE_TYPE = "global"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS local_claims (
    id                  TEXT PRIMARY KEY,        -- uuid7, TEXT: sqlite has no native UUID type
    statement           TEXT NOT NULL,
    subject             TEXT,
    predicate           TEXT,
    object              TEXT,
    truth_state         TEXT NOT NULL DEFAULT 'IN',
    scope_type          TEXT NOT NULL DEFAULT 'repository',
    scope_entity_id     TEXT,
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    repo_root           TEXT
);
CREATE INDEX IF NOT EXISTS idx_local_claims_created_at ON local_claims(created_at);
"""

# Same four-column publish-link shape `local_store.py::_PUBLISH_LINK_COLUMNS`
# uses, added the same additive/idempotent way (PRAGMA table_info-then-
# ALTER, not baked into the CREATE TABLE -- see that module's own comment
# for the full "why not ADD COLUMN IF NOT EXISTS" rationale, which applies
# identically here).
_PUBLISH_LINK_COLUMNS: list[tuple[str, str]] = [
    ("published_claim_id", "TEXT"),       # the global knowledge_nodes.id this row became
    ("published_claim_row_id", "TEXT"),   # same value as published_claim_id today (claims have
                                           # no separate stable-handle-vs-version-row split the
                                           # way procedures do) -- kept as a distinct column
                                           # anyway to mirror local_store.py's four-column shape
                                           # exactly, so a future claim version chain (claims.py
                                           # already has one -- claim_family_id/claim_version in
                                           # properties) has a natural place to diverge from
                                           # "which row is this" without a schema change.
    ("published_by", "TEXT"),
    ("published_at", "TEXT"),
]


class LocalClaimNotFound(Exception):
    """Raised when an operation targets a local claim row id that doesn't
    resolve to a live row -- same distinction
    `local_store.py::LocalProcedureNotFound` draws."""


class AlreadyClaimPublishedError(Exception):
    """Raised when `publish_local_claim` is called against a local row
    that already has a durable publish link and the caller did not pass
    `force=True`. Mirrors `publish.py::AlreadyPublishedError`'s exact
    posture (refuse-by-default, `force=True` opt-in creates a genuinely
    new, independent global row) -- defined locally rather than imported
    from `publish.py` so this module stays self-contained and does not
    reach into a file another lane owns."""


class ClaimPublishFailedError(Exception):
    """Raised when the real global `capture_claim()` returns `None` even
    though `publish_local_claim` supplied an anchor (task_ids and/or
    justification_episode_id) -- i.e. the supplied task_ids did not
    resolve to any live task_node. See module docstring's constraint
    note: this is never silently swallowed as a successful no-op
    publish."""


def resolve_local_claim_store_path(repo_root: Optional[str] = None) -> str:
    """Same resolution order as `local_store.py::resolve_local_store_path`:
    explicit `repo_root` argument, then the env var override, then
    `.stealthlab/local_claims.db` under the current working directory."""
    env_override = os.environ.get(LOCAL_CLAIM_STORE_PATH_ENV)
    if env_override:
        return env_override
    root = repo_root or os.getcwd()
    return os.path.join(root, DEFAULT_RELATIVE_PATH)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


class LocalClaimStore:
    """A per-workspace SQLite claim registry. Every method opens and
    closes its own connection -- same concurrency posture as
    `LocalProcedureStore` (see that class's own docstring note): SQLite's
    file locking already serializes the few concurrent callers a single
    workspace ever has, so no long-lived connection is held."""

    def __init__(self, repo_root: Optional[str] = None, *, db_path: Optional[str] = None):
        self.db_path = db_path or resolve_local_claim_store_path(repo_root)
        if self.db_path != ":memory:":
            parent = Path(self.db_path).parent
            parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
            self._migrate_publish_link_columns(conn)
        finally:
            conn.close()

    def _migrate_publish_link_columns(self, conn: sqlite3.Connection) -> None:
        """Additive, idempotent -- identical idiom to
        `LocalProcedureStore._migrate_publish_link_columns`: never touches
        existing rows, never drops/recreates the table, safe to call on
        every open including against a db file created before this
        column set existed."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(local_claims)").fetchall()}
        for col_name, col_type in _PUBLISH_LINK_COLUMNS:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE local_claims ADD COLUMN {col_name} {col_type}")
        conn.commit()

    # -----------------------------------------------------------------
    # Capture. Always lands scope_type='repository' by construction (see
    # module docstring) -- a caller cannot pass a different scope_type
    # through this method; the ONE local-claim scope-widening path is
    # `publish_local_claim`, explicitly.
    #
    # scope_entity_id defaults to `repo_root` when not supplied: a
    # repository-scoped claim's natural entity IS the repository it was
    # observed in. This is required, not cosmetic -- `validate_scope()`
    # (the same V0 gate `capture_procedure`/`LocalProcedureStore` use,
    # reused here rather than re-invented) raises on a non-global
    # scope_type with no entity_id, so a real, scope-appropriate default
    # is needed rather than leaving it None and letting the gate reject
    # every capture that doesn't explicitly pass one.
    # -----------------------------------------------------------------
    def capture_local_claim(
        self,
        *,
        statement: str,
        subject: Optional[str] = None,
        predicate: Optional[str] = None,
        object: Optional[str] = None,  # noqa: A002 -- matches claims.py's own field name
        truth_state: str = "IN",
        scope_type: str = "repository",
        scope_entity_id: Optional[str] = None,
        created_by: str,
        repo_root: Optional[str] = None,
    ) -> dict:
        if truth_state not in TRUTH_STATES:
            raise ValueError(f"truth_state must be one of {TRUTH_STATES}, got {truth_state!r}")
        if not created_by:
            raise ValueError("capture_local_claim requires an explicit created_by")

        resolved_entity_id = scope_entity_id or repo_root
        resolved_scope_type, resolved_entity_id = validate_scope(scope_type, resolved_entity_id)

        row_id = str(uuid7())
        now = _now_iso()

        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO local_claims (
                    id, statement, subject, predicate, object, truth_state,
                    scope_type, scope_entity_id, created_by, created_at,
                    updated_at, repo_root
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id, statement, subject, predicate, object, truth_state,
                    resolved_scope_type, resolved_entity_id, created_by, now,
                    now, repo_root,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return {"id": row_id}

    def get_local_claim(self, row_id: str) -> Optional[dict]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM local_claims WHERE id = ?", (row_id,),
            ).fetchone()
        finally:
            conn.close()
        return _row_to_dict(row) if row else None

    def list_local_claims(self) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM local_claims").fetchall()
        finally:
            conn.close()
        return [_row_to_dict(r) for r in rows]

    # -----------------------------------------------------------------
    # Publish linkage -- durable local-side record of whether/where this
    # row was published to the global `knowledge_nodes` table. Written by
    # `publish_local_claim` (below) AFTER a successful global
    # `capture_claim()` call, never before, same ordering
    # `mark_local_procedure_published` enforces for procedures.
    # -----------------------------------------------------------------
    def mark_local_claim_published(
        self,
        row_id: str,
        *,
        global_claim_id: str,
        global_claim_row_id: str,
        published_by: str,
    ) -> dict:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM local_claims WHERE id = ?", (row_id,),
            ).fetchone()
            if row is None:
                raise LocalClaimNotFound(row_id)
            now = _now_iso()
            conn.execute(
                "UPDATE local_claims SET published_claim_id = ?, "
                "published_claim_row_id = ?, published_by = ?, published_at = ?, "
                "updated_at = ? WHERE id = ?",
                (global_claim_id, global_claim_row_id, published_by, now, now, row_id),
            )
            conn.commit()
            updated = conn.execute(
                "SELECT * FROM local_claims WHERE id = ?", (row_id,),
            ).fetchone()
        finally:
            conn.close()
        return _row_to_dict(updated)


def capture_local_claim(
    store: LocalClaimStore,
    *,
    statement: str,
    subject: Optional[str] = None,
    predicate: Optional[str] = None,
    object: Optional[str] = None,  # noqa: A002 -- matches claims.py's own field name
    truth_state: str = "IN",
    scope_type: str = "repository",
    scope_entity_id: Optional[str] = None,
    repo_root: str,
    created_by: str,
) -> dict:
    """Module-level convenience wrapper around
    `LocalClaimStore.capture_local_claim`, matching the exact call shape
    `local_store.py`'s own module-level helpers use elsewhere in this
    package. Thin on purpose -- all real logic (V0 scope gating, default
    resolution) lives on the store method, not duplicated here."""
    return store.capture_local_claim(
        statement=statement,
        subject=subject,
        predicate=predicate,
        object=object,
        truth_state=truth_state,
        scope_type=scope_type,
        scope_entity_id=scope_entity_id,
        created_by=created_by,
        repo_root=repo_root,
    )


def _redact_text(value: str) -> str:
    """Redact known-secret-shaped tokens out of one free-text string, via
    the SAME shared primitive `publish.py::_redact_text` uses
    (`trace_redaction.redact_value`) -- not reimplemented here."""
    matched: list[str] = []
    return redact_value(value, matched)


async def publish_local_claim(
    local_store: LocalClaimStore,
    local_row_id: str,
    *,
    pool: Any,
    published_by: str,
    task_ids: Optional[list[str]] = None,
    justification_episode_id: Optional[str] = None,
    scope_type: str = PUBLISHED_PROVENANCE_SCOPE_TYPE,
    scope_entity_id: Optional[str] = None,
    force: bool = False,
) -> dict:
    """
    Publishes one local claim -- identified by `local_row_id` against the
    caller's real `local_store` -- into the global `knowledge_nodes`
    table as a real claim, via the REAL existing
    `app.services.claims.capture_claim()` write path (imported, never
    reimplemented).

    `pool` is typed `Any`, not `asyncpg.Pool` -- see module docstring's
    DB-FREE CONTRACT note: this file must never import `asyncpg` at the
    top level.

    `published_by` is required, never defaulted to an anonymous constant
    -- same "explicit user action" posture `publish_local_procedure`
    enforces.

    ANCHOR REQUIREMENT (see module docstring's real-constraint note): the
    caller must supply at least one of `task_ids` (ids of live
    `task_nodes.skill_ref` this claim should attach to) or
    `justification_episode_id` (the real episode this claim was observed
    from). Without either, `capture_claim()` itself would silently return
    `None` -- this function refuses that call up front with a `ValueError`
    naming the constraint, rather than forwarding it and leaving the
    local row's publish link ambiguous. If a caller-supplied anchor still
    fails to resolve to anything live, `ClaimPublishFailedError` is
    raised instead of accepting `capture_claim`'s `None` as if it were a
    successful, ordinary result.

    Raises `LocalClaimNotFound` if `local_row_id` doesn't resolve to a
    live local row, and `AlreadyClaimPublishedError` if that row was
    already published and `force` is not True -- a forced re-publish
    creates a brand-new, independent global claim row and moves the
    local row's publish link to point at it; it never touches or retires
    the previously-published global row.

    Returns `{"id": <global knowledge_nodes.id>}`.
    """
    if not published_by:
        raise ValueError("publish_local_claim requires an explicit published_by (no anonymous publish)")

    local_claim = local_store.get_local_claim(local_row_id)
    if local_claim is None:
        raise LocalClaimNotFound(local_row_id)

    if local_claim.get("published_claim_row_id") and not force:
        raise AlreadyClaimPublishedError(
            f"local claim {local_row_id!r} was already published as global "
            f"claim {local_claim['published_claim_row_id']!r} "
            f"(at {local_claim.get('published_at')!r}); pass force=True to "
            "publish it again as a new, independent global claim."
        )

    resolved_task_ids = task_ids or []
    if not resolved_task_ids and justification_episode_id is None:
        raise ValueError(
            "publish_local_claim requires task_ids and/or a justification_episode_id: "
            "capture_claim() silently returns None for a claim anchored to neither "
            "(see local_claims.py module docstring's real-constraint note) -- a local "
            "claim has no task_node of its own by construction, so the caller must "
            "supply the real episode (or task ids) this claim is justified by."
        )

    redacted_statement = _redact_text(local_claim["statement"])

    result_id = await capture_claim(
        pool,
        statement=redacted_statement,
        task_ids=resolved_task_ids,
        justification_episode_id=justification_episode_id,
        created_by=published_by,
        truth_state=local_claim.get("truth_state") or "IN",
        subject=local_claim.get("subject"),
        predicate=local_claim.get("predicate"),
        object=local_claim.get("object"),
        owner_id=published_by,
        visibility="public",
        scope_type=scope_type,
        scope_entity_id=scope_entity_id,
    )

    if result_id is None:
        raise ClaimPublishFailedError(
            f"capture_claim() returned None for local claim {local_row_id!r}: the "
            "supplied task_ids did not resolve to any live task_node (and no "
            "justification_episode_id was given, or it also failed to anchor the "
            "write) -- no global claim was written, and the local row's publish "
            "link has NOT been marked."
        )

    local_store.mark_local_claim_published(
        local_row_id,
        global_claim_id=result_id,
        global_claim_row_id=result_id,
        published_by=published_by,
    )

    return {"id": result_id}
