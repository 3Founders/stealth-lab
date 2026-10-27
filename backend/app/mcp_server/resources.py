"""
MCP Resources for kell -- canonical, read-only, id-addressable
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
            txt = s.get("goal") or s.get("do") or s.get("description") or s.get("action") or s.get("name") or ""
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
    if detail.get("executor_kinds"):
        md += _section("Executor kinds named by step bindings", ", ".join(detail["executor_kinds"]))
    prov = "".join(filter(None, [
        _kv("source", detail.get("provenance")),
        _kv("created_by", detail.get("created_by")),
        _kv("visibility", detail.get("visibility")),
    ]))
    md += _section("Provenance", prov)
    return md.rstrip() + "\n"


async def goal_product_resource(goal_id: str, ctx: Context) -> str:
    """stealth://goals/{goal_id} -- goal + benchmarks + solutions + leaderboard."""
    scope = _scope()
    pool = _pool(ctx)
    g = await _pm.get_goal_for_product(pool, goal_id, scope=scope)
    if g is None:
        return _NOT_FOUND
    benchmarks = await _pm.list_goal_benchmarks(pool, goal_id, scope=scope)
    solutions = await _pm.list_goal_solutions(pool, goal_id, scope=scope)
    lb = await _pm.goal_leaderboard(pool, goal_id, scope=scope)

    md = f"# Goal: {g.get('canonical_name') or goal_id}\n\n"
    md += _kv("ID", g.get("id"))
    md += _kv("Status", g.get("status"))
    md += _kv("Objective", g.get("objective"))
    md += _section("Description", g.get("description") or "")
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


async def goal_solutions_resource(goal_id: str, ctx: Context) -> str:
    """stealth://goals/{goal_id}/solutions -- association rows only."""
    scope = _scope()
    pool = _pool(ctx)
    if await _pm.get_goal_for_product(pool, goal_id, scope=scope) is None:
        return _NOT_FOUND
    rows = await _pm.list_goal_solutions(pool, goal_id, scope=scope)
    md = f"# Solutions for goal `{goal_id}`\n\n"
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
    md += _kv("Goal", e.get("goal_id"))
    md += _kv("Benchmark", e.get("benchmark_id"))
    md += _kv("Solution", e.get("solution_id"))
    md += _kv("Procedure version", e.get("procedure_version_id") or e.get("procedure_id"))
    if e.get("metrics"):
        md += _section("Metrics", _fence(e.get("metrics")))
    if e.get("verification"):
        md += _section("Verification summary", _fence(e.get("verification")))
    md += _section("Linked executions", _bullets(
        [{"description": str(x)} for x in (e.get("executions") or [])]
    ) or "_none_")
    return md.rstrip() + "\n"


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


# --------------------------------------------------------------------------
# MCP v1 related-claims resources (final_thing.md). Output is claims.md's own
# pipe grammar, grouped into contiguous blocks (discovery / precondition /
# related) so a small executor agent gets everything for one step in a single
# read or one `rg`. Always scoped to the caller -- never unrestricted.
# --------------------------------------------------------------------------
def _claim_statement(row: dict) -> str:
    props = row.get("properties") or {}
    if isinstance(props, str):
        try:
            props = json.loads(props)
        except ValueError:
            props = {}
    return props.get("statement") or row.get("statement") or row.get("name") or ""


def _claim_block(topic: str, rows: list[dict]) -> str:
    from app.stealth.pipe_format import _kv_field, _row, _SEP

    lines = []
    for r in rows:
        cid = str(r.get("claim_id") or r.get("id"))
        scope = r.get("scope")
        scope_s = (scope.get("scope_type") if isinstance(scope, dict) else None) or r.get("scope_type") or "global"
        lines.append(
            _row("CLAIM", cid, r.get("status") or r.get("claim_status") or "current", topic, scope_s,
                 _claim_statement(r))
            + _SEP + _kv_field("version", r.get("version") or 1)
        )
    return "\n".join(lines)


async def _discovery_claims(pool, procedure_id: str, scope) -> list[dict]:
    from app.services.access import visibility_predicate
    from app.services.shards import fanout_fetch

    vis_sql, vis_params = visibility_predicate(scope, param_index=2)
    rows = await fanout_fetch(
        pool,
        f"SELECT id, properties, visibility, scope_type, claim_status, t_created FROM knowledge_nodes "
        f"WHERE node_type = 'claim' AND t_invalid IS NULL "
        f"AND properties->>'source' = 'report_discovery' AND properties->>'procedure_id' = $1 "
        f"AND {vis_sql} ORDER BY t_created",
        procedure_id, *vis_params,
    )
    return [dict(r) for r in rows]


