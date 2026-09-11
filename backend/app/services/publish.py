"""
Shared publication-scrub helpers.

This module used to host `publish_local_procedure` -- the integration
point between the DB-free per-workspace SQLite `local_procedures` store
and the global procedure writer. That store was removed with the
local-architecture rebuild (the local side is now the filesystem
`.stealth/` working set), so the local->global publish path here is gone.

What survives is the leaf-string scrub that every local->global crossing
still needs and that `app.services.publication.publish_procedure` reuses
(`_scrub_value`): strip known-secret tokens (via
`trace_redaction.redact_value`), absolute filesystem paths, and
private/internal hostnames. `app/services/publication.py` remains the
one Local -> Global path -- an already-Postgres-resident
`visibility='private'|'org'` row published via
`POST /v1/procedures/{id}/publish`.

`AlreadyPublishedError` / `UnauthorizedPublication` are kept as importable
names so any lingering `except` sites do not break; nothing in-tree
raises them any more.
"""
from __future__ import annotations

import re
from typing import Any

from app.services.trace_redaction import redact_value

PUBLISHED_PROVENANCE = "system_pending_review"

# trace_redaction's KNOWN_TOKEN_PATTERNS (via redact_value) catches
# secret-shaped tokens but has no generic absolute-path rule. A procedure's
# steps/preconditions/scope can legitimately contain an author's own
# absolute path (e.g. "cd C:\\Users\\me\\repo") which is machine-specific
# and must not enter the shared global commons verbatim. Narrowly-targeted
# regexes only -- not a generic PII framework. Private/internal hostnames
# are stripped the same way.
_WINDOWS_ABS_PATH = re.compile(r"[A-Za-z]:\\(?:[^\s\"'<>|*?]+)")
_POSIX_ABS_PATH = re.compile(r"/(?:home|Users|root)/[^\s\"'<>|]+")
_PRIVATE_HOSTNAME = re.compile(
    r"\b(?:[a-zA-Z0-9-]+\.)*(?:internal|corp|local|lan|intranet)\b"
    r"(?::\d{2,5})?",
)
PATH_REDACTION_PLACEHOLDER = "[REDACTED:absolute_path]"
HOSTNAME_REDACTION_PLACEHOLDER = "[REDACTED:private_hostname]"


def _scrub_paths(value: str) -> str:
    """Replace absolute filesystem paths (Windows and POSIX user/home/root
    paths) and private/internal hostnames with a placeholder. Leaf-string
    only, same shape as trace_redaction's own leaf-string primitive."""
    value = _WINDOWS_ABS_PATH.sub(PATH_REDACTION_PLACEHOLDER, value)
    value = _POSIX_ABS_PATH.sub(PATH_REDACTION_PLACEHOLDER, value)
    value = _PRIVATE_HOSTNAME.sub(HOSTNAME_REDACTION_PLACEHOLDER, value)
    return value


def _scrub_value(value: Any) -> Any:
    """Recursively walk a parsed JSON value (dict/list/str/other),
    applying BOTH scrubs to every string leaf: trace_redaction's shared
    known-secret-token primitive (`redact_value`), then this module's own
    absolute-path/hostname scrub."""
    if isinstance(value, str):
        matched: list[str] = []
        return _scrub_paths(redact_value(value, matched))
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_value(v) for v in value]
    return value


class AlreadyPublishedError(Exception):
    """Retained importable name. Nothing in-tree raises this any more
    (the local-store publish path was removed)."""


class UnauthorizedPublication(PermissionError):
    """Retained importable name. Nothing in-tree raises this any more
    (the local-store publish path was removed)."""
