"""
Deterministic tool -> Implementation identity mapping (trajectory-
ingestion-hardening task, Sec 8). Concrete tool/mechanism identification
should be deterministic: `rg` is the ripgrep Implementation whether an
LLM ever looks at the trajectory or not. The LLM's job (see
trajectory_semantics.py) is limited to inferring which Goal an
Implementation served, its role in a Procedure, and observed failure
conditions -- never inventing an Implementation identity, matching
`app/execution/implementation_registry.py`'s "nothing is born trusted"
posture and directive Sec 19's "never mutate a used implementation's
identity silently".

Reuses `implementation_registry.register()` as the one real writer (no
second implementations table, no second identity scheme) -- this module
only adds the find-or-create wrapper that registry doesn't provide, using
the same optimistic-insert-then-recover-from-UniqueViolationError race
pattern `app/services/goals.py::find_or_create_goal` already established
for a different table under the same concurrent-writer constraint.
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg

from app.execution import implementation_registry

# tool_name / canonical action identifier (as it appears in trace_events,
# either the Claude-Code tool_name or the OpenHands `action` discriminator)
# -> (implementation name, provider, kind). Deliberately small and
# reviewable, not an attempt at exhaustive tool coverage -- new entries
# are added here as new concrete tools are observed, never inferred by an
# LLM at ingestion time.
TOOL_TO_IMPLEMENTATION: dict[str, tuple[str, str, str]] = {
    # Claude Code / MCP-native tool names.
    "Bash": ("bash", "posix", "tool"),
    "Read": ("file_read", "claude_code", "tool"),
    "Edit": ("file_editor", "claude_code", "tool"),
    "Write": ("file_editor", "claude_code", "tool"),
    "MultiEdit": ("file_editor", "claude_code", "tool"),
    "Grep": ("ripgrep", "claude_code", "tool"),
    "Glob": ("glob", "claude_code", "tool"),
    # OpenHands action discriminators.
    "run": ("bash", "posix", "tool"),
    "run_ipython": ("ipython", "openhands", "tool"),
    "read": ("openhands_file_editor", "openhands", "tool"),
    "write": ("openhands_file_editor", "openhands", "tool"),
    "edit": ("openhands_file_editor", "openhands", "tool"),
    "browse": ("browser", "openhands", "tool"),
    "browse_interactive": ("browser", "openhands", "tool"),
}

# Command substrings (checked against a Bash/`run` command's own text,
# same substring discipline observations.py's _looks_like_test_command
# uses) -> a more specific Implementation than the generic shell.
COMMAND_TO_IMPLEMENTATION: dict[str, tuple[str, str, str]] = {
    "pytest": ("pytest", "python", "tool"),
    "ripgrep": ("ripgrep", "posix", "tool"),
    " rg ": ("ripgrep", "posix", "tool"),
    "npm test": ("npm_test", "node", "tool"),
    "go test": ("go_test", "go", "tool"),
    "cargo test": ("cargo_test", "rust", "tool"),
    "jest": ("jest", "node", "tool"),
    "git commit": ("git", "posix", "tool"),
}


def identify_implementation(tool_name: Optional[str], command: Optional[str] = None) -> Optional[tuple[str, str, str]]:
    """Pure, deterministic. Returns (name, provider, kind) or None when
    nothing in the closed map matches -- an unrecognized tool is real,
    honest "no known Implementation", never a guessed one."""
    if command:
        lowered = command.lower()
        for marker, identity in COMMAND_TO_IMPLEMENTATION.items():
            if marker in lowered:
                return identity
    if tool_name and tool_name in TOOL_TO_IMPLEMENTATION:
        return TOOL_TO_IMPLEMENTATION[tool_name]
    return None


async def find_or_create_implementation_identity(
    pool: asyncpg.Pool,
    *,
    name: str,
    provider: str,
    kind: str,
    created_by: str,
    description: Optional[str] = None,
    visibility: str = "public",
    owner_id: Optional[str] = None,
    scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None,
) -> dict[str, Any]:
    """Finds the latest version of the (name, provider) identity, or
    registers version 1 if none exists yet. Never invents a NEW version
    of an existing implementation -- that is `implementation_registry`'s
    own explicit versioning operation, out of scope for an ingestion-time
    identity lookup, which only ever wants "the" implementation this tool
    call used, not a specific pinned version."""
    row = await pool.fetchrow(
        "SELECT * FROM implementations WHERE name = $1 AND provider = $2 "
        "ORDER BY version DESC LIMIT 1",
        name, provider,
    )
    if row is not None:
        return dict(row)

    try:
        return await implementation_registry.register(
            pool,
            name=name,
            kind=kind,
            provider=provider,
            created_by=created_by,
            description=description,
            visibility=visibility,
            owner_id=owner_id,
            scope_type=scope_type,
            scope_entity_id=scope_entity_id,
        )
    except asyncpg.UniqueViolationError:
        # A concurrent ingestion job registered the same (name, provider,
        # version=1) identity between our SELECT and INSERT -- re-select
        # rather than erroring the whole extraction over a benign race.
        row = await pool.fetchrow(
            "SELECT * FROM implementations WHERE name = $1 AND provider = $2 "
            "ORDER BY version DESC LIMIT 1",
            name, provider,
        )
        if row is None:
            raise
        return dict(row)
