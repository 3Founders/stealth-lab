"""
Phase 1 of the local/global runtime split (product spec, personal
procedure library): a SQLite-backed, file-local procedure registry --
NOT a second Postgres, NOT a shard of the global `procedures` table.

WHY SQLITE, NOT A SECOND POSTGRES STACK. The product spec is explicit:
"Do NOT duplicate the entire global PostgreSQL stack locally... prefer a
lightweight local store / file-backed procedure registry... do not
invent an unnecessary distributed database." A local agent's own
procedure library is small by construction (one workspace's worth of
private capability), needs zero cross-process coordination, and must
work with no server running and no `DATABASE_URL` set -- stdlib
`sqlite3`, one file, no new dependency.

WHY THE SCHEMA MIRRORS `procedures` (backend/db/18_procedures.sql).
Column NAMES match the real Postgres table exactly where the concepts
overlap, on purpose: this is the substrate a later "publish local ->
global" step (an explicitly separate, out-of-scope phase) can map onto
without a translation layer. This module does not implement publish and
must not be read as implying it exists yet.

WHAT IS DELIBERATELY NOT HERE.
  - No claims graph, no `project_state()`, no precondition-against-claims
    checking (applicability.py's `check_hard_constraints` does that
    against Postgres; a local SQLite file has no claims to project state
    from). local_applicability.py documents this gap explicitly rather
    than silently only checking a subset and calling it equivalent.
  - No approval_status / visibility / tenant scoping -- a private local
    procedure has exactly one viewer (the person running this agent
    against this workspace) by construction; those columns exist on
    `procedures` to police a SHARED commons this store is not.
  - No RRF fusion machinery, no pgvector -- cosine similarity is done in
    plain Python over however many rows this workspace has captured,
    which is never enough to need an ANN index.

CONCURRENCY: this module opens a fresh sqlite3.Connection per call
(WAL journal mode, a short busy_timeout) rather than holding one
long-lived connection -- a local agent process is not a connection-pooled
server, and SQLite's own file locking already serializes writers safely
across the few concurrent callers a single workspace ever has.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.services.procedures import (
    MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
    MIN_SUCCESSES_FOR_VERIFIED,
)
from app.services.v0_gate import V0Violation, validate_provenance, validate_scope
from app.utils.ids import uuid7

# Env var override for a user-level fallback location -- e.g. a caller
# with no real repo root (a bare directory, a CI sandbox) still gets a
# stable, documented place to persist a personal library across runs,
# rather than silently falling back to an in-process-only store.
LOCAL_STORE_PATH_ENV = "STEALTHLAB_LOCAL_STORE_PATH"

# Default relative location: `.stealthlab/` at the repo root -- sits next
# to the kind of directory a project already gitignores (`.venv/`,
# `node_modules/`), naturally per-workspace, and a caller can gitignore
# this one file if they choose without this module taking a position on
# their .gitignore.
DEFAULT_RELATIVE_PATH = os.path.join(".stealthlab", "local_procedures.db")

CREATED_BY = "local_procedure_capture"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS local_procedures (
    id                  TEXT PRIMARY KEY,       -- this version row's own id (uuid7, TEXT: sqlite has no native UUID type)
    procedure_id        TEXT NOT NULL,          -- stable handle across a version chain -- mirrors procedures.procedure_id
    name                TEXT NOT NULL,
    goal                TEXT NOT NULL,
    steps               TEXT NOT NULL DEFAULT '[]',        -- JSON array, mirrors procedures.steps
    preconditions       TEXT NOT NULL DEFAULT '[]',        -- JSON array, mirrors procedures.preconditions
    invariants          TEXT NOT NULL DEFAULT '[]',        -- JSON array, mirrors procedures.invariants
    exclusions          TEXT NOT NULL DEFAULT '[]',        -- JSON array, mirrors procedures.exclusions
    scope               TEXT NOT NULL DEFAULT '{}',        -- JSON object, mirrors procedures.scope
    evidence_refs        TEXT NOT NULL DEFAULT '[]',        -- JSON array, mirrors procedures.evidence_refs
    source_episode_ids   TEXT NOT NULL DEFAULT '[]',        -- JSON array, mirrors procedures.source_episode_ids
    provenance           TEXT NOT NULL,                     -- V0-gated, same PROVENANCE_VALUES as the global gate
    scope_type           TEXT NOT NULL,                     -- V0-gated, same SCOPE_TYPES as the global gate
    scope_entity_id      TEXT,
    verification_state   TEXT NOT NULL DEFAULT 'candidate', -- mirrors procedure_verification_state: candidate|verified|retired
    staleness             TEXT NOT NULL DEFAULT 'fresh',      -- mirrors procedure_staleness: fresh|stale|revalidating
    availability          TEXT NOT NULL DEFAULT 'active',     -- mirrors procedure_availability: active|quarantined|disabled
    verification_stats    TEXT NOT NULL DEFAULT '{}',         -- JSON object, same shape as procedures.verification_stats
    embedding              TEXT,                              -- JSON array of floats, or NULL -- plain Python cosine, no pgvector
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    t_invalid               TEXT                               -- ISO timestamp or NULL -- same bi-temporal invalidate-and-append convention as procedures.t_invalid
);
CREATE INDEX IF NOT EXISTS idx_local_procedures_procedure_id ON local_procedures(procedure_id);
CREATE INDEX IF NOT EXISTS idx_local_procedures_live ON local_procedures(t_invalid);
"""

