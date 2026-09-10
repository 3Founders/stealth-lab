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

A broader relevant-global-Claim projection and the "knowledge page fault"
refill loop are P3; multi-agent owners / file-intents in `run.*` are P4.
Until then those sections are honestly empty, never fabricated.

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
)
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


def _build_claims_page(context: dict[str, Any]) -> tuple[str, list[IdxRow]]:
    blocks: list[MdBlock] = []
    for pc in context.get("required_preconditions") or []:
        oid = _pc_id(pc)
        subj, pred, obj = pc.get("subject", ""), pc.get("predicate", ""), pc.get("object", "")
        status = pc.get("status", "UNKNOWN")
        summary = f"{subj} {pred} {obj} -> {status}"
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
            scope="local",
            status=status,
            tags=("precondition",),
            summary=summary,
        ))
    if not blocks:
        page = ("# claims.md -- GENERATED, not canonical. Do not hand-edit.\n\n"
                "(no structured preconditions for the selected procedure; "
                "relevant-global-Claim projection is P3)\n")
        return page, []
    rendered = render_md_page("claims.md", blocks)
    rows = [
        IdxRow(
            obj_id=b.obj_id, version="-", scope=b.scope, status=b.status, tags=b.tags,
            file="claims.md", start=rendered.ranges[b.obj_id][0], end=rendered.ranges[b.obj_id][1],
            summary=b.summary,
        )
        for b in blocks
    ]
    return rendered.text, rows