async def procedure_claims_resource(procedure_id: str, ctx: Context) -> str:
    """stealth://procedures/{procedure_id}/claims -- every claim an agent needs
    for this Procedure, in three contiguous blocks: discoveries recorded
    against it (report_discovery), claims its preconditions cite, and claims
    related to its goal."""
    from app.mcp_server.server import _resolve_live_procedure
    from app.services.applicability import ProcedureNotFound
    from app.services.relevant_claims import get_relevant_claims

    scope = _scope()
    pool = _pool(ctx)
    try:
        proc = await _resolve_live_procedure(pool, procedure_id, access_scope=scope)
    except ProcedureNotFound:
        return _NOT_FOUND
    stable_id = str(proc["procedure_id"])
    discoveries = await _discovery_claims(pool, stable_id, scope)
    preconditions = await _pg.get_procedure_claims(pool, str(proc["id"]), scope=scope)
    related = await get_relevant_claims(pool, goal=proc.get("goal") or proc.get("name") or "",
                                        access_scope=scope, top_k=10)
    seen = {str(r.get("id")) for r in discoveries + preconditions}
    related = [r for r in related if str(r.get("claim_id")) not in seen]
    blocks = [f"# claims for procedure {stable_id} ({proc.get('name')})"]
    for topic, rows in (("discovery", discoveries), ("precondition", preconditions), ("related", related)):
        if rows:
            blocks.append(_claim_block(topic, rows))
    return "\n".join(blocks) + "\n"


async def _tenant_scope_for(pool, scope):
    """The tenant the caller's evidence is read under: their organisation, else the shared commons. Any failure -> the commons
    (a narrower view, never a wider one)."""
    from app.services.access import TenantScope

    if not getattr(scope, "viewer_id", None):
        return TenantScope.commons()
    try:
        from app.services.authn import Actor, tenant_scope_for_actor

        return (await tenant_scope_for_actor(pool, Actor(subject=scope.viewer_id)))[1]
    except Exception:  # noqa: BLE001 -- ambiguous membership, no user row: read as the commons
        return TenantScope.commons()


async def goal_claims_resource(goal_id: str, ctx: Context) -> str:
    """stealth://goals/{goal_id}/claims -- one Goal's contract and status, then claims related to it.

    First line after the header: GOAL_STATUS, the Goal's status COMPUTED from the evidence on its guarantee claims
    (verified | open | failed | contested | expired; read only, never stored, `goals.status` is something else). Then the
    claims linked to the Goal (topic = their role: guarantee or assumption), then claims found by searching on its name."""
    from app.services import goal_claims as _gc
    from app.services import goals as _goals
    from app.services.access import visibility_predicate
    from app.services.relevant_claims import get_relevant_claims
    from app.stealth.pipe_format import _SEP, _kv_field, _row

    scope = _scope()
    pool = _pool(ctx)
    goal = await _goals.get_goal(pool, goal_id, scope=scope)
    if goal is None:
        return _NOT_FOUND
    out = [f"# claims for goal {goal_id} ({goal.get('canonical_name')})"]
    status = await _gc.goal_status(pool, goal_id, scope=scope, tenant_scope=await _tenant_scope_for(pool, scope))
    linked_ids = [c["claim_id"] for c in (status or {}).get("claims", [])]
    if status:
        k = status["counts"]
        checks = ",".join(f"{r}:{n}" for r, n in sorted(status["client_checks"].items())) or "-"
        out.append(_row("GOAL_STATUS", goal_id, status["status"]) + _SEP + _SEP.join([
            _kv_field("guarantees", k["guarantees"]), _kv_field("verified", k["verified_claims"]), _kv_field("failed", k["failed_claims"]),
            _kv_field("contested", k["contested_claims"]), _kv_field("open", k["open_claims"]),
            _kv_field("assumptions", k["assumptions"]), _kv_field("closed", k["closed_links"]),
            _kv_field("client_checks", checks), _kv_field("why", status["why"])]))
    if linked_ids:
        vis_sql, vis_params = visibility_predicate(scope, param_index=2)
        rows = await pool.fetch(
            f"SELECT id, properties, name, visibility, scope_type, claim_status, t_created FROM knowledge_nodes "
            f"WHERE node_type = 'claim' AND id = ANY($1::uuid[]) AND {vis_sql}", linked_ids, *vis_params)
        by_id = {str(r["id"]): dict(r) for r in rows}
        for role in ("guarantee", "assumption"):
            block_rows = [by_id[c["claim_id"]] for c in status["claims"] if c["role"] == role and c["claim_id"] in by_id]
            if block_rows:
                out.append(_claim_block(role, block_rows))
    related = await get_relevant_claims(pool, goal=goal.get("canonical_name") or "", access_scope=scope, top_k=10)
    seen = set(linked_ids)
    body = _claim_block("related", [r for r in related if str(r.get("claim_id") or r.get("id")) not in seen])
    if body:
        out.append(body)
    return "\n".join(out) + "\n"