_VERIFICATION_STATS_DEFAULT = {
    "attempts": 0,
    "successes": 0,
    "mean_steps": None,
    "distinct_contexts": 0,
    "context_keys_seen": [],
    "consecutive_failures": 0,
}


class LocalProcedureNotFound(Exception):
    """Raised when an operation targets a local procedure row id that
    doesn't resolve to a live row -- same distinction procedures.py's
    ProcedureNotFound draws (a caller error, not a silent no-op)."""


def resolve_local_store_path(repo_root: Optional[str] = None) -> str:
    """
    Resolution order, most-specific first:
      1. explicit `repo_root` argument (caller-supplied workspace root)
      2. `STEALTHLAB_LOCAL_STORE_PATH` env var (documented user-level
         fallback -- a full path to the db file, not a directory)
      3. `.stealthlab/local_procedures.db` under the current working
         directory (the common case: an agent invoked with cwd already
         set to the repo it's working in)

    Returns a path string; does not create the file or its parent
    directory -- `LocalProcedureStore.__init__` owns that side effect so
    a caller can call this purely to inspect where the store WOULD live.
    """
    env_override = os.environ.get(LOCAL_STORE_PATH_ENV)
    if env_override:
        return env_override
    root = repo_root or os.getcwd()
    return os.path.join(root, DEFAULT_RELATIVE_PATH)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for json_field in (
        "steps", "preconditions", "invariants", "exclusions", "scope",
        "evidence_refs", "source_episode_ids", "verification_stats",
    ):
        d[json_field] = json.loads(d[json_field]) if d[json_field] else (
            {} if json_field in ("scope", "verification_stats") else []
        )
    d["embedding"] = json.loads(d["embedding"]) if d["embedding"] else None
    return d


