"""
P3 -- the "knowledge page fault".

An agent greps `.stealth/index/*.idx`, misses, and calls the
`project_knowledge` MCP tool. This module resolves the requested objects
from **global Postgres** (never fabricated) and merges them additively
into the existing `.stealth/` pages -- the run-scoped working set the
generator wrote is preserved, the globals are appended, and every `.idx`
(plus `root.idx`) is regenerated so the new blocks are line-addressable.

Faulted-in ids are also recorded in `index/faulted.json`, so the next
full `generate_projection` re-includes them (membership in the working
set survives regeneration).

Fail-closed: an id that resolves to nothing is reported as `not_found`,
never written as an empty-but-real block.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

import asyncpg

from app.stealth.atomic import atomic_write_batch
from app.stealth.errors import StealthProjectionError
from app.stealth.format import (
    ROOT_IDX_MAX_BYTES,
    IdxRow,
    MdBlock,
    kv,
    parse_md_page,
    render_idx,
    render_md_page,
    render_root_idx,
    standard_root_rows,
)
from app.stealth.journal import SingleWriterLock, append_event
from app.stealth.legacy_context import STEALTH_DIRNAME

FAULTED_SIDECAR = os.path.join("index", "faulted.json")

_PAGE_FILE = {"claim": "claims.md", "procedure": "procedures.md", "implementation": "implementations.md"}
_IDX_FILE = {"claim": "claims.idx", "procedure": "procedures.idx", "implementation": "implementations.idx"}
_IDX_HEADER = {
    "claim": "claims.idx  id|version|scope|status|tags|file|start|end|summary",
    "procedure": "procedures.idx  id|version|scope|status|tags|file|start|end|summary",
    "implementation": "implementations.idx  id|version|scope|status|tags|file|start|end|summary",
}


def _sdir(workspace_root: str) -> str:
    return os.path.join(workspace_root, STEALTH_DIRNAME)


def read_faulted(workspace_root: str) -> list[dict]:
    path = os.path.join(_sdir(workspace_root), FAULTED_SIDECAR)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return [e for e in data if isinstance(e, dict) and e.get("id") and e.get("kind")]
    except (FileNotFoundError, ValueError):
        return []


def _write_faulted(workspace_root: str, entries: list[dict]) -> None:
    path = os.path.join(_sdir(workspace_root), FAULTED_SIDECAR)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_batch([(path, json.dumps(entries, indent=2, default=str))])


# --------------------------------------------------------------- resolvers
async def _resolve_claim(pool: asyncpg.Pool, cid: str) -> Optional[MdBlock]:
    row = await pool.fetchrow(
        "SELECT id, properties, scope_type, t_valid, t_invalid FROM knowledge_nodes "
        "WHERE id = $1::uuid AND node_type = 'claim'", cid,
    )
    if row is None:
        return None
    props = row["properties"]
    if isinstance(props, str):
        props = json.loads(props)
    props = props or {}
    stmt = props.get("statement") or props.get("text") or ""
    status = props.get("claim_status") or props.get("status") or "-"
    live = row["t_invalid"] is None
    return MdBlock(
        obj_id=str(row["id"]),
        heading=f"CLAIM {row['id']} (global)",
        body=[
            kv("statement", stmt),
            kv("kind", "global_claim"),
            kv("status", status),
            kv("scope", row["scope_type"] or "-"),
            kv("live", "yes" if live else "no"),
            kv("belief", props.get("belief_score", "-")),
        ],
        version="-", scope=row["scope_type"] or "global", status=str(status),
        tags=("global", "faulted"), summary=(stmt or str(row["id"]))[:110],
    )


async def _resolve_procedure(pool: asyncpg.Pool, pid: str) -> Optional[MdBlock]:
    row = await pool.fetchrow(
        "SELECT id, procedure_id, version, name, goal, steps, preconditions, postconditions "
        "FROM procedures WHERE id = $1::uuid OR procedure_id = $1::uuid "
        "ORDER BY version DESC LIMIT 1", pid,
    )
    if row is None:
        return None
    steps = row["steps"]
    if isinstance(steps, str):
        steps = json.loads(steps or "[]")
    body = [kv("goal", row["goal"] or ""), kv("name", row["name"] or ""),
            kv("procedure_id", str(row["procedure_id"])), kv("version", row["version"])]
    if steps:
        body.append("steps:")
        for i, s in enumerate(steps):
            g = s.get("goal") if isinstance(s, dict) else str(s)
            body.append(f"  {i}. {g or ''}")
    return MdBlock(
        obj_id=str(row["procedure_id"]),
        heading=f"PROCEDURE {row['procedure_id']} v{row['version']} (global)",
        body=body, version=f"v{row['version']}", scope="global", status="-",
        tags=("global", "faulted"), summary=(row["goal"] or row["name"] or "")[:110],
    )


async def _resolve_implementation(pool: asyncpg.Pool, iid: str) -> Optional[MdBlock]:
    row = await pool.fetchrow(
        "SELECT id, name, kind, provider, version FROM implementations WHERE id = $1::uuid", iid,
    )
    if row is None:
        return None
    return MdBlock(
        obj_id=str(row["id"]),
        heading=f"IMPLEMENTATION {row['id']} (global)",
        body=[kv("name", row["name"] or ""), kv("kind", row["kind"] or ""),
              kv("provider", row["provider"] or ""), kv("version", row["version"])],
        version=str(row["version"] or "-"), scope="global", status="AVAILABLE",
        tags=("global", "faulted"), summary=f"{row['name']} ({row['provider']})"[:110],
    )


_RESOLVERS = {"claim": _resolve_claim, "procedure": _resolve_procedure, "implementation": _resolve_implementation}


async def _resolve_one(pool: asyncpg.Pool, kind: str, oid: str) -> Optional[MdBlock]:
    fn = _RESOLVERS.get(kind)
    if fn is None:
        return None
    try:
        return await fn(pool, oid)
    except (asyncpg.PostgresError, ValueError):
        return None


async def resolve_blocks_by_kind(pool: asyncpg.Pool, entries: list[dict]) -> dict[str, list[MdBlock]]:
    """For `generate_projection` -- resolve every sidecar entry to a block,
    grouped by page kind. Silently drops entries that no longer resolve."""
    out: dict[str, list[MdBlock]] = {"claim": [], "procedure": [], "implementation": []}
    seen: set[tuple[str, str]] = set()
    for e in entries:
        kind, oid = e.get("kind"), str(e.get("id"))
        if kind not in out or (kind, oid) in seen:
            continue
        seen.add((kind, oid))
        block = await _resolve_one(pool, kind, oid)
        if block is not None:
            out[kind].append(block)
    return out


# --------------------------------------------------------- the MCP entrypoint
async def project_knowledge(
    pool: asyncpg.Pool, workspace_root: str, *,
    object_ids: Optional[list[dict]] = None,
    query: Optional[str] = None,
    top_k: int = 8,
) -> dict[str, Any]:
    """
    Merge global objects into an existing `.stealth/` projection.

    `object_ids`: list of `{"kind": "claim|procedure|implementation", "id": "<uuid>"}`.
    `query`: free text -> relevant global claims via `get_relevant_claims`.

    Requires a projection to already exist under `workspace_root`
    (`generate_projection` must have run). Returns
    `{"resolved": [...], "not_found": [...], "already_present": [...],
      "counts": {...}, "journal_seq": N}`.
    """
    sdir = _sdir(workspace_root)
    if not os.path.isdir(os.path.join(sdir, "index")):
        raise StealthProjectionError(
            f"no .stealth/ projection under {workspace_root!r} -- run generate_projection first"
        )

    requested: list[tuple[str, str]] = []
    for e in (object_ids or []):
        k, i = e.get("kind"), e.get("id")
        if k in _RESOLVERS and i:
            requested.append((k, str(i)))

    if query:
        from app.services.relevant_claims import get_relevant_claims
        try:
            claim_refs = await get_relevant_claims(pool, goal=query, top_k=top_k)
        except Exception:  # noqa: BLE001 -- retrieval is best-effort here
            claim_refs = []
        for ref in claim_refs:
            cid = ref.get("id") or ref.get("claim_id")
            if cid:
                requested.append(("claim", str(cid)))

    # de-dup requested
    seen: set[tuple[str, str]] = set()
    requested = [r for r in requested if not (r in seen or seen.add(r))]

    existing_faulted = read_faulted(workspace_root)
    existing_ids = {(e["kind"], str(e["id"])) for e in existing_faulted}

    resolved: list[dict] = []
    not_found: list[dict] = []
    already_present: list[dict] = []
    new_blocks: dict[str, list[MdBlock]] = {"claim": [], "procedure": [], "implementation": []}

    for kind, oid in requested:
        if (kind, oid) in existing_ids:
            already_present.append({"kind": kind, "id": oid})
            continue
        block = await _resolve_one(pool, kind, oid)
        if block is None:
            not_found.append({"kind": kind, "id": oid})
            continue
        new_blocks[kind].append(block)
        resolved.append({"kind": kind, "id": oid})

    with SingleWriterLock(workspace_root):
        writes: list[tuple[str, str]] = []
        for kind, blocks in new_blocks.items():
            if not blocks:
                continue
            page_path = os.path.join(sdir, _PAGE_FILE[kind])
            idx_path = os.path.join(sdir, "index", _IDX_FILE[kind])
            try:
                current = parse_md_page(open(page_path, encoding="utf-8").read())
            except FileNotFoundError:
                current = []
            have = {b.obj_id for b in current}
            merged = current + [b for b in blocks if b.obj_id not in have]
            rendered = render_md_page(_PAGE_FILE[kind], merged)
            idx_rows = [
                IdxRow(
                    obj_id=b.obj_id, version=b.version or "-", scope=b.scope or "-",
                    status=b.status or "-", tags=b.tags or (),
                    file=_PAGE_FILE[kind], start=rendered.ranges[b.obj_id][0],
                    end=rendered.ranges[b.obj_id][1], summary=b.summary or "",
                )
                for b in merged
            ]
            writes.append((page_path, rendered.text))
            writes.append((idx_path, render_idx(idx_rows, header=_IDX_HEADER[kind])))

        # regenerate root.idx (exploration.idx presence carried through)
        has_expl = os.path.exists(os.path.join(sdir, "index", "exploration.idx"))
        root_idx = render_root_idx(standard_root_rows(has_exploration=has_expl))
        if len(root_idx.encode("utf-8")) > ROOT_IDX_MAX_BYTES:
            raise StealthProjectionError("index/root.idx over budget after page-fault merge")
        writes.append((os.path.join(sdir, "index", "root.idx"), root_idx))

        # persist sidecar membership
        merged_faulted = existing_faulted + [
            {"kind": r["kind"], "id": r["id"]} for r in resolved
        ]
        _write_faulted(workspace_root, merged_faulted)

        seq = append_event(
            workspace_root, "knowledge_fault", _lock_held=True,
            requested=[{"kind": k, "id": i} for k, i in requested],
            resolved=resolved, not_found=not_found, already_present=already_present,
        )

        # bump meta.json.projection_revision to the new seq
        meta_path = os.path.join(sdir, "meta.json")
        try:
            meta = json.loads(open(meta_path, encoding="utf-8").read())
        except (FileNotFoundError, ValueError):
            meta = {}
        meta["projection_revision"] = seq
        faulted_counts = meta.get("faulted", {})
        for r in resolved:
            faulted_counts[r["kind"]] = faulted_counts.get(r["kind"], 0) + 1
        meta["faulted"] = faulted_counts
        writes.append((meta_path, json.dumps(meta, indent=2, default=str)))

        atomic_write_batch(writes)

    return {
        "resolved": resolved,
        "not_found": not_found,
        "already_present": already_present,
        "counts": {k: len(v) for k, v in new_blocks.items()},
        "journal_seq": seq,
    }