async def survey_repo_prompt_resource() -> str:
    """stealth://prompts/survey_repo -- the survey_repo prompt's text, for clients that don't show MCP prompts."""
    from app.mcp_server.prompts import survey_repo

    return survey_repo()


async def plan_and_run_prompt_resource() -> str:
    """stealth://prompts/plan_and_run -- the plan_and_run prompt's text with a `<your task>` placeholder."""
    from app.mcp_server.prompts import plan_and_run

    return plan_and_run("<your task>")


# The long form of find_ways' description (server.py lists a short one: every listed word is paid on every turn).
FIND_WAYS_GUIDE = (
    '# find_ways: the full guide\n'
    '\n'
    'Find the known ways to do something. Returns KNOWLEDGE, not a plan: you\n'
    '(the planner agent) compile the plan and write `.kell/` yourself --\n'
    'the `plan_and_run` prompt has the format.\n'
    '\n'
    'The unit is a TASK, not a message: a goal plus the context needed to do\n'
    'it. Call it at the start of every new task, before writing code (a bug\n'
    'fix, a feature, a data transformation, a config change), and again when\n'
    "the task changes. One user message can hold several tasks (one call each);\n"
    "a follow-up on the same task needs no new call. Don't call it for a\n"
    "one-line edit, a question about this repo's own code, or the same task\n"
    'twice -- repeats are served from cache and then refused.\n'
    '\n'
    'query: the task as a self-contained statement -- the goal first, then the\n'
    'context that decides how it is done (language and framework, the files or\n'
    'components involved, constraints, how success is checked). For example:\n'
    '"add a DOCX export to the report page of a Django 4 app; keep the existing\n'
    'table styles; check: the report tests pass". Not a paste of the user\'s\n'
    'message or logs.\n'
    "repo_claims: the text of this repo's `.kell/claims.md`\n"
    '  (`CLAIM|R-001|current|stack|repository|Node 20.11|source=.nvmrc:1#sha=9f2c|version=1`\n'
    '  lines; up to 200 facts / 64 KB; write it with the `survey_repo` prompt).\n'
    '  Used for this request only -- never stored or logged.\n'
    '\n'
    'What happens:\n'
    '  0. Triage. One quick judgment decides whether this is a reusable task\n'
    '     worth a lookup. A question about this repo, a trivial edit, a\n'
    '     general question or conversation gets `outcome: "not_needed"` at\n'
    '     once (with `next`); when unsure, the lookup runs.\n'
    '  1. Goal search. Hybrid (lexical + vector) candidates, each judged by\n'
    '     the JEV/NLI judge against your query AND your repo facts; accepted\n'
    '     Goal-hierarchy neighbours of a match are judged the same way and\n'
    '     listed under `goal_judgment.related_goals`. Three honest outcomes,\n'
    '     never a guess: "resolved" (one Goal clearly best), "ambiguous" (2+\n'
    '     too close to call, or only partial matches -- you pick, or\n'
    '     rephrase), "no_match" (nothing known). If no judge answers, a\n'
    '     lexical re-ranker is used instead and `goal_judgment.mode` says\n'
    '     "lexical_fallback".\n'
    '  2. Procedure choice (`resolve_goal`): feasible Procedures for the Goal,\n'
    '     and recursively for its sub-Goals. With repo facts, the 5-20 most\n'
    '     related facts per Procedure go to the NLI/JEV judge together with\n'
    "     the Procedure's preconditions and its steps' runtime needs. A\n"
    '     contradicted REQUIRED precondition drops the Procedure; a\n'
    '     contradicted runtime need only moves it to the back.\n'
    '  3. The result, per Goal: the chosen Procedure with every step in full\n'
    '     (what to do, source locator, binding, needs, success check), the\n'
    '     alternatives, and `repo_fit` (which of your fact ids supported or\n'
    '     blocked it). Steps are `action` (has a script/tool), `subgoal`\n'
    '     (see the entry with that goal_id), or `instruction` (do it from the\n'
    '     text). No node ids, no order, no file writes, no execution -- those\n'
    "     are the planner's job.\n"
    '  4. On any outcome, `related_examples` (when enabled): verified solutions\n'
    '     of SIMILAR past tasks -- worked examples, NOT verified to apply here:\n'
    '     adapt them to your request, never copy them as-is. On "ambiguous",\n'
    '     `suggested` names the one candidate to start from.\n'
    '\n'
    'Read-only; needs no token. Report what you learn with `report_discovery`.\n'
    '\n'
    'Optional model plan: pass `candidates` (the "model|scaffold" units you can run; a deployment\n'
    'may also supply them server-side) and a `model_plan` block comes back with the cheapest\n'
    'ladder likely to pass (try A; if its check fails, B). `check_kind` is how you will check\n'
    "each attempt (tests by default); `model_constraints` is recommend_models' `constraints`.\n"
    'Run the first model, check it, then call `report_result(instance_key, accepted)` -- its\n'
    'reply names the next model. Without candidates the reply is unchanged.\n'
    '\n'
    "This repository's own knowledge (all optional; with none of them the reply is exactly as before):\n"
    'repo_identity: {repo_id, public_name?, strength?} from `.kell/meta.json` (repo_id is a hash;\n'
    "  send public_name -- owner/name -- only if the user allows). A strong identity lets this repo's own\n"
    '  global Goals be judged too; strength "weak" (or a p: id) is never used to match other repos.\n'
    'library_rows: the text of `.kell/index/library.idx` (up to 64 KB): problems already solved in\n'
    '  THIS repo. Matching entries come back as `library_matches` (read them, and their diffs under\n'
    '  `.kell/library/solutions/`, before anything global); a Goal they name wins a tie.\n'
    "route_obs: the ROUTE and OBS lines of `.kell/routing.md` (up to 16 KB): this machine's attempt\n"
    '  counts per model. With a model plan they condition it on what worked HERE, and `routing_rows`\n'
    '  comes back: the ROUTE line(s) to write into routing.md.\n'
    'Like repo_claims, these are used for this request only -- never stored or logged.\n'
    '\n'
    'my_model: the model YOU are (e.g. "claude-sonnet-4-5"). It is always a candidate (scaffold = your\n'
    'MCP client), so the plan says whether to keep the task yourself or hand it to a cheaper / stronger\n'
    'model. Each rung carries p_ok (mean and 90% interval) and expected cost; `basis` says whether that\n'
    'rests on observed runs ("posterior") or on public benchmarks and model cards only ("prior").\n'
    '\n'
    'detail: "full" (default) returns every step in full. "summary" shortens step text, checks and\n'
    'example bodies to a line each (the Goal, Procedure, why chosen, repo fit, alternatives and\n'
    'preconditions stay complete): much less to carry through a long session. Call again with the same\n'
    'query and detail="full" for the whole thing; a hosted server answers that from cache.\n'
)


