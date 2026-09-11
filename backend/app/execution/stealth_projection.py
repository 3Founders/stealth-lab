"""
MCP hardening B35 / A29 / G13: the `.stealth/` local working-set
projection.

This module is now a thin back-compat shim. The implementation moved to
the `app.stealth` package when the local side was expanded (per the
ratified local-architecture decision) from the compact
`context.md` / `run.json` / `meta.json` trio into a filesystem-native
working set -- addressable `claims.md` / `procedures.md` /
`implementations.md` / `run.md` pages plus `index/*.idx` routing tables.
See `app/stealth/__init__.py`.

Everything the old module exported is re-exported here unchanged:

  - `generate_projection(pool, *, workspace_root, procedure_run_id)` --
    same signature, same return keys (`context_md`, `run_json`,
    `meta_json`, `paths`), now also writes the addressable pages;
  - `_render_context_md` / `_render_run_json` / `_render_meta_json` --
    the original B35 single-file renderers, verbatim
    (`app.stealth.legacy_context`);
  - `_atomic_write` -- `app.stealth.atomic.atomic_write`;
  - `STEALTH_DIRNAME`, `CONTEXT_MD_MAX_BYTES`, `StealthProjectionError`.

The projection is still a pure, idempotent regeneration from canonical
state. Nothing reads `.stealth/` back and trusts it; a missing or stale
projection is always rehydrated from Postgres.
"""
from __future__ import annotations

from typing import Any

import asyncpg

from app.stealth.atomic import atomic_write as _atomic_write
from app.stealth.errors import StealthProjectionError
from app.stealth.generator import generate_projection as _generate_projection
from app.stealth.legacy_context import (
    CONTEXT_MD_MAX_BYTES,
    STEALTH_DIRNAME,
    _render_context_md,
    _render_meta_json,
    _render_run_json,
)

__all__ = [
    "generate_projection",
    "StealthProjectionError",
    "STEALTH_DIRNAME",
    "CONTEXT_MD_MAX_BYTES",
    "_atomic_write",
    "_render_context_md",
    "_render_run_json",
    "_render_meta_json",
]


async def generate_projection(
    pool: asyncpg.Pool, *, workspace_root: str, procedure_run_id: str,
) -> dict[str, Any]:
    """See `app.stealth.generator.generate_projection`. This wrapper
    passes the module-level `CONTEXT_MD_MAX_BYTES` (looked up per call, so
    a test that monkeypatches it on this module still takes effect)."""
    return await _generate_projection(
        pool,
        workspace_root=workspace_root,
        procedure_run_id=procedure_run_id,
        context_md_max_bytes=CONTEXT_MD_MAX_BYTES,
    )
