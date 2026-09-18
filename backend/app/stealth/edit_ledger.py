"""
`stealth_edit_ledger` (migration 92) -- a GitHub-style "who changed what
file, and when" audit trail for hand-edits a caller makes directly to
`.stealth/*.md` projection files, through a separate, simpler MCP
frontend a teammate (Chaitanya) is building on top of this server.

WHAT THIS IS NOT: `.stealth/` stays a purely disposable, regenerated
projection of canonical Postgres state -- that invariant is unchanged by
this module. An edit-ledger entry does NOT feed into any canonical
Claim/Procedure/Goal creation, is never diffed or replayed, and never
promotes edited content into backend truth. That already exists,
untouched, as `app.stealth.local_sync.preview_local_sync`/
`commit_local_sync_items` -- a completely separate, explicit gate. This
module is deliberately kept decoupled from that one: it never imports
from `local_sync.py`, and `local_sync.py` never imports from here. This
is log-only, evidence/audit history -- not source control (no diffs, no
before/after content, no revert mechanism).

IDENTITY: scoped by `project_id`, the SAME derived-from-repo_path identity
`app.execution.workspace_init._project_id_from_repo_path` already
establishes for a workspace -- reused here rather than inventing a
second workspace-identity concept (a ledger entry recorded from one
checkout of a repo is visible from any other checkout of the SAME repo,
same reasoning `run_collaboration_records`, migration 90, already used
for why it is durable Postgres and not a local-only journal).

VALID FILES: `file_path` must be one of `app.stealth.generator.
CONTENT_PAGE_FILES` / `OPTIONAL_CONTENT_PAGE_FILES` -- the same, single
list of real `.stealth/*.md` content pages `generate_projection` itself
writes, imported rather than hardcoded a second time. `ledger.md` itself
is excluded from that list (it is generated FROM this ledger, not a page
a caller edits) and is therefore never a valid `file_path` here either.

Same DB-access discipline as `app.execution.run_collaboration`: plain
`pool.fetch`/`fetchrow`/`execute`, no ORM, no side effects on any other
table.
"""
from __future__ import annotations

import hashlib
import os
from typing import Optional

import asyncpg

from app.stealth.atomic import atomic_write
from app.stealth.generator import CONTENT_PAGE_FILES, OPTIONAL_CONTENT_PAGE_FILES
from app.stealth.legacy_context import STEALTH_DIRNAME

VALID_FILES: tuple[str, ...] = CONTENT_PAGE_FILES + OPTIONAL_CONTENT_PAGE_FILES


class EditLedgerError(ValueError):
    """Raised for a malformed `record_stealth_edit` call -- an unrecognized
    `file_path` (not a real `.stealth/*.md` content page), or an empty
    `actor`/`summary`. Never silently coerced or defaulted."""


def _hash_local_file(repo_path: str, file_path: str) -> Optional[str]:
    """Best-effort `sha256` of the CURRENT local file's text, for the
    optional `content_hash` column (see migration 92's own docstring for
    why this is the one exception to "no content is stored" -- it is a
    one-line, already-cheap computation, not new hashing infrastructure).
    Returns `None` when the file cannot be read (missing, permissions,
    not yet written) -- a ledger entry is still valid without a hash."""
    path = os.path.join(repo_path, STEALTH_DIRNAME, file_path)
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


async def record_stealth_edit(
    pool: asyncpg.Pool, *, project_id: str, file_path: str, actor: str, summary: str,
    repo_path: Optional[str] = None,
) -> dict:
    """
    Insert one edit-ledger entry. Validates, then writes:

      - `file_path` must be one of `VALID_FILES` (the real known
        `.stealth/*.md` content pages) -- refused, not silently accepted,
        for anything else (a typo, a non-`.stealth/` path, `ledger.md`
        itself).
      - `actor` and `summary` must be non-empty.

    `repo_path`, when given, is used ONLY to compute the optional
    `content_hash` (reading the CURRENT local file at
    `repo_path/.stealth/<file_path>`) -- never required, never a source
    of any other field. Returns the inserted row as a plain dict.
    """
    if file_path not in VALID_FILES:
        raise EditLedgerError(f"file_path must be one of {VALID_FILES}, got {file_path!r}")
    if not actor or not actor.strip():
        raise EditLedgerError("actor must be non-empty")
    if not summary or not summary.strip():
        raise EditLedgerError("summary must be non-empty")

    content_hash = _hash_local_file(repo_path, file_path) if repo_path else None

    row = await pool.fetchrow(
        """
        INSERT INTO stealth_edit_ledger (project_id, file_path, actor, summary, content_hash)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING *
        """,
        project_id, file_path, actor.strip(), summary.strip(), content_hash,
    )
    return dict(row)


async def list_stealth_edits(
    pool: asyncpg.Pool, *, project_id: str, file_path: Optional[str] = None, limit: int = 50,
) -> list[dict]:
    """Ledger entries for a workspace, newest first. `file_path` narrows
    to one file when given; otherwise every recorded edit for the
    workspace. Pure read, no folding -- the flat, immutable history."""
    if file_path is not None:
        rows = await pool.fetch(
            "SELECT * FROM stealth_edit_ledger WHERE project_id = $1 AND file_path = $2 "
            "ORDER BY created_at DESC LIMIT $3",
            project_id, file_path, limit,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM stealth_edit_ledger WHERE project_id = $1 "
            "ORDER BY created_at DESC LIMIT $2",
            project_id, limit,
        )
    return [dict(r) for r in rows]


async def write_ledger_projection(pool: asyncpg.Pool, *, workspace_root: str, project_id: str, limit: int = 200) -> str:
    """Best-effort local `.stealth/ledger.md` write -- the same one-way,
    disposable-projection pattern `run.md` already established for
    `run_collaboration_records` (migration 90): the DB rows above are
    already durable and canonical the moment they are inserted; this is
    purely a readable local mirror, never read back as trusted input by
    anything in this codebase. Callers (the `record_stealth_edit` MCP
    tool) treat a failure here as best-effort -- surfaced, never raised,
    never rolling back the already-committed DB row.

    Returns `"written"`. Raises `OSError` on a real filesystem failure --
    the caller decides how to report that (same convention as every other
    `.stealth/` write path)."""
    from app.stealth.pipe_format import EditLine, render_ledger_md

    edits = await list_stealth_edits(pool, project_id=project_id, limit=limit)
    lines = [
        EditLine(
            edit_id=str(e["id"]), actor=e["actor"], file=e["file_path"],
            created_at=e["created_at"].isoformat() if e["created_at"] else "-",
            summary=e["summary"],
        )
        for e in edits
    ]
    ledger_md = render_ledger_md(lines)
    path = os.path.join(workspace_root, STEALTH_DIRNAME, "ledger.md")
    atomic_write(path, ledger_md)
    return "written"
