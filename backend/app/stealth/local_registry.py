"""
Local project registry -- the smallest possible answer to "what local keळ
projects exist on this machine", for the local sync bridge's `list-projects`
step (docs/local_project_sync_security.md §C / Implementation Closure §2).

WHY THIS EXISTS: every `.stealth`-touching MCP tool takes `repo_path` as a
caller-supplied parameter on every call (confirmed by reading
app/mcp_server/server.py) -- the MCP server process holds no persistent
notion of "the current project" and there was, before this file, no
existing registry of "every local keळ project this machine knows about".
Without one, the local bridge could only ever offer the single project the
calling agent happens to already be pointed at, never "pick from your other
local projects too".

WHAT THIS IS NOT: not a project database, not a cache of project content.
Exactly three fields per entry, nothing else, ever:
  - stable_project_id  (the SAME id ensure_stable_project_id already mints,
                        never a second identity)
  - display_hint        (the local folder's basename ONLY -- never a full
                        path, never sent past the loopback interface to the
                        remote server)
  - last_local_activity_at

WHERE: a per-OS, per-user application-data directory (via `platformdirs`),
never inside any single project's own `.stealth/` (it must span multiple
repos) and never a hardcoded path.

WRITTEN BY: `ensure_stable_project_id` (app.stealth.project_sync) -- the one
function every relevant call site (init_workspace, generate_projection, the
sync preview tool) already calls on every connect. This file adds one
best-effort upsert there; nothing new scans, watches, or crawls anything.

READ BY: the local bridge's `list-projects` step only. No recursive
filesystem scan is ever performed by this module -- it reads exactly the
one file below and nothing else.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from app.stealth.atomic import atomic_write

_APP_NAME = "stealthlab"


def _registry_path() -> str:
    from platformdirs import user_data_dir

    directory = user_data_dir(_APP_NAME, appauthor=False, roaming=True)
    return os.path.join(directory, "local_projects.json")


def _read_registry(path: str) -> dict[str, dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            return loaded
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def upsert_local_project(repo_path: str, *, stable_project_id: str) -> None:
    """Best-effort. A failure here (e.g. no writable app-data directory on
    this machine) must never block the caller's own operation -- callers
    swallow OSError the same way every other best-effort `.stealth/` write
    in this codebase already does (see generate_projection's own
    `projection = f"write_failed: {exc}"` convention)."""
    path = _registry_path()
    registry = _read_registry(path)
    registry[stable_project_id] = {
        "repo_path": repo_path,
        "display_hint": os.path.basename(os.path.normpath(repo_path)) or repo_path,
        "last_local_activity_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write(path, json.dumps(registry, indent=2))


def list_local_projects() -> list[dict[str, Any]]:
    """Read-only. Returns every registry entry, newest activity first.
    Never touches the filesystem beyond the one registry file -- no scan,
    no crawl, by construction (there is no other filesystem access in this
    function at all)."""
    registry = _read_registry(_registry_path())
    rows = [
        {"stable_project_id": pid, **entry}
        for pid, entry in registry.items()
        if isinstance(entry, dict) and "repo_path" in entry
    ]
    rows.sort(key=lambda r: r.get("last_local_activity_at") or "", reverse=True)
    return rows
