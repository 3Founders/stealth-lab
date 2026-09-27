"""Verified solutions: the verified code a Procedure was extracted from, recorded on the PROVENANCE model.

docs/knowledge_side_improvements.md, changes 1-2 (migration 124). Nothing new on the Procedure row: the solution is
recorded where provenance already lives --

  procedures.source_locator     WHERE the solution is. Code committed somewhere durable is pointed at, never copied:
                                {uri, repository?, path, commit, line_start?, line_end?, content_hash, granularity}.
                                Otherwise it points at the artifact below.
  procedures.source_artifacts   {artifact_id, path, role: "verified_solution", execution_allowed: false, note: <task>}
  ingested_artifacts            one row per solution (role verified_solution, content_hash, repo/path/commit).
                                Bytes: object storage when configured (content_ref = {sha256, locator, size});
                                else inline in content_ref when <= INLINE_MAX; else hash only (metadata_only).
                                A solution with a durable locator keeps no bytes at all.

The code is passed through the known-token redaction (trace_redaction) before anything is stored or hashed, and it is
a REFERENCE: execution_allowed is false and the role can never be executed (ingested_artifacts_exec_role_chk).
`resolve` returns what find_ways shows: the task, the locator, and the code when Kel can read it (inline or object
storage); for a durable repo location the agent opens the locator itself.
Gated by settings.knowledge_verified_examples.
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping, Optional

ROLE = "verified_solution"
SOURCE_TYPE = "verified_solution"
INLINE_MAX = 64 * 1024          # same default as RAW_PAYLOAD_INLINE_MAX_BYTES
MAX_CODE_CHARS = 200_000
MAX_TASK_CHARS = 1_000          # the ref's `note`: what the solution solved
_LOCATOR_KEYS = ("uri", "repository", "path", "commit", "line_start", "line_end")


def _redacted(text: str) -> str:
    from app.services.trace_redaction import _redact_string

    return _redact_string(text)[0]


def durable_locator(given: Any) -> Optional[dict]:
    """A caller-supplied location counts as durable only with a commit AND a path (a repo, or a full uri)."""
    if not isinstance(given, Mapping):
        return None
    loc = {k: given[k] for k in _LOCATOR_KEYS if given.get(k) not in (None, "")}
    if not (loc.get("commit") and loc.get("path") and (loc.get("repository") or loc.get("uri"))):
        return None
    if not loc.get("uri"):
        loc["uri"] = f"https://github.com/{loc['repository']}/blob/{loc['commit']}/{loc['path']}"
    return loc


async def preserve(pool: Any, *, procedure_row_id: str, code: str, task: str, language: str = "python",
                   verified_by: Optional[str] = None, locator: Any = None, owner_id: Optional[str] = None,
                   visibility: str = "public") -> Optional[dict]:
    """Record the verified solution of the Procedure version `procedure_row_id`. Returns the source_artifacts ref,
    or None when there is no code. Never overwrites an existing source_locator (ingested provenance wins)."""
    from app.services.object_storage import get_store, store_blob
    from app.services.shards import home_pool
    from app.services.source_locators import validate_locator, validate_source_artifacts

    code = _redacted((code or "")[:MAX_CODE_CHARS])
    if not code.strip():
        return None
    task = _redacted((task or "").strip())[:MAX_TASK_CHARS]
    data = code.encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    durable = durable_locator(locator)
    content_ref: Optional[dict] = None
    status = "metadata_only"
    if durable is None:
        store = get_store()
        if store is not None:
            content_ref = await store_blob(pool, store, data, content_type="text/plain")
            status = "stored"
        elif len(data) <= INLINE_MAX:
            content_ref = {"sha256": sha, "size": len(data), "inline": code}
            status = "stored"
    if content_ref is not None and verified_by:
        content_ref = {**content_ref, "verified_by": verified_by[:300]}
    uri = durable["uri"] if durable else f"kel:verified-solution:{sha}"
    row = await pool.fetchrow(
        "INSERT INTO ingested_artifacts (id, source_type, uri, repository, path, \"commit\", content_hash, role, mime_type, "
        " language, byte_size, content_ref, extraction_status, execution_allowed, visibility, owner_id, procedure_row_id) "
        "VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7, 'text/plain', $8, $9, $10::jsonb, $11, false, "
        " $12::visibility_level, $13, $14::uuid) "
        "ON CONFLICT (source_type, uri, content_hash) WHERE role IS NOT NULL DO UPDATE SET last_seen = now() RETURNING id",
        SOURCE_TYPE, uri, (durable or {}).get("repository"), (durable or {}).get("path"), (durable or {}).get("commit"),
        sha, ROLE, (language or "")[:40] or None, len(data), content_ref, status,
        visibility, owner_id, str(procedure_row_id))
    ref = validate_source_artifacts([{"artifact_id": str(row["id"]), "path": (durable or {}).get("path") or "verified_solution",
                                      "role": ROLE, "execution_allowed": False, "note": task}])[0]
    if durable:
        # the locator schema carries the repository inside `uri` (the artifact row keeps `repository` itself)
        loc = {**{k: v for k, v in durable.items() if k != "repository"}, "content_hash": sha,
               "granularity": "span" if "line_start" in durable else "document"}
    else:
        loc = {"uri": uri, "content_hash": sha, "granularity": "document",
               **({"object_locator": content_ref["locator"]} if content_ref and content_ref.get("locator") else {})}
    loc = validate_locator(loc)
    hp = await home_pool(pool, "procedure", str(procedure_row_id), by_row_id=True)
    await hp.execute(
        "UPDATE procedures SET source_artifacts = COALESCE(source_artifacts, '[]'::jsonb) || $2::jsonb, "
        "source_locator = COALESCE(source_locator, $3::jsonb) WHERE id = $1::uuid",
        str(procedure_row_id), [ref], loc)       # the pools' jsonb codec encodes; never pass pre-encoded JSON
    return ref


def solution_ref(source_artifacts: Any) -> Optional[dict]:
    import json

    if isinstance(source_artifacts, str):
        try:
            source_artifacts = json.loads(source_artifacts)
        except ValueError:
            return None
    for ref in source_artifacts or []:
        if isinstance(ref, Mapping) and ref.get("role") == ROLE and ref.get("artifact_id"):
            return dict(ref)
    return None


async def resolve(pool: Any, source_artifacts: Any, source_locator: Any = None) -> Optional[dict]:
    """What find_ways returns for a Procedure's verified solution, or None when it has none.
    {task, locator, language, verified_by, code (None when Kel keeps no bytes), code_available}."""
    import json

    from app.services.object_storage import get_store

    ref = solution_ref(source_artifacts)
    if ref is None:
        return None
    art = await pool.fetchrow(
        "SELECT uri, repository, path, \"commit\", content_hash, language, content_ref FROM ingested_artifacts "
        "WHERE id = $1::uuid AND role = $2 AND t_invalid IS NULL", ref["artifact_id"], ROLE)
    if art is None:
        return None
    content_ref = art["content_ref"]
    if isinstance(content_ref, str):
        content_ref = json.loads(content_ref)
    content_ref = content_ref or {}
    code = content_ref.get("inline")
    if code is None and content_ref.get("locator"):
        store = get_store()
        if store is not None:
            try:
                data = await store.get(content_ref["locator"])
                if hashlib.sha256(data).hexdigest() == art["content_hash"]:
                    code = data.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001 -- unreadable storage: return the locator, never fail the answer
                code = None
    locator = source_locator
    if isinstance(locator, str):
        try:
            locator = json.loads(locator)
        except ValueError:
            locator = None
    if not isinstance(locator, Mapping) or locator.get("content_hash") != art["content_hash"]:
        locator = {k: art[k] for k in ("uri", "repository", "path", "commit", "content_hash") if art[k]}
    return {"task": ref.get("note") or None, "locator": dict(locator), "language": art["language"],
            "verified_by": content_ref.get("verified_by"), "code": code, "code_available": code is not None}
