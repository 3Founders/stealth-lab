"""
MCP Resources for StealthLab -- canonical, read-only, id-addressable
knowledge objects.

WHY THIS EXISTS
---------------
The MCP surface (server.py) was entirely @server.tool()-centric: ~29 tools,
several of which are pure "fetch noun by id" reads. The MCP protocol has a
dedicated primitive for exactly that -- Resources -- and hosts use it for
progressive disclosure (start from a Prompt + a few discovery tools, pull
detail via Resources) instead of reasoning over every tool every turn.

CONTRACT
--------
Every resource here:
  * is READ-ONLY -- it never mutates. Mutations stay @server.tool().
  * resolves the caller's visibility scope with server._caller_access_scope()
    (the MCP analogue of the REST get_scope dependency) -- NEVER
    AccessScope.unrestricted(). A private object is invisible, same as the
    tools.
  * calls an EXISTING service function -- no new DB logic, no second
    retrieval/execution stack.
  * returns human+agent-readable Markdown.
  * fails cleanly on an unknown / out-of-scope id: a short "# Not found"
    body, never a fabricated object and never an unhandled exception.
  * surfaces verification / approval / staleness state verbatim -- a
    candidate procedure is rendered as a candidate, never as "verified".

The handler functions are module-level (not closures) so they can be
unit-tested by direct call, exactly like the @server.tool() functions in
server.py. register_resources() binds them to their URI templates.

NOTE: the "resources" inside an imported SKILL.md package (files, scripts,
references) are a different thing entirely -- those are source-package
assets, not MCP protocol Resources.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from mcp.server.mcpserver import Context

from app.execution import durable_resume as _dres
from app.execution import implementation_registry as _impl
from app.services import claim_graph_api as _claims
from app.services import procedure_graph_api as _pg
from app.services import product_model as _pm

_NOT_FOUND = (
    "# Not found\n\nNo visible object for that id "
    "(it does not exist, or your scope cannot see it)."
)


# --------------------------------------------------------------------------
# tiny markdown helpers -- deterministic, no side effects
# --------------------------------------------------------------------------
def _kv(label: str, value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    return f"**{label}:** {value}\n"


def _section(title: str, body: str) -> str:
    body = (body or "").strip()
    if not body:
        return ""
    return f"\n## {title}\n\n{body}\n"


def _bullets(items: Any) -> str:
    out = []
    for it in items or []:
        if isinstance(it, dict):
            for k in ("description", "text", "goal", "statement", "expr",
                      "when", "clause", "name"):
                if it.get(k):
                    out.append(f"- {it[k]}")
                    break
            else:
                out.append(f"- `{json.dumps(it, default=str)[:300]}`")
        else:
            out.append(f"- {it}")
    return "\n".join(out)


def _numbered_steps(steps: Any) -> str:
    out = []
    for i, s in enumerate(steps or [], 1):
        if isinstance(s, dict):
            txt = s.get("goal") or s.get("description") or s.get("action") or s.get("name") or ""
        else:
            txt = str(s)
        if txt:
            out.append(f"{i}. {txt}")
    return "\n".join(out)


def _fence(obj: Any) -> str:
    return "```json\n" + json.dumps(obj, indent=2, default=str) + "\n```"


def _scope():
    # Late import: this module is imported from the bottom of server.py, so a
    # top-level import would be circular. By the time any handler runs, the
    # server module is fully loaded.
    from app.mcp_server.server import _caller_access_scope

    return _caller_access_scope()


def _pool(ctx: Context):
    return ctx.request_context.lifespan_context["pool"]


# --------------------------------------------------------------------------
# handlers (module-level, directly unit-testable)
# --------------------------------------------------------------------------
async def procedure_resource(procedure_id: str, ctx: Context) -> str:
    """stealth://procedures/{procedure_id} -- canonical procedure detail.
    Accepts the stable procedure_id handle OR a version row id."""
    from app.mcp_server.server import _resolve_live_procedure
    from app.services.applicability import ProcedureNotFound

    scope = _scope()
    pool = _pool(ctx)
    try:
        live = await _resolve_live_procedure(pool, procedure_id)
    except ProcedureNotFound:
        return _NOT_FOUND
    detail = await _pg.get_procedure_detail(pool, str(live["id"]), scope=scope)
    if detail is None:
        return _NOT_FOUND

    md = f"# Procedure: {detail.get('display_name') or detail['name']}\n\n"
    md += _kv("ID (handle)", detail["procedure_id"])
    md += _kv("Version row id", detail["id"])
    md += _kv("Version", detail.get("version"))
    md += _kv("Technical name", detail["name"])
    md += _kv("Verification state", detail.get("verification_state"))
    md += _kv("Approval status", detail.get("approval_status"))
    md += _kv("Staleness", detail.get("staleness"))
    md += _kv("Availability", detail.get("availability"))
    md += _kv("Domain", detail.get("domain"))
    if detail.get("scope_type"):
        md += _kv("Scope", f"{detail.get('scope_type')}:{detail.get('scope_entity_id')}")
    md += _section("Capability", detail.get("display_description") or detail.get("goal") or "")
    md += _section("Use when", detail.get("applicability_summary") or "")
    md += _section("Preconditions", _bullets(detail.get("preconditions")))
    md += _section("Steps", _numbered_steps(detail.get("steps")))
    md += _section("Constraints / invariants", _bullets(detail.get("invariants")))
    md += _section("Failure modes", _bullets(detail.get("failure_modes")))
    ev = detail.get("evidence_summary") or {}
    if ev.get("total"):
        md += _section(
            "Evidence",
            f"{ev.get('total', 0)} recorded outcome(s): "
            f"{ev.get('success_count', 0)} success, {ev.get('failure_count', 0)} failure.",
        )
    if detail.get("claims"):
        md += _section("Precondition-referenced claims", _bullets(detail["claims"]))
    if detail.get("implementation_kinds"):
        md += _section("Advertised implementation kinds", ", ".join(detail["implementation_kinds"]))
    prov = "".join(filter(None, [
        _kv("source", detail.get("provenance")),
        _kv("created_by", detail.get("created_by")),
        _kv("visibility", detail.get("visibility")),
    ]))
    md += _section("Provenance", prov)
    return md.rstrip() + "\n"


async def problem_resource(problem_id: str, ctx: Context) -> str:
    """stealth://problems/{problem_id} -- problem + benchmarks + solutions + leaderboard."""
    scope = _scope()
    pool = _pool(ctx)
    p = await _pm.get_problem(pool, problem_id, scope=scope)
    if p is None:
        return _NOT_FOUND
    benchmarks = await _pm.list_problem_benchmarks(pool, problem_id, scope=scope)
    solutions = await _pm.list_problem_solutions(pool, problem_id, scope=scope)
    lb = await _pm.problem_leaderboard(pool, problem_id, scope=scope)

    md = f"# Problem: {p.get('title') or problem_id}\n\n"
    md += _kv("ID", p.get("id"))
    md += _kv("Status", p.get("status"))
    md += _kv("Objective", p.get("objective"))
    md += _section("Description", p.get("description") or "")
    md += _section("Benchmarks", _bullets([
        {"description": f"{b.get('name') or b.get('id')} ({b.get('status', 'unknown')})"}
        for b in benchmarks
    ]) or "_none_")
    md += _section("Associated solutions", _bullets([
        {"description": f"{s.get('solution_id') or s.get('id')} "
                        f"-- {s.get('relationship', 'candidate')}"}
        for s in solutions
    ]) or "_none_")
    best = lb.get("current_best") or []
    if best:
        body = _bullets([{"description": str(x)} for x in best])
        if len(best) > 1:
            body += "\n\n_tie_"
        md += _section("Current best (verified, evidence-derived)", body)
    else:
        md += _section("Current best (verified, evidence-derived)", "_no verified solution yet_")
    return md.rstrip() + "\n"


