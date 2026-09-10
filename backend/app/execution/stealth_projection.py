"""
MCP hardening B35/A29: the `.stealth/` local working-set projection.

`.stealth/context.md` / `run.json` / `meta.json` are a GENERATED,
disposable projection of canonical database state -- never a second
knowledge store (B35's own rule). This module builds them from EXISTING
reads only:

  - `app.execution.durable_resume.get_run_context` (continue_run's own
    engine) supplies the current node/objective/preconditions/
    recommended-implementations working set -- already bounded to one
    run's current node, exactly the "only the current working set,
    never the entire global graph" requirement.
  - `app.services.verification.evaluate_run_completion` supplies
    verification state.

No new retrieval mechanism, no new Claim/Procedure query path. A
genuinely separate `get_relevant_claims` retrieval surface (B30) does
not exist yet (see MCP_HARDENING_DEFERRED_ITEMS.md) -- `[RELEVANT GLOBAL
CLAIMS]` says so honestly rather than fabricating content.

Writes are atomic (write to a temp file in the same directory, then
`os.replace` -- POSIX/Windows-portable atomic rename) so a reader never
observes a half-written file. This projection is NEVER accepted as
canonical truth by anything in this codebase -- `generate_projection`
is a pure, idempotent regeneration from canonical state; there is no
"read .stealth/ and trust it" path anywhere.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg

STEALTH_DIRNAME = ".stealth"
# B35: "context.md MUST be index-first and block-addressable... the
# first router section MUST stay below a configured byte/token budget."
# A hard cap here is a real, enforced check (test_stealth_projection_*
# asserts against it), not just documentation.
CONTEXT_MD_MAX_BYTES = 8192


class StealthProjectionError(Exception):
    pass


def _atomic_write(path: str, content: str) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-stealth-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _render_context_md(
    *, context: dict[str, Any], procedure: dict, verification: dict[str, Any],
) -> str:
    lines: list[str] = []
    lines.append("# .stealth/context.md -- GENERATED, not canonical. Do not hand-edit.")
    lines.append("")
    lines.append("[ROUTER]")
    lines.append(f"run: {context['procedure_run_id']} | status: {context['status']} | "
                 f"phase: {context['current_phase_or_node']}")
    lines.append(f"procedure: {context['procedure_id']}@{context['procedure_version']} "
                 f"\"{procedure.get('name', '')}\"")
    lines.append("grep -A20 '\\[SELECTED PROCEDURES\\]' .stealth/context.md   -- full procedure detail")
    lines.append("grep -A20 '\\[RELEVANT IMPLEMENTATIONS\\]' .stealth/context.md -- implementation candidates")
    lines.append("grep -A20 '\\[COORDINATION\\]' .stealth/context.md          -- multi-agent state")
    lines.append("")

    lines.append("[LOCAL CLAIMS]")
    if context["required_preconditions"]:
        for p in context["required_preconditions"]:
            lines.append(f"  {p['subject']} {p['predicate']} {p['object']} -> {p['status']}")
    else:
        lines.append("  (this procedure declares no structured preconditions)")
    lines.append("")

    lines.append("[RELEVANT GLOBAL CLAIMS]")
    lines.append("  (not projected -- get_relevant_claims is not yet a distinct retrieval")
    lines.append("   surface separate from precondition checking; see B30 in")
    lines.append("   MCP_HARDENING_DEFERRED_ITEMS.md. Never fabricated as empty-but-real.)")
    lines.append("")

    lines.append("[SELECTED PROCEDURES]")
    lines.append(f"  id: {context['procedure_id']}@{context['procedure_version']}")
    lines.append(f"  goal: {procedure.get('goal', '')}")
    lines.append("  steps:")
    for n in context["nodes"]:
        lines.append(f"    {n['node_order']}. [{n['status']}] {n.get('goal') or ''}")
    if procedure.get("postconditions"):
        lines.append("  postconditions:")
        by_id = {c["criterion_id"]: c for c in verification["criteria"]}
        for i, statement in enumerate(procedure["postconditions"]):
            text = statement if isinstance(statement, str) else statement.get("statement", "")
            state = by_id.get(f"postcondition:{i}", {}).get("state", "inconclusive")
            lines.append(f"    - {text} [{state}]")
    lines.append("")

    lines.append("[RELEVANT IMPLEMENTATIONS]")
    if context["recommended_implementations"]:
        for impl in context["recommended_implementations"]:
            role = impl.get("role") or impl.get("kind") or ""
            lines.append(f"  {impl['implementation_id']} role={role} source={impl['source']}")
    else:
        lines.append("  (none resolved for the current node -- MISSING_IMPLEMENTATION, not fabricated)")
    lines.append("")

    lines.append("[COORDINATION]")
    if context.get("waiting_child"):
        wc = context["waiting_child"]
        lines.append(f"  waiting on child run {wc['child_run_id']} (status={wc['child_status']})")
    else:
        lines.append("  no multi-agent file-intent coordination declared for this run (B36 not yet built)")
    lines.append("")

    return "\n".join(lines)


def _render_run_json(
    *, context: dict[str, Any], run_row: dict, verification: dict[str, Any],
) -> dict:
    return {
        "procedure_run_id": context["procedure_run_id"],
        "execution_plan_id": str(run_row["execution_plan_id"]),
        "task_graph_id": str(run_row["task_graph_id"]),
        "procedure_id": context["procedure_id"],
        "procedure_version": context["procedure_version"],
        "parent_run_id": context.get("parent_run_id"),
        "root_run_id": context.get("root_run_id"),
        "status": context["status"],
        "node_states": [
            {"node_order": n["node_order"], "status": n["status"], "deps": n.get("deps") or []}
            for n in context["nodes"]
        ],
        "node_owners": {},  # B36 (multi-agent coordination) not yet built -- honest empty, not fabricated
        "file_intents": [],  # B36 not yet built
        "implementation_bindings": context["recommended_implementations"],
        "verification_state": verification["overall_state"],
        "change_cursor": run_row["updated_at"].isoformat() if run_row.get("updated_at") else None,
    }


def _render_meta_json(
    *, run_row: dict, workspace_root: str, scope_type: Optional[str], scope_entity_id: Optional[str],
    revision: int,
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "projection_revision": revision,
        "generated_at": now.isoformat(),
        "workspace_root": workspace_root,
        "scope_type": scope_type,
        "scope_entity_id": scope_entity_id,
        "canonical_run_updated_at": run_row["updated_at"].isoformat() if run_row.get("updated_at") else None,
        "sync_cursor": str(run_row["id"]) + ":" + (
            run_row["updated_at"].isoformat() if run_row.get("updated_at") else "0"
        ),
    }


async def generate_projection(
    pool: asyncpg.Pool, *, workspace_root: str, procedure_run_id: str,
) -> dict[str, Any]:
    """
    Regenerates `.stealth/{context.md,run.json,meta.json}` under
    `workspace_root` for `procedure_run_id`, from canonical state only.
    Returns `{context_md, run_json, meta_json, paths}` for callers that
    want the content without re-reading the files (e.g. tests, or a
    caller returning it inline in an MCP response).

    Raises `StealthProjectionError` if `procedure_run_id` does not
    exist -- never writes a fabricated/empty projection for a run that
    isn't real.
    """
    from app.execution.durable_resume import get_run_context
    from app.execution.procedure_graph import fetch_procedure_version
    from app.services.verification import evaluate_run_completion

    run_row = await pool.fetchrow("SELECT * FROM execution_runs WHERE id = $1::uuid", procedure_run_id)
    if run_row is None:
        raise StealthProjectionError(f"procedure_run_id {procedure_run_id!r} not found")

    context = await get_run_context(pool, procedure_run_id)
    procedure = await fetch_procedure_version(pool, run_row["procedure_id"], run_row["procedure_version"]) or {}
    verification = await evaluate_run_completion(pool, execution_run_id=procedure_run_id, procedure=procedure)

    context_md = _render_context_md(context=context, procedure=procedure, verification=verification)
    if len(context_md.encode("utf-8")) > CONTEXT_MD_MAX_BYTES:
        # B35's own bounded-router rule, enforced: truncate with an
        # honest marker rather than silently exceeding the budget. A
        # single run's context should never realistically hit this --
        # if it does, that is itself worth surfacing, not hiding.
        context_md = (
            context_md[:CONTEXT_MD_MAX_BYTES]
            + f"\n... [TRUNCATED at {CONTEXT_MD_MAX_BYTES} bytes -- exceeded projection budget]\n"
        )

    run_json = _render_run_json(context=context, run_row=run_row, verification=verification)
    meta_json = _render_meta_json(
        run_row=run_row, workspace_root=workspace_root,
        scope_type=run_row.get("scope_type"), scope_entity_id=run_row.get("scope_entity_id"),
        revision=int(datetime.now(timezone.utc).timestamp()),
    )

    stealth_dir = os.path.join(workspace_root, STEALTH_DIRNAME)
    paths = {
        "context_md": os.path.join(stealth_dir, "context.md"),
        "run_json": os.path.join(stealth_dir, "run.json"),
        "meta_json": os.path.join(stealth_dir, "meta.json"),
    }
    _atomic_write(paths["context_md"], context_md)
    _atomic_write(paths["run_json"], json.dumps(run_json, indent=2, default=str))
    _atomic_write(paths["meta_json"], json.dumps(meta_json, indent=2, default=str))

    return {"context_md": context_md, "run_json": run_json, "meta_json": meta_json, "paths": paths}