async def find_ways_guide_resource() -> str:
    """stealth://guides/find_ways -- every find_ways argument, step and reply field."""
    return FIND_WAYS_GUIDE


async def library_format_resource() -> str:
    """stealth://formats/library -- the grammar of .kell/library.md and routing.md and how to keep them."""
    from app.stealth.library import IDX_HEADER, LIBRARY_HEADER, ROUTING_HEADER

    return (
        "# .kell library: this repository's own solved problems and model routes\n\n"
        "Kept on the user's machine by `kell library <command>` (npm package kell-mcp); the\n"
        "server only ever sees what find_ways' `library_rows` / `route_obs` / `repo_identity` carry, for one request.\n\n"
        "## library.md (committed; `.kell/.gitattributes`: library.md merge=union)\n```\n" + LIBRARY_HEADER + "```\n"
        "Free text is escaped: `|` -> %7C, newline -> %0A, a literal %XX -> %25XX.\n\n"
        "## index/library.idx (generated; what find_ways' library_rows sends)\n```\n" + IDX_HEADER + "```\n\n"
        "## routing.md (generated ROUTE lines from find_ways' routing_rows; OBS counts from this machine)\n```\n"
        + ROUTING_HEADER + "```\n\n"
        "## Commands\n"
        "- `kell library index` -- canonical library.md, library.idx, terms.idx, SUMMARY.md\n"
        "- `kell library add --title <goal> --check <cmd> [--g <goal id>] ...` -- write back a solved "
        "problem (re-runs the check; refuses if it fails; keeps the diff in library/solutions/)\n"
        "- `kell library check` / `refresh <id>` -- staleness from git hash-object of touched files\n"
        "- `kell library route --from-reply <reply.json>` / `obs <R-id> --model m --scaffold s --ok|--fail`\n"
        "- `kell library payload` -- exactly what the knowledge hook sends to find_ways\n"
    )