async def problem_solutions_resource(problem_id: str, ctx: Context) -> str:
    """stealth://problems/{problem_id}/solutions -- association rows only."""
    scope = _scope()
    pool = _pool(ctx)
    if await _pm.get_problem(pool, problem_id, scope=scope) is None:
        return _NOT_FOUND
    rows = await _pm.list_problem_solutions(pool, problem_id, scope=scope)
    md = f"# Solutions for problem `{problem_id}`\n\n"
    if not rows:
        return md + "_none associated yet_\n"
    return md + _fence(rows)


async def claim_resource(claim_id: str, ctx: Context) -> str:
    """stealth://claims/{claim_id} -- one structured claim + its live evidence."""
    scope = _scope()
    pool = _pool(ctx)
    c = await _claims.get_claim(pool, claim_id, scope=scope)
    if c is None:
        return _NOT_FOUND
    evidence = await _claims.get_claim_evidence_api(pool, claim_id, scope=scope)
    md = f"# Claim: {c.get('statement') or claim_id}\n\n"
    md += _kv("ID", c.get("id"))
    md += _kv("Subject", c.get("subject"))
    md += _kv("Predicate", c.get("predicate"))
    md += _kv("Object", c.get("object"))
    md += _kv("Truth state", c.get("truth_state"))
    md += _kv("Epistemic status", c.get("epistemic_status") or c.get("status"))
    if c.get("scope_type"):
        md += _kv("Scope", f"{c.get('scope_type')}:{c.get('scope_entity_id')}")
    md += _section("Evidence", _bullets([
        {"description": f"{e.get('evidence_type', 'evidence')} "
                        f"({e.get('relation', e.get('stance', 'supports'))})"}
        for e in evidence
    ]) or "_none recorded_")
    return md.rstrip() + "\n"


