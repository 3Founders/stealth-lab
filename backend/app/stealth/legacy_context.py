"""
The original B35 single-file renderers -- `context.md`, `run.json`,
`meta.json` -- moved here VERBATIM from `app.execution.stealth_projection`
so both the new multi-file generator and the back-compat shim share one
copy. The shim re-exports these names, so
`from app.execution.stealth_projection import _render_context_md` (and the
offline test that does exactly that) keeps working.

`context.md` stays the compact, bounded, index-first router the spec's
B35 asks for. The addressable detail pages (`claims.md` / `procedures.md`
/ ...) that the ratified local-architecture decision adds are produced
alongside it by `app.stealth.generator`, from the same canonical reads.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

STEALTH_DIRNAME = ".stealth"
# B35: "context.md MUST be index-first and block-addressable... the first
# router section MUST stay below a configured byte/token budget." Enforced
# by the generator, asserted by test_stealth_projection_*.
CONTEXT_MD_MAX_BYTES = 8192


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