class LocalProcedureStore:
    """
    A per-workspace SQLite procedure registry. Every method opens and
    closes its own connection -- see module docstring's Concurrency note.
    """

    def __init__(self, repo_root: Optional[str] = None, *, db_path: Optional[str] = None):
        self.db_path = db_path or resolve_local_store_path(repo_root)
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
        finally:
            conn.close()

    # -----------------------------------------------------------------
    # Capture -- mirrors procedures.py::capture_procedure's real param
    # shape and defaults (candidate/fresh/active, provenance required).
    # -----------------------------------------------------------------
    def capture_local_procedure(
        self,
        *,
        name: str,
        goal: str,
        steps: Optional[list] = None,
        preconditions: Optional[list] = None,
        invariants: Optional[list] = None,
        exclusions: Optional[list] = None,
        scope: Optional[dict] = None,
        evidence_refs: Optional[list] = None,
        source_episode_ids: Optional[list[str]] = None,
        provenance: str,
        scope_type: str,
        scope_entity_id: Optional[str] = None,
        embedding: Optional[list[float]] = None,
    ) -> dict:
        """
        Inserts a new local procedure, always starting
        candidate/fresh/active -- same "nothing is born verified/trusted"
        posture as the global writer. V0-gated the same way
        capture_procedure() is (Rule 2: scope + provenance on everything
        entering storage) -- reuses the SAME validate_provenance/
        validate_scope the global gate uses, not a locally re-invented
        weaker check.

        Returns {"id": ..., "procedure_id": ...}, same shape as
        capture_procedure()'s return value.
        """
        validate_provenance(provenance)
        resolved_scope_type, resolved_scope_entity_id = validate_scope(
            scope_type, scope_entity_id,
        )

        row_id = str(uuid7())
        procedure_id = row_id  # first version of a new procedure: id == procedure_id, same convention capture_procedure's INSERT ... RETURNING id, procedure_id establishes implicitly for a fresh row
        now = _now_iso()

        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO local_procedures (
                    id, procedure_id, name, goal, steps, preconditions, invariants,
                    exclusions, scope, evidence_refs, source_episode_ids,
                    provenance, scope_type, scope_entity_id,
                    verification_state, staleness, availability, verification_stats,
                    embedding, created_at, updated_at, t_invalid
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id, procedure_id, name, goal,
                    json.dumps(steps if steps is not None else []),
                    json.dumps(preconditions if preconditions is not None else []),
                    json.dumps(invariants if invariants is not None else []),
                    json.dumps(exclusions if exclusions is not None else []),
                    json.dumps(scope if scope is not None else {}),
                    json.dumps(evidence_refs if evidence_refs is not None else []),
                    json.dumps(source_episode_ids if source_episode_ids is not None else []),
                    provenance, resolved_scope_type, resolved_scope_entity_id,
                    "candidate", "fresh", "active",
                    json.dumps(dict(_VERIFICATION_STATS_DEFAULT)),
                    json.dumps(embedding) if embedding is not None else None,
                    now, now, None,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return {"id": row_id, "procedure_id": procedure_id}

    def get_local_procedure(self, row_id: str) -> Optional[dict]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM local_procedures WHERE id = ?", (row_id,),
            ).fetchone()
        finally:
            conn.close()
        return _row_to_dict(row) if row else None

    def list_local_procedures(self, *, include_invalid: bool = False) -> list[dict]:
        conn = self._connect()
        try:
            if include_invalid:
                rows = conn.execute("SELECT * FROM local_procedures").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM local_procedures WHERE t_invalid IS NULL",
                ).fetchall()
        finally:
            conn.close()
        return [_row_to_dict(r) for r in rows]

    # -----------------------------------------------------------------
    # Outcome recording -- mirrors procedures.py::record_execution_outcome's
    # real counter/threshold logic. Reuses the SAME MIN_SUCCESSES_FOR_VERIFIED
    # / MIN_DISTINCT_CONTEXTS_FOR_VERIFIED constants imported from
    # procedures.py (Rule 6: no second copy of the same threshold).
    #
    # HONEST SCOPE NARROWER than the global writer: no evidence table (no
    # `evidence` rows are written -- there is no global evidence schema to
    # target locally), no circuit breaker / quarantine escalation, no
    # ChangeSet audit trail. What IS reused, verbatim, is the exact
    # promotion arithmetic (>=10 successes, 0 failures, across >=3
    # distinct contexts) so a local procedure's candidate->verified
    # transition means the same thing the global one does.
    # -----------------------------------------------------------------
    def record_local_execution_outcome(
        self,
        *,
        row_id: str,
        success: bool,
        context_key: str,
        steps_used: Optional[int] = None,
    ) -> dict:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM local_procedures WHERE id = ?", (row_id,),
            ).fetchone()
            if row is None:
                raise LocalProcedureNotFound(row_id)
            record = _row_to_dict(row)

            stats = record["verification_stats"] or {}
            for key, default in _VERIFICATION_STATS_DEFAULT.items():
                stats.setdefault(key, default if not isinstance(default, list) else list(default))

            stats["attempts"] = stats.get("attempts", 0) + 1
            if context_key not in stats["context_keys_seen"]:
                stats["context_keys_seen"].append(context_key)
            stats["distinct_contexts"] = len(stats["context_keys_seen"])

            if success:
                stats["successes"] = stats.get("successes", 0) + 1
                stats["consecutive_failures"] = 0
                if steps_used is not None:
                    prior_mean = stats.get("mean_steps")
                    prior_successes = stats["successes"] - 1
                    stats["mean_steps"] = (
                        steps_used if prior_mean is None or prior_successes == 0
                        else (prior_mean * prior_successes + steps_used) / stats["successes"]
                    )
            else:
                stats["consecutive_failures"] = stats.get("consecutive_failures", 0) + 1

            verification_state = record["verification_state"]
            total_failures = stats["attempts"] - stats["successes"]
            if (
                verification_state == "candidate"
                and total_failures == 0
                and stats["successes"] >= MIN_SUCCESSES_FOR_VERIFIED
                and stats["distinct_contexts"] >= MIN_DISTINCT_CONTEXTS_FOR_VERIFIED
            ):
                verification_state = "verified"

            now = _now_iso()
            conn.execute(
                "UPDATE local_procedures SET verification_stats = ?, verification_state = ?, "
                "updated_at = ? WHERE id = ?",
                (json.dumps(stats), verification_state, now, row_id),
            )
            conn.commit()

            updated = conn.execute(
                "SELECT * FROM local_procedures WHERE id = ?", (row_id,),
            ).fetchone()
        finally:
            conn.close()
        return _row_to_dict(updated)

    def mark_local_procedure_stale(self, *, row_id: str, reason: str) -> dict:
        """Same one-directional fresh->stale transition
        procedures.py::mark_procedure_stale makes, without the ChangeSet
        write (no global audit trail exists locally to write into)."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM local_procedures WHERE id = ?", (row_id,),
            ).fetchone()
            if row is None:
                raise LocalProcedureNotFound(row_id)
            if row["staleness"] != "fresh":
                return _row_to_dict(row)
            now = _now_iso()
            conn.execute(
                "UPDATE local_procedures SET staleness = 'stale', updated_at = ? WHERE id = ?",
                (now, row_id),
            )
            conn.commit()
            updated = conn.execute(
                "SELECT * FROM local_procedures WHERE id = ?", (row_id,),
            ).fetchone()
        finally:
            conn.close()
        return _row_to_dict(updated)

    # -----------------------------------------------------------------
    # Search -- lexical substring match on name+goal always; a real
    # cosine-similarity ranking layered on top IFF the row carries a
    # stored embedding AND the caller supplies a query embedding. Pure
    # Python: this corpus is small by design (one workspace's private
    # library), no pgvector/ANN index warranted.
    # -----------------------------------------------------------------
    def search_local_procedures(
        self,
        query: str,
        *,
        query_embedding: Optional[list[float]] = None,
        limit: int = 10,
        include_invalid: bool = False,
    ) -> list[dict]:
        rows = self.list_local_procedures(include_invalid=include_invalid)
        needle = (query or "").strip().lower()
        # Word-level, not whole-phrase, substring match: a multi-word
        # query like "pandas append" must match a name/goal that mentions
        # either word (real prose rarely repeats the query verbatim) --
        # whole-phrase substring matching would almost never hit real
        # prose. Rows are ranked by how many distinct query words they
        # match, most first, so a row matching every word still sorts
        # ahead of one matching only one.
        needle_words = needle.split()

        if not needle_words:
            candidates = list(rows)
        else:
            scored_lexical = []
            for r in rows:
                haystack = f"{r['name']} {r['goal']}".lower()
                match_count = sum(1 for w in needle_words if w in haystack)
                if match_count > 0:
                    scored_lexical.append((match_count, r))
            scored_lexical.sort(key=lambda pair: pair[0], reverse=True)
            candidates = [r for _, r in scored_lexical]

        if query_embedding is not None:
            scored = []
            for r in candidates or rows:
                if r.get("embedding"):
                    sim = _cosine_similarity(query_embedding, r["embedding"])
                else:
                    sim = None
                scored.append((r, sim))
            # Rows with a real embedding rank by similarity, highest
            # first; rows with none sort after them (None never beats a
            # real number) but are not dropped -- a lexical match with no
            # embedding is still a real match.
            scored.sort(key=lambda pair: (pair[1] is None, -(pair[1] or 0.0)))
            return [r for r, _ in scored[:limit]]

        return candidates[:limit]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