def _build_procedures_page(
    context: dict[str, Any], procedure: dict, verification: dict[str, Any],
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
    rendered = render_md_page("procedures.md", [block])
    s, e = rendered.ranges[pid]
    row = IdxRow(
        obj_id=pid, version=block.version, scope=block.scope, status=block.status, tags=block.tags,
        file="procedures.md", start=s, end=e, summary=block.summary,
    )
    return rendered.text, [row]


def _build_implementations_page(context: dict[str, Any]) -> tuple[str, list[IdxRow]]:
    impls = context.get("recommended_implementations") or []
    if not impls:
        page = ("# implementations.md -- GENERATED, not canonical. Do not hand-edit.\n\n"
                "(no implementation resolved for the current node -- "
                "MISSING_IMPLEMENTATION, not fabricated)\n")
        return page, []
    blocks: list[MdBlock] = []
    for impl in impls:
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
    rendered = render_md_page("implementations.md", blocks)
    rows = [
        IdxRow(
            obj_id=b.obj_id, version=b.version, scope="-", status=b.status, tags=b.tags,
            file="implementations.md", start=rendered.ranges[b.obj_id][0],
            end=rendered.ranges[b.obj_id][1], summary=b.summary,
        )
        for b in blocks
    ]
    return rendered.text, rows


def _build_run_page(context: dict[str, Any]) -> tuple[str, list[RunIdxRow]]:
    blocks: list[MdBlock] = []
    nodes = context.get("nodes", [])
    for n in nodes:
        nid = f"N{n['node_order']}"
        deps = [f"N{d}" for d in (n.get("deps") or [])]
        body = [
            kv("objective", n.get("goal") or ""),
            kv("status", n["status"]),
            kv("owner", "-"),  # P4: multi-agent ownership
            kv("depends_on", ", ".join(deps) or "-"),
            kv("verification", n.get("verification_state") or "PENDING"),
        ]
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
        rows.append(RunIdxRow(
            node_id=b.obj_id, status=b.status, owner="-", deps=deps, write_globs=(),
            file="run.md", start=s, end=e, summary=b.summary,
        ))
    return rendered.text, rows


def _revision() -> int:
    return int(datetime.now(timezone.utc).timestamp())


async def generate_projection(
    pool: asyncpg.Pool, *, workspace_root: str, procedure_run_id: str,
    context_md_max_bytes: int | None = None,
    include_addressable_pages: bool = False,
) -> dict[str, Any]:
    """
    Regenerate `.stealth/` under `workspace_root` for `procedure_run_id`
    from canonical state only.

    B35 STRICT CLOSURE (re-read against the literal spec text): B35's
    own words are "Use ONE compact human/agent-readable context file
    plus machine-readable runtime files: context.md, run.json,
    meta.json... Do NOT maintain large duplicated claims.md,
    procedures.md, implementations.md, run.md... files UNLESS an
    existing integration strictly requires them." No real caller
    anywhere in this codebase (grepped: only this module's own tests)
    reads `claims.md`/`procedures.md`/`implementations.md`/`run.md`/
    `index/*.idx` -- the "existing integration" exception does not
    apply. The compact trio is therefore the literal, canonical,
    UNCONDITIONAL default (`include_addressable_pages` defaults False);
    both real production call sites (`server.py`'s `_respond_plan_only`/
    `continue_run`) call this with no override and now get exactly the
    compact trio, nothing else.

    The addressable-page architecture (`claims.md`/`procedures.md`/
    `implementations.md`/`run.md` + `index/*.idx`) that a PRIOR pass in
    this codebase built is real, tested, and NOT deleted here (CLAUDE.md
    rule 2: don't discard working code) -- it becomes an explicit
    opt-in (`include_addressable_pages=True`) for a caller with a real,
    demonstrated integration need for it, exactly the shape B35's own
    exception clause describes, rather than an unconditional default
    that clause does not license.

    Returns `{context_md, run_json, meta_json, paths}` always;
    `{claims_md, procedures_md, implementations_md, run_md, root_idx,
    claims_idx, procedures_idx, implementations_idx, run_idx}` are
    additionally present, and `paths` additionally includes them, only
    when `include_addressable_pages=True`.

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

    # B35 remaining projection gaps, closed against the now-real B30/B36
    # systems: relevant GLOBAL Claims (get_relevant_claims, bounded,
    # never the whole graph) and live multi-agent file-intent
    # coordination (real execution_run_nodes declarations -- B36).
    # Both informational: a failure here must never block projection
    # generation itself.
    relevant_claim_refs: list[dict] = []
    objective = context.get("objective")
    if objective:
        try:
            from app.services.relevant_claims import get_relevant_claims
            relevant_claim_refs = await get_relevant_claims(pool, goal=objective, top_k=5)
        except Exception:  # noqa: BLE001
            relevant_claim_refs = []

    file_intent_rows = await pool.fetch(
        "SELECT node_order, owner_agent_id, read_exact, read_globs, write_exact, write_globs, "
        "symbols_expected_to_modify, file_intent_lease_expires_at FROM execution_run_nodes "
        "WHERE execution_run_id = $1::uuid AND file_intent_lease_expires_at IS NOT NULL "
        "AND file_intent_lease_expires_at > now() "
        "AND (write_exact != '[]' OR write_globs != '[]' OR read_exact != '[]' OR read_globs != '[]')",
        procedure_run_id,
    )
    file_intents = [dict(r) for r in file_intent_rows]

    # --- compact B35 trio (unchanged behaviour) ---------------------------
    context_md = _render_context_md(
        context=context, procedure=procedure, verification=verification,
        relevant_claim_refs=relevant_claim_refs, file_intents=file_intents,
    )
    if len(context_md.encode("utf-8")) > ctx_budget:
        context_md = (
            context_md[:ctx_budget]
            + f"\n... [TRUNCATED at {ctx_budget} bytes -- exceeded projection budget]\n"
        )
    run_json = _render_run_json(
        context=context, run_row=run_row, verification=verification, file_intents=file_intents,
    )
    meta_json = _render_meta_json(
        run_row=run_row, workspace_root=workspace_root,
        scope_type=run_row.get("scope_type"), scope_entity_id=run_row.get("scope_entity_id"),
        revision=_revision(),
    )

    # --- addressable per-type pages + indexes (B35 STRICT CLOSURE:
    # opt-in only -- see this function's own docstring for why the
    # literal spec text makes the compact trio above the unconditional
    # default) --------------------------------------------------------
    claims_md = procedures_md = implementations_md = run_md = None
    claims_idx = procedures_idx = implementations_idx = run_idx = root_idx = None
    claims_rows: list = []
    procedures_rows: list = []
    impl_rows: list = []
    run_rows: list = []
    if include_addressable_pages:
        claims_md, claims_rows = _build_claims_page(context)
        procedures_md, procedures_rows = _build_procedures_page(context, procedure, verification)
        implementations_md, impl_rows = _build_implementations_page(context)
        run_md, run_rows = _build_run_page(context)

        claims_idx = render_idx(claims_rows, header="claims.idx  id|version|scope|status|tags|file|start|end|summary")
        procedures_idx = render_idx(procedures_rows, header="procedures.idx  id|version|scope|status|tags|file|start|end|summary")
        implementations_idx = render_idx(impl_rows, header="implementations.idx  id|version|scope|status|tags|file|start|end|summary")
        run_idx = render_idx(run_rows, header="run.idx  node|status|owner|deps|globs|file|start|end|summary")

        for name, content in (("claims.idx", claims_idx), ("procedures.idx", procedures_idx),
                              ("implementations.idx", implementations_idx), ("run.idx", run_idx)):
            if len(content.encode("utf-8")) > TYPE_IDX_MAX_BYTES:
                raise StealthProjectionError(
                    f"{name} is {len(content)} bytes -- over the {TYPE_IDX_MAX_BYTES} working-set "
                    "index budget; the projection pulled in too much"
                )

        root_idx = render_root_idx([
            RootRow("claims", "claims.idx", "facts, preconditions, assumptions in scope"),
            RootRow("procedures", "procedures.idx", "the selected procedure + steps"),
            RootRow("implementations", "implementations.idx", "resolved executors/tools"),
            RootRow("run", "run.idx", "current nodes, status, blockers"),
        ])
        if len(root_idx.encode("utf-8")) > ROOT_IDX_MAX_BYTES:
            raise StealthProjectionError(
                f"index/root.idx is {len(root_idx)} bytes -- over the {ROOT_IDX_MAX_BYTES} router budget"
            )

    # --- enrich meta.json ----------------------------------------------
    now_iso = datetime.now(timezone.utc).isoformat()
    updated_at = run_row.get("updated_at")
    change_cursor = f"{run_row['id']}:{updated_at.isoformat() if updated_at else '0'}"
    files = ["context.md", "run.json", "meta.json"]
    meta_json.update({
        "schema": "stealth-projection/1",
        "workspace_id": str(run_row.get("scope_entity_id") or _short(hashlib.sha1(
            workspace_root.encode("utf-8")).hexdigest(), 12)),
        "change_cursor": change_cursor,
        "generated_at": now_iso,
    })
    if include_addressable_pages:
        meta_json.update({
            "revisions": {
                "claims": len(claims_rows),
                "procedures": len(procedures_rows),
                "implementations": len(impl_rows),
                "run": len(run_rows),
            },
            "counts": {
                "claims": len(claims_rows),
                "procedures": len(procedures_rows),
                "implementations": len(impl_rows),
                "run_nodes": len([r for r in run_rows if r.node_id.startswith("N")]),
            },
        })
        files += [
            "claims.md", "procedures.md", "implementations.md", "run.md",
            "index/root.idx", "index/claims.idx", "index/procedures.idx",
            "index/implementations.idx", "index/run.idx",
        ]
    meta_json["files"] = files

    # --- atomic batch write (meta.json LAST) --------------------------
    stealth_dir = os.path.join(workspace_root, STEALTH_DIRNAME)
    index_dir = os.path.join(stealth_dir, "index")
    plan: list[tuple[str, str]] = [
        (os.path.join(stealth_dir, "context.md"), context_md),
        (os.path.join(stealth_dir, "run.json"), json.dumps(run_json, indent=2, default=str)),
    ]
    if include_addressable_pages:
        plan += [
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
    # meta.json LAST regardless -- a reader keying off its own revision
    # must never see a partial set, in either mode.
    plan.append((os.path.join(stealth_dir, "meta.json"), json.dumps(meta_json, indent=2, default=str)))
    atomic_write_batch(plan)

    paths = {os.path.basename(p) if not p.endswith(".idx") else "index/" + os.path.basename(p): p
             for p, _ in plan}
    result = {
        "context_md": context_md,
        "run_json": run_json,
        "meta_json": meta_json,
        "paths": paths,
    }
    if include_addressable_pages:
        result.update({
            "claims_md": claims_md,
            "procedures_md": procedures_md,
            "implementations_md": implementations_md,
            "run_md": run_md,
            "root_idx": root_idx,
            "claims_idx": claims_idx,
            "procedures_idx": procedures_idx,
            "implementations_idx": implementations_idx,
            "run_idx": run_idx,
        })
    return result