async def evaluation_resource(evaluation_id: str, ctx: Context) -> str:
    """stealth://evaluations/{evaluation_id} -- version-pinned result + lineage."""
    scope = _scope()
    pool = _pool(ctx)
    e = await _pm.get_evaluation(pool, evaluation_id, scope=scope)
    if e is None:
        return _NOT_FOUND
    md = f"# Evaluation `{evaluation_id}`\n\n"
    md += _kv("Status", e.get("status"))
    md += _kv("Problem", e.get("problem_id"))
    md += _kv("Benchmark", e.get("benchmark_id"))
    md += _kv("Solution", e.get("solution_id"))
    md += _kv("Procedure version", e.get("procedure_version_id") or e.get("procedure_id"))
    md += _kv("Implementation", e.get("implementation_id"))
    if e.get("metrics"):
        md += _section("Metrics", _fence(e.get("metrics")))
    if e.get("verification"):
        md += _section("Verification summary", _fence(e.get("verification")))
    md += _section("Linked executions", _bullets(
        [{"description": str(x)} for x in (e.get("executions") or [])]
    ) or "_none_")
    return md.rstrip() + "\n"


async def implementation_resource(implementation_id: str, ctx: Context) -> str:
    """stealth://implementations/{implementation_id} -- secret-free descriptor."""
    scope = _scope()
    pool = _pool(ctx)
    desc = await _impl.get_descriptor(pool, implementation_id, scope=scope)
    if desc is None:
        return _NOT_FOUND
    md = f"# Implementation `{implementation_id}`\n\n"
    md += _kv("Kind", desc.get("kind"))
    md += _kv("Provider", desc.get("provider"))
    md += _kv("Version", desc.get("version"))
    md += _kv("Status", desc.get("status"))
    md += _kv("Verification status", desc.get("verification_status"))
    md += _kv("Protocol", desc.get("protocol"))
    md += _section("Descriptor", _fence(desc))
    md += "\n_Secret material (auth) is sanitised to references only by the registry._\n"
    return md.rstrip() + "\n"


async def task_implementations_resource(task_node_id: str, ctx: Context) -> str:
    """stealth://tasks/{task_node_id}/implementations -- active links only."""
    scope = _scope()
    pool = _pool(ctx)
    rows = await _impl.get_for_task(pool, task_node_id, scope=scope)
    md = f"# Implementations for task node `{task_node_id}`\n\n"
    if not rows:
        return md + "_no active implementations linked_\n"
    for r in rows:
        md += (f"- `{r.get('id')}` -- {r.get('kind')}/{r.get('provider')} "
               f"v{r.get('version')} ({r.get('status')}, "
               f"{r.get('verification_status', 'unverified')})\n")
    return md


async def run_resource(run_id: str, ctx: Context) -> str:
    """stealth://runs/{run_id} -- durable run status + per-node history. Read-only."""
    pool = _pool(ctx)
    status = await _dres.run_status_by_id(pool, run_id)
    if status is None:
        return _NOT_FOUND
    history = await _dres.node_history_by_id(pool, run_id)
    md = f"# Execution run `{run_id}`\n\n"
    md += _kv("Status", status.get("status") if isinstance(status, dict) else status)
    md += _section("Status detail", _fence(status))
    if history:
        md += _section("Per-node history", _fence(history))
    return md.rstrip() + "\n"