_V1_RESOURCES = [
    ("stealth://claims/{claim_id}", "claim", "Claim",
     "One structured claim: subject/predicate/object, truth state, epistemic "
     "status, and its live evidence.", claim_resource),
    ("stealth://procedures/{procedure_id}/claims", "procedure-claims", "Claims for a procedure",
     "Every claim needed for one Procedure, as grep-friendly CLAIM lines in "
     "contiguous blocks: discoveries recorded against it, claims its "
     "preconditions cite, and claims related to its goal.", procedure_claims_resource),
    ("stealth://goals/{goal_id}/claims", "goal-claims", "Claims for a goal",
     "One Goal's computed status (GOAL_STATUS), the claims linked to it as its contract (guarantee, assumption) and claims related to it, as grep-friendly lines.", goal_claims_resource),
    # The two v1 prompts as plain resources: the same text, for MCP clients that can read
    # resources but don't surface MCP prompts to the model (a model can't call prompts/get).
    ("stealth://prompts/survey_repo", "prompt-survey-repo", "survey_repo prompt",
     "The survey_repo prompt: how to write .kell/claims.md (what facts to capture and how "
     "to write them). Same text as the MCP prompt.", survey_repo_prompt_resource),
    ("stealth://prompts/plan_and_run", "prompt-plan-and-run", "plan_and_run prompt",
     "The plan_and_run prompt with a <your task> placeholder: find_ways, compile "
     ".kell/run.md, do and check each step, keep claims.md true, report what was "
     "learned. Same text as the MCP prompt.", plan_and_run_prompt_resource),
    ("stealth://formats/library", "format-library", ".kell library format",
     "The grammar of .kell/library.md (this repo's solved problems), index/library.idx and routing.md, "
     "and the kell library commands that keep them.", library_format_resource),
    ("stealth://guides/find_ways", "guide-find-ways", "find_ways guide",
     "Every find_ways argument, what each step does, and the reply fields (the listed description is short).",
     find_ways_guide_resource),
]

def register_resources(server) -> None:
    """Bind the read-only Resource surface to `server`.

    Called once from server.py after the @server.tool() definitions.
    """
    for uri, name, title, description, fn in _V1_RESOURCES:
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
    """Return ONLY the Procedures / Claims relevant to one
    TaskNode + environment, as resource references -- instead of dumping
    global context.

    This is the documented seam for a future
    ``resolve_node_resources(node, environment)`` in the execution path. It
    is NOT wired into execution yet: it is a thin aggregator over two
    already-real reads so a future caller has one entrypoint. Each leg is
    best-effort -- a failing leg yields ``[]``, never an exception.

      * relevant procedures      -> applicability.find_applicable_procedures
                                    (goal_text = the node's goal, scoped)
      * relevant claims          -> the precondition-referenced claims on the
                                    top procedure candidate

    Returns {"procedures": [...], "claims": [...]}
    where each item is {"uri": "stealth://...", "label": "..."}.
    """
    out: dict[str, list[dict[str, str]]] = {
        "procedures": [], "claims": [],
    }
    goal_text = (node or {}).get("goal") or (node or {}).get("description") or ""

    try:
        from app.services import retrieval_service as _rs

        found = await _rs.search_procedures(pool, goal_text, scope=scope, require_verified=False)
        procs = [i["_row"] for i in found.procedures.ranked[:5]]
        for p in procs or []:
            out["procedures"].append({
                "uri": f"stealth://procedures/{p['procedure_id']}",
                "label": p.get("display_name") or p.get("name") or str(p["procedure_id"]),
            })
    except Exception:  # noqa: BLE001 -- best-effort seam
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
