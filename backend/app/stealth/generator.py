"""
`generate_projection` -- regenerate the whole `.stealth/` working set for
one ProcedureRun from canonical Postgres state.

P1 scope: the run-scoped working set (the same canonical reads B35's
original `generate_projection` used -- `get_run_context`,
`fetch_procedure_version`, `evaluate_run_completion`). It now emits, in
addition to the compact `context.md` / `run.json` / `meta.json`:

    claims.md + index/claims.idx           -- the precondition-derived local Claim working set
    procedures.md + index/procedures.idx   -- the selected Procedure, in full
    implementations.md + index/implementations.idx  -- resolved implementation candidates
    run.md + index/run.idx                 -- per-node execution state
    index/root.idx                         -- the router over the above

P2 adds the durable journal: every regeneration runs under the
`SingleWriterLock`, emits a `projection_regenerated` event, and stamps
`meta.json.projection_revision` with the journal's latest `seq` (a
monotonic integer, not a wall-clock stamp). P3 merges any page-faulted
global objects (recorded in `index/faulted.json`) back into the pages so
working-set membership survives regeneration. P4 adds `exploration.md`
(folded from journal state) and real multi-agent `owner` / `write_globs`
/ file-intents in `run.*` from `execution_run_nodes` (migration 56).

Writes go through `atomic_write_batch` with `meta.json` LAST, so a reader
that keys off `meta.json.projection_revision` only sees a revision once
the rest of the set is already on disk.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

import asyncpg

from app.stealth.atomic import atomic_write_batch
from app.stealth.errors import StealthProjectionError
from app.stealth.exploration import render_exploration_page
from app.stealth.faults import read_faulted, resolve_blocks_by_kind
from app.stealth.format import (
    ROOT_IDX_MAX_BYTES,
    TYPE_IDX_MAX_BYTES,
    IdxRow,
    MdBlock,
    RootRow,
    RunIdxRow,
    kv,
    render_idx,
    render_md_page,
    render_root_idx,
    standard_root_rows,
)
from app.stealth.journal import SingleWriterLock, append_events, latest_seq
from app.stealth.legacy_context import (
    CONTEXT_MD_MAX_BYTES,
    STEALTH_DIRNAME,
    _render_context_md,
    _render_meta_json,
    _render_run_json,
)

__all__ = ["generate_projection", "StealthProjectionError", "STEALTH_DIRNAME", "CONTEXT_MD_MAX_BYTES"]


def _short(value: object, n: int = 12) -> str:
    s = str(value or "")
    return s[:n]


def _pc_id(pc: dict) -> str:
    """Stable id for a precondition-derived local claim row."""
    raw = f"{pc.get('subject')}|{pc.get('predicate')}|{pc.get('object')}"
    return "pc-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


_EMPTY_NOTE = {
    "claims.md": "(no structured preconditions and no page-faulted global claims in scope)",
    "procedures.md": "(no selected procedure)",
    "implementations.md": "(no implementation resolved for the current node -- MISSING_IMPLEMENTATION, not fabricated)",
    "run.md": "(no nodes)",
}
_IDX_HEADER = {
    "claims.md": "claims.idx  id|version|scope|status|tags|file|start|end|summary",
    "procedures.md": "procedures.idx  id|version|scope|status|tags|file|start|end|summary",
    "implementations.md": "implementations.idx  id|version|scope|status|tags|file|start|end|summary",
}


def _finalize_page(name: str, blocks: list[MdBlock]) -> tuple[str, list[IdxRow]]:
    """Render a page + its object index. Deduplicates by `obj_id`
    (run-scoped block wins over a faulted duplicate)."""
    seen: set[str] = set()
    uniq: list[MdBlock] = []
    for b in blocks:
        if b.obj_id in seen:
            continue
        seen.add(b.obj_id)
        uniq.append(b)
    if not uniq:
        page = (f"# {name} -- GENERATED, not canonical. Do not hand-edit.\n\n"
                f"{_EMPTY_NOTE.get(name, '(empty)')}\n")
        return page, []
    rendered = render_md_page(name, uniq)
    rows = [
        IdxRow(
            obj_id=b.obj_id, version=b.version or "-", scope=b.scope or "-",
            status=b.status or "-", tags=b.tags or (), file=name,
            start=rendered.ranges[b.obj_id][0], end=rendered.ranges[b.obj_id][1],
            summary=b.summary or "",
        )
        for b in uniq
    ]
    return rendered.text, rows


def _build_claims_page(
    context: dict[str, Any], extra_blocks: tuple[MdBlock, ...] = (),
) -> tuple[str, list[IdxRow]]:
    blocks: list[MdBlock] = []
    for pc in context.get("required_preconditions") or []:
        oid = _pc_id(pc)
        subj, pred, obj = pc.get("subject", ""), pc.get("predicate", ""), pc.get("object", "")
        status = pc.get("status", "UNKNOWN")
        blocks.append(MdBlock(
            obj_id=oid,
            heading=f"CLAIM {oid} (local precondition)",
            body=[
                kv("statement", f"{subj} {pred} {obj}"),
                kv("kind", "precondition"),
                kv("status", status),
                kv("subject", subj),
                kv("predicate", pred),
                kv("object", obj),
            ],
            scope="local", status=status, tags=("precondition",),
            summary=f"{subj} {pred} {obj} -> {status}",
        ))
    return _finalize_page("claims.md", blocks + list(extra_blocks))


def _build_procedures_page(
    context: dict[str, Any], procedure: dict, verification: dict[str, Any],
    extra_blocks: tuple[MdBlock, ...] = (),
) -> tuple[str, list[IdxRow]]:
    pid = str(context["procedure_id"])
    ver = context["procedure_version"]
    by_id = {c["criterion_id"]: c for c in verification.get("criteria", [])}
    body: list[str] = [
        kv("goal", procedure.get("goal", "")),
        kv("name", procedure.get("name", "")),
        kv("procedure_id", pid),
        kv("version", ver),
        kv("run_status", context["status"]),
    ]
    preconds = procedure.get("preconditions") or []
    if preconds:
        body.append("preconditions:")
        for pc in preconds:
            body.append(f"  - {pc.get('subject','')} {pc.get('predicate','')} {pc.get('object','')}")
    body.append("steps:")
    for n in context.get("nodes", []):
        body.append(f"  {n['node_order']}. [{n['status']}] {n.get('goal') or ''}")
    postconds = procedure.get("postconditions") or []
    if postconds:
        body.append("verification:")
        for i, statement in enumerate(postconds):
            text = statement if isinstance(statement, str) else statement.get("statement", "")
            state = by_id.get(f"postcondition:{i}", {}).get("state", "inconclusive")
            body.append(f"  - {text} [{state}]")

    block = MdBlock(
        obj_id=pid,
        heading=f"PROCEDURE {pid} v{ver}",
        body=body,
        version=f"v{ver}",
        scope=context.get("scope_type") or "-",
        status=context["status"],
        tags=tuple((procedure.get("tags") or [])[:6]),
        summary=procedure.get("goal", "") or procedure.get("name", ""),
    )
    return _finalize_page("procedures.md", [block] + list(extra_blocks))


def _build_implementations_page(
    context: dict[str, Any], extra_blocks: tuple[MdBlock, ...] = (),
) -> tuple[str, list[IdxRow]]:
    blocks: list[MdBlock] = []
    for impl in context.get("recommended_implementations") or []:
        iid = str(impl["implementation_id"])
        role = impl.get("role") or impl.get("kind") or "-"
        source = impl.get("source", "-")
        blocks.append(MdBlock(
            obj_id=iid,
            heading=f"IMPLEMENTATION {iid}",
            body=[
                kv("role", role),
                kv("source", source),
                kv("implementation_version", impl.get("implementation_version") or "-"),
            ],
            version=str(impl.get("implementation_version") or "-"),
            status="AVAILABLE",
            tags=(source,),
            summary=f"role={role} source={source}",
        ))
    return _finalize_page("implementations.md", blocks + list(extra_blocks))


def _build_run_page(
    context: dict[str, Any], intents: dict[int, dict] | None = None,
) -> tuple[str, list[RunIdxRow]]:
    """`intents` maps node_order -> a file-intent row from
    `execution_run_nodes` (owner_agent_id / write_globs / ... ). Empty
    when no coordination has been declared."""
    intents = intents or {}
    blocks: list[MdBlock] = []
    nodes = context.get("nodes", [])
    for n in nodes:
        order = n["node_order"]
        nid = f"N{order}"
        deps = [f"N{d}" for d in (n.get("deps") or [])]
        it = intents.get(order) or {}
        owner = it.get("owner_agent_id") or "-"
        wglobs = it.get("write_globs") or []
        wexact = it.get("write_exact") or []
        body = [
            kv("objective", n.get("goal") or ""),
            kv("status", n["status"]),
            kv("owner", owner),
            kv("depends_on", ", ".join(deps) or "-"),
            kv("verification", n.get("verification_state") or "PENDING"),
        ]
        if wglobs_str := ", ".join(wglobs):
            body.append(kv("write_globs", wglobs_str))
        if wexact_str := ", ".join(wexact):
            body.append(kv("write_exact", wexact_str))
        if it.get("file_intent_lease_expires_at"):
            body.append(kv("lease_expires_at", it["file_intent_lease_expires_at"]))
        if n.get("implementation_id"):
            body.append(kv("implementation", str(n["implementation_id"])))
        blocks.append(MdBlock(
            obj_id=nid, heading=f"NODE {nid}", body=body,
            status=n["status"], summary=(n.get("goal") or "")[:100],
        ))
    if context.get("waiting_child"):
        wc = context["waiting_child"]
        blocks.append(MdBlock(
            obj_id="waiting_child",
            heading="COORDINATION waiting_child",
            body=[kv("child_run_id", wc["child_run_id"]), kv("child_status", wc["child_status"])],
            status="BLOCKED", summary=f"waiting on child {wc['child_run_id']} ({wc['child_status']})",
        ))
    if not blocks:
        return ("# run.md -- GENERATED, not canonical. Do not hand-edit.\n\n(no nodes)\n", [])
    rendered = render_md_page("run.md", blocks)
    rows: list[RunIdxRow] = []
    for b in blocks:
        s, e = rendered.ranges[b.obj_id]
        node = next((x for x in nodes if f"N{x['node_order']}" == b.obj_id), None)
        deps = tuple(f"N{d}" for d in (node.get("deps") or [])) if node else ()
        it = intents.get(node["node_order"]) if node else None
        owner = (it or {}).get("owner_agent_id") or "-"
        wglobs = tuple((it or {}).get("write_globs") or ())
        rows.append(RunIdxRow(
            node_id=b.obj_id, status=b.status, owner=owner, deps=deps, write_globs=wglobs,
            file="run.md", start=s, end=e, summary=b.summary,
        ))
    return rendered.text, rows


async def _fetch_file_intents(pool: asyncpg.Pool, run_id: str) -> dict[int, dict]:
    """Live (non-expired) file-intent declarations for the run's nodes
    (migration 56 columns on `execution_run_nodes`). Empty when none."""
    try:
        rows = await pool.fetch(
            "SELECT node_order, owner_agent_id, read_exact, read_globs, write_exact, "
            "       write_globs, symbols_expected_to_modify, file_intent_lease_expires_at "
            "FROM execution_run_nodes WHERE execution_run_id = $1::uuid "
            "AND (owner_agent_id IS NOT NULL "
            "     OR write_globs <> '[]'::jsonb OR write_exact <> '[]'::jsonb)",
            run_id,
        )
    except asyncpg.PostgresError:
        return {}
    out: dict[int, dict] = {}
    for r in rows:
        d = dict(r)
        for k in ("read_exact", "read_globs", "write_exact", "write_globs", "symbols_expected_to_modify"):
            v = d.get(k)
            if isinstance(v, str):
                d[k] = json.loads(v or "[]")
        lease = d.get("file_intent_lease_expires_at")
        d["file_intent_lease_expires_at"] = lease.isoformat() if lease else None
        out[d["node_order"]] = d
    return out


def _revision() -> int:
    return int(datetime.now(timezone.utc).timestamp())


async def generate_projection(
    pool: asyncpg.Pool, *, workspace_root: str, procedure_run_id: str,
    context_md_max_bytes: int | None = None,
) -> dict[str, Any]:
    """
    Regenerate `.stealth/` under `workspace_root` for `procedure_run_id`
    from canonical state only. Returns
    `{context_md, run_json, meta_json, paths}` -- `paths` maps every
    written file (including the new pages and `index/*.idx`) to its
    absolute path.

    `context_md_max_bytes` overrides the `context.md` router budget
    (the back-compat shim passes its own patchable module constant so
    the existing budget test keeps working); `None` uses
    `CONTEXT_MD_MAX_BYTES`.

    Raises `StealthProjectionError` if the run does not exist (never
    writes a fabricated projection) or if the router would exceed its
    byte budget (a real signal the working set pulled in too much).
    """
    ctx_budget = CONTEXT_MD_MAX_BYTES if context_md_max_bytes is None else context_md_max_bytes
    from app.execution.durable_resume import get_run_context
    from app.execution.procedure_graph import fetch_procedure_version
    from app.services.verification import evaluate_run_completion

    run_row = await pool.fetchrow("SELECT * FROM execution_runs WHERE id = $1::uuid", procedure_run_id)
    if run_row is None:
        raise StealthProjectionError(f"procedure_run_id {procedure_run_id!r} not found")
    run_row = dict(run_row)

    context = await get_run_context(pool, procedure_run_id)
    if context is None:
        raise StealthProjectionError(f"procedure_run_id {procedure_run_id!r} has no run context")
    context.setdefault("scope_type", run_row.get("scope_type"))
    procedure = await fetch_procedure_version(
        pool, run_row["procedure_id"], run_row["procedure_version"]
    ) or {}
    verification = await evaluate_run_completion(
        pool, execution_run_id=procedure_run_id, procedure=procedure
    )
    intents = await _fetch_file_intents(pool, procedure_run_id)

    # --- P3: page-faulted global objects recorded in index/faulted.json --
    faulted = await resolve_blocks_by_kind(pool, read_faulted(workspace_root))
    global_claims = tuple(faulted.get("claim", ()))

    # --- compact B35 trio ------------------------------------------------
    context_md = _render_context_md(context=context, procedure=procedure, verification=verification)
    if global_claims:
        context_md = _inject_global_claims(context_md, global_claims)
    if len(context_md.encode("utf-8")) > ctx_budget:
        context_md = (
            context_md[:ctx_budget]
            + f"\n... [TRUNCATED at {ctx_budget} bytes -- exceeded projection budget]\n"
        )
    run_json = _render_run_json(context=context, run_row=run_row, verification=verification)
    _enrich_run_json_coordination(run_json, intents)
    meta_json = _render_meta_json(
        run_row=run_row, workspace_root=workspace_root,
        scope_type=run_row.get("scope_type"), scope_entity_id=run_row.get("scope_entity_id"),
        revision=0,
    )

    # --- addressable per-type pages + indexes --------------------------
    claims_md, claims_rows = _build_claims_page(context, global_claims)
    procedures_md, procedures_rows = _build_procedures_page(
        context, procedure, verification, tuple(faulted.get("procedure", ())))
    implementations_md, impl_rows = _build_implementations_page(
        context, tuple(faulted.get("implementation", ())))
    run_md, run_rows = _build_run_page(context, intents)
    exploration_md, exploration_rows = render_exploration_page(workspace_root)

    claims_idx = render_idx(claims_rows, header="claims.idx  id|version|scope|status|tags|file|start|end|summary")
    procedures_idx = render_idx(procedures_rows, header="procedures.idx  id|version|scope|status|tags|file|start|end|summary")
    implementations_idx = render_idx(impl_rows, header="implementations.idx  id|version|scope|status|tags|file|start|end|summary")
    run_idx = render_idx(run_rows, header="run.idx  node|status|owner|deps|globs|file|start|end|summary")
    exploration_idx = render_idx(exploration_rows, header="exploration.idx  id|version|scope|status|tags|file|start|end|summary")

    for name, content in (("claims.idx", claims_idx), ("procedures.idx", procedures_idx),
                          ("implementations.idx", implementations_idx), ("run.idx", run_idx),
                          ("exploration.idx", exploration_idx)):
        if len(content.encode("utf-8")) > TYPE_IDX_MAX_BYTES:
            raise StealthProjectionError(
                f"{name} is {len(content)} bytes -- over the {TYPE_IDX_MAX_BYTES} working-set "
                "index budget; the projection pulled in too much"
            )

    has_expl = bool(exploration_rows)
    root_idx = render_root_idx(standard_root_rows(has_exploration=has_expl))
    if len(root_idx.encode("utf-8")) > ROOT_IDX_MAX_BYTES:
        raise StealthProjectionError(
            f"index/root.idx is {len(root_idx)} bytes -- over the {ROOT_IDX_MAX_BYTES} router budget"
        )

    # --- enrich meta.json --------------------------------------------
    now_iso = datetime.now(timezone.utc).isoformat()
    updated_at = run_row.get("updated_at")
    change_cursor = f"{run_row['id']}:{updated_at.isoformat() if updated_at else '0'}"
    faulted_counts = {k: len(v) for k, v in faulted.items() if v}
    file_list = [
        "context.md", "run.json", "meta.json",
        "claims.md", "procedures.md", "implementations.md", "run.md", "events.jsonl",
        "index/root.idx", "index/claims.idx", "index/procedures.idx",
        "index/implementations.idx", "index/run.idx",
    ]
    if has_expl:
        file_list += ["exploration.md", "index/exploration.idx"]
    meta_json.update({
        "schema": "stealth-projection/2",
        "workspace_id": str(run_row.get("scope_entity_id") or _short(hashlib.sha1(
            workspace_root.encode("utf-8")).hexdigest(), 12)),
        "change_cursor": change_cursor,
        "revisions": {
            "claims": len(claims_rows),
            "procedures": len(procedures_rows),
            "implementations": len(impl_rows),
            "run": len(run_rows),
            "exploration": len(exploration_rows),
        },
        "counts": {
            "claims": len(claims_rows),
            "procedures": len(procedures_rows),
            "implementations": len(impl_rows),
            "run_nodes": len([r for r in run_rows if r.node_id.startswith("N")]),
            "explorations": len(exploration_rows),
        },
        "faulted": faulted_counts,
        "coordination_declared": bool(intents),
        "generated_at": now_iso,
        "files": file_list,
    })

    # --- atomic batch write under the single-writer lock -------------
    stealth_dir = os.path.join(workspace_root, STEALTH_DIRNAME)
    index_dir = os.path.join(stealth_dir, "index")
    os.makedirs(index_dir, exist_ok=True)
    with SingleWriterLock(workspace_root):
        seq = append_events(
            workspace_root,
            [("projection_regenerated", {
                "procedure_run_id": procedure_run_id,
                "counts": meta_json["counts"],
                "faulted": faulted_counts,
            })],
            _lock_held=True,
        )[0]
        meta_json["projection_revision"] = seq

        plan: list[tuple[str, str]] = [
            (os.path.join(stealth_dir, "context.md"), context_md),
            (os.path.join(stealth_dir, "run.json"), json.dumps(run_json, indent=2, default=str)),
            (os.path.join(stealth_dir, "claims.md"), claims_md),
            (os.path.join(stealth_dir, "procedures.md"), procedures_md),
            (os.path.join(stealth_dir, "implementations.md"), implementations_md),
            (os.path.join(stealth_dir, "run.md"), run_md),
            (os.path.join(index_dir, "root.idx"), root_idx),
            (os.path.join(index_dir, "claims.idx"), claims_idx),
            (os.path.join(index_dir, "procedures.idx"), procedures_idx),
            (os.path.join(index_dir, "implementations.idx"), implementations_idx),
            (os.path.join(index_dir, "run.idx"), run_idx),
        ]
        if has_expl:
            plan.append((os.path.join(stealth_dir, "exploration.md"), exploration_md))
            plan.append((os.path.join(index_dir, "exploration.idx"), exploration_idx))
        plan.append((os.path.join(stealth_dir, "meta.json"), json.dumps(meta_json, indent=2, default=str)))
        atomic_write_batch(plan)

    paths = {os.path.basename(p) if not p.endswith(".idx") else "index/" + os.path.basename(p): p
             for p, _ in plan}
    return {
        "context_md": context_md,
        "run_json": run_json,
        "meta_json": meta_json,
        "claims_md": claims_md,
        "procedures_md": procedures_md,
        "implementations_md": implementations_md,
        "run_md": run_md,
        "exploration_md": exploration_md,
        "root_idx": root_idx,
        "claims_idx": claims_idx,
        "procedures_idx": procedures_idx,
        "implementations_idx": implementations_idx,
        "run_idx": run_idx,
        "exploration_idx": exploration_idx,
        "journal_seq": seq,
        "paths": paths,
    }


def _inject_global_claims(context_md: str, global_claims: tuple[MdBlock, ...]) -> str:
    """Replace the honest-empty `[RELEVANT GLOBAL CLAIMS]` body with the
    real faulted-in claim ids once a page fault has pulled some in. The
    no-fault case keeps the "never fabricated as empty-but-real" text."""
    marker = "[RELEVANT GLOBAL CLAIMS]"
    if marker not in context_md:
        return context_md
    head, _, rest = context_md.partition(marker)
    _, _, after = rest.partition("\n\n")
    lines = [marker]
    for b in global_claims:
        lines.append(f"  {b.obj_id}  {b.summary}  (grep {b.obj_id} .stealth/index/claims.idx)")
    return head + "\n".join(lines) + "\n\n" + after


def _enrich_run_json_coordination(run_json: dict, intents: dict[int, dict]) -> None:
    """Populate `node_owners` / `file_intents` from live declarations;
    the empty defaults stay when no coordination is declared (so
    `_render_run_json`'s own offline contract is unchanged)."""
    if not intents:
        return
    owners: dict[str, str] = {}
    file_intents: list[dict] = []
    for order, it in sorted(intents.items()):
        owner = it.get("owner_agent_id")
        if owner:
            owners[str(order)] = owner
        file_intents.append({
            "node_order": order,
            "owner": owner,
            "read_exact": it.get("read_exact") or [],
            "read_globs": it.get("read_globs") or [],
            "write_exact": it.get("write_exact") or [],
            "write_globs": it.get("write_globs") or [],
            "symbols_expected_to_modify": it.get("symbols_expected_to_modify") or [],
            "lease_expires_at": it.get("file_intent_lease_expires_at"),
        })
    run_json["node_owners"] = owners
    run_json["file_intents"] = file_intents