_RESOURCES = [
    ("stealth://procedures/{procedure_id}", "procedure", "Procedure",
     "One procedure's canonical detail: capability, applicability, steps, "
     "preconditions, constraints, failure modes, evidence summary, "
     "verification/approval state, provenance. Accepts the stable procedure_id "
     "handle or a version row id.", procedure_resource),
    ("stealth://problems/{problem_id}", "problem", "Problem",
     "One Problem with its benchmarks, associated Solutions and the "
     "evidence-derived leaderboard (current best VERIFIED solution, or none yet).",
     problem_resource),
    ("stealth://problems/{problem_id}/solutions", "problem-solutions", "Problem solutions",
     "Every Solution associated with a Problem (association rows; target objects "
     "are read via their own resources).", problem_solutions_resource),
    ("stealth://claims/{claim_id}", "claim", "Claim",
     "One structured epistemic claim: subject/predicate/object, truth state, "
     "epistemic status, and its live evidence.", claim_resource),
    ("stealth://evaluations/{evaluation_id}", "evaluation", "Evaluation",
     "One Evaluation: version-pinned procedure/implementation, recomputed metrics, "
     "verification summary, status, linked execution ids.", evaluation_resource),
    ("stealth://implementations/{implementation_id}", "implementation", "Implementation",
     "One durable implementation's secret-free execution descriptor: kind, provider, "
     "protocol, locator, I/O schema, requirements, lifecycle state.",
     implementation_resource),
    ("stealth://tasks/{task_node_id}/implementations", "task-implementations",
     "Task node implementations",
     "Every active implementation linked to a TaskNode (candidate/deprecated/"
     "disabled/quarantined ones are excluded).", task_implementations_resource),
    ("stealth://runs/{run_id}", "run", "Execution run",
     "A durable execution run: overall status, per-node status / attempt counts / "
     "error class, pinned implementation bindings, and the full per-node attempt "
     "history. Read-only.", run_resource),
]


def register_resources(server) -> None:
    """Bind the canonical read-only Resource surface to `server`.

    Called once from server.py after the @server.tool() definitions.
    """
    for uri, name, title, description, fn in _RESOURCES:
        server.resource(uri, name=name, title=title, description=description,
                        mime_type="text/markdown")(fn)


# --------------------------------------------------------------------------
# Phase 5 seam: node-scoped resource resolution
# --------------------------------------------------------------------------
async def resolve_node_resources(
    pool,
    node: dict,
    environment: Optional[dict] = None,
    *,
    scope,
) -> dict[str, list[dict[str, str]]]:
    """Return ONLY the Procedures / Claims / Implementations relevant to one
    TaskNode + environment, as resource references -- instead of dumping
    global context.

    This is the documented seam for a future
    ``resolve_node_resources(node, environment)`` in the execution path. It
    is NOT wired into execution yet: it is a thin aggregator over three
    already-real reads so a future caller has one entrypoint. Each leg is
    best-effort -- a failing leg yields ``[]``, never an exception.

      * relevant procedures      -> applicability.find_applicable_procedures
                                    (goal_text = the node's goal, scoped)
      * relevant implementations -> implementation_registry.get_for_task
      * relevant claims          -> the precondition-referenced claims on the
                                    top procedure candidate

    Returns {"procedures": [...], "implementations": [...], "claims": [...]}
    where each item is {"uri": "stealth://...", "label": "..."}.
    """
    out: dict[str, list[dict[str, str]]] = {
        "procedures": [], "implementations": [], "claims": [],
    }
    goal_text = (node or {}).get("goal") or (node or {}).get("description") or ""

    try:
        from app.services.applicability import find_applicable_procedures

        procs = await find_applicable_procedures(
            pool, goal_text=goal_text, require_verified=False, limit=5,
            access_scope=scope,
        )
        for p in procs or []:
            out["procedures"].append({
                "uri": f"stealth://procedures/{p['procedure_id']}",
                "label": p.get("display_name") or p.get("name") or str(p["procedure_id"]),
            })
    except Exception:  # noqa: BLE001 -- best-effort seam
        pass

    node_id = (node or {}).get("id") or (node or {}).get("task_node_id")
    if node_id:
        try:
            impls = await _impl.get_for_task(pool, str(node_id), scope=scope)
            for i in impls or []:
                out["implementations"].append({
                    "uri": f"stealth://implementations/{i['id']}",
                    "label": f"{i.get('kind')}/{i.get('provider')} v{i.get('version')}",
                })
        except Exception:  # noqa: BLE001
            pass

    if out["procedures"]:
        try:
            from app.mcp_server.server import _resolve_live_procedure

            top = out["procedures"][0]["uri"].rsplit("/", 1)[-1]
            live = await _resolve_live_procedure(pool, top)
            rows = await _pg.get_procedure_claims(pool, str(live["id"]), scope=scope)
            out["claims"] = [
                {"uri": f"stealth://claims/{c.get('id')}",
                 "label": c.get("statement") or str(c.get("id"))}
                for c in (rows or []) if c.get("id")
            ]
        except Exception:  # noqa: BLE001
            pass

    return out
