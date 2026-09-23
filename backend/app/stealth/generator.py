"""
`generate_projection` -- regenerate the whole `.stealth/` working set for
one ProcedureRun from canonical Postgres state.

P1 scope: the run-scoped working set (the same canonical reads B35's
original `generate_projection` used -- `get_run_context`,
`fetch_procedure_version`, `evaluate_run_completion`). It now emits, in
addition to the compact `context.md` / `run.json` / `meta.json`:

    claims.md + index/claims.idx           -- the precondition-derived local Claim working set
    procedures.md + index/procedures.idx   -- the selected Procedure, in full
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
import logging
import os
from datetime import datetime, timezone
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

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

__all__ = [
    "generate_projection", "StealthProjectionError", "STEALTH_DIRNAME", "CONTEXT_MD_MAX_BYTES",
    "CONTENT_PAGE_FILES", "OPTIONAL_CONTENT_PAGE_FILES",
]

# The real, addressable `.stealth/*.md` "content pages" this generator
# writes -- as opposed to the router/compact files (context.md, run.json,
# meta.json, index.md) or index/*.idx line-range indexes. The single
# source of truth for "which .stealth/*.md files are real projection
# pages a caller might legitimately hand-edit" -- `app.stealth.edit_ledger`
# imports this rather than hardcoding a second list (see that module's
# own docstring). `ledger.md` itself is deliberately excluded: it is
# ITSELF generated from the edit ledger, not a page a caller edits.
CONTENT_PAGE_FILES: tuple[str, ...] = ("claims.md", "procedures.md", "goals.md", "run.md")
# Written only when the corresponding working set is non-empty (see
# `has_expl` below) -- still a real, editable content page when present.
OPTIONAL_CONTENT_PAGE_FILES: tuple[str, ...] = ("exploration.md",)


def _short(value: object, n: int = 12) -> str:
    s = str(value or "")
    return s[:n]


_EMPTY_NOTE = {
    "claims.md": "(no structured preconditions and no page-faulted global claims in scope)",
    "procedures.md": "(no selected procedure)",
    "run.md": "(no nodes)",
}
_IDX_HEADER = {
    "claims.md": "claims.idx  id|version|scope|status|tags|file|start|end|summary",
    "procedures.md": "procedures.idx  id|version|scope|status|tags|file|start|end|summary",
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


async def _build_claims_page(
    pool: asyncpg.Pool, global_claims: tuple[MdBlock, ...] = (),
) -> tuple[str, list[IdxRow]]:
    """Meta-harness Sec 25: `CLAIM|<id>|<status>|<topic>|<scope>|<statement>
    |source=<source_ref>|version=<version>`, one line per real Claim
    (`knowledge_nodes` node_type='claim').

    Driven by `global_claims` -- the SAME page-faulted working set
    `faults.py::resolve_blocks_by_kind` already resolves (real Claim rows
    a caller has actually pulled into scope, via a precondition check or
    an explicit `open_exploration` fault) -- never the whole corpus (B30's
    bounded-working-set rule). Local precondition predicates (the OLD
    `pc-<hash>` synthetic rows this function used to also emit) are NOT
    real Claims -- they belong to run.md's own CONTEXT/ACCESS lines for
    the node that checks them, not here; dropping them from claims.md is
    a correction, not a regression (nothing downstream keyed off a
    `pc-<hash>` id -- confirmed via grep before this change).

    Queries `knowledge_nodes` directly for `source_id`/`claim_type`
    (present on every real claim row, confirmed live) rather than
    round-tripping through the faulted MdBlock's own rendered body text
    -- `topic` is `claim_type` (fact/invariant/assumption/constraint/
    causal/failure_mode/environment_fact/decision), a real stored
    category, not an invented one; no claim in this corpus has a
    separate free-text "topic" field to draw from honestly.
    """
    from app.stealth.pipe_format import ClaimLine, render_claims_md

    if not global_claims:
        return render_claims_md([]), []

    ids = [b.obj_id for b in global_claims]
    rows = await pool.fetch(
        "SELECT id, properties, scope_type, t_invalid FROM knowledge_nodes "
        "WHERE id = ANY($1::uuid[]) AND node_type = 'claim'", ids,
    )
    claim_lines: list[ClaimLine] = []
    for row in rows:
        props = row["properties"]
        if isinstance(props, str):
            props = json.loads(props)
        props = props or {}
        status = str(props.get("claim_status") or props.get("status") or "UNKNOWN")
        if row["t_invalid"] is not None:
            status = "SUPERSEDED"
        claim_lines.append(ClaimLine(
            claim_id=str(row["id"]),
            status=status,
            topic=str(props.get("claim_type") or "-"),
            scope=str(row["scope_type"] or "global"),
            statement=str(props.get("statement") or ""),
            source=str(props.get("source_id") or "unknown"),
            version=1,  # claims are id-addressed, not versioned (confirmed live -- no version column)
        ))
    claims_md = render_claims_md(claim_lines)

    idx_rows: list[IdxRow] = []
    for i, line in enumerate(claims_md.splitlines(), start=1):
        if not line.startswith("CLAIM|"):
            continue
        cl = next(c for c in claim_lines if line.startswith(f"CLAIM|{c.claim_id}|"))
        idx_rows.append(IdxRow(
            obj_id=cl.claim_id, version=str(cl.version), scope=cl.scope, status=cl.status,
            tags=(cl.topic,), file="claims.md", start=i, end=i, summary=cl.statement[:110],
        ))
    return claims_md, idx_rows


def _procedure_line_from_row(procedure: dict, *, procedure_id: str, version: int, scope_type: str | None) -> "ProcedureLine":  # noqa: F821
    from app.stealth.pipe_format import ProcedureLine, StepLine, VerifyReqLine

    steps_raw = procedure.get("steps") or []
    steps: list[StepLine] = []
    verify_reqs: list[VerifyReqLine] = []
    prev_step_id: str | None = None
    for s in steps_raw:
        order = s.get("order")
        if order is None:
            continue
        step_id = f"S{order}"
        deps = s.get("deps")
        if not deps:
            deps = [prev_step_id] if prev_step_id else []
        goal_type = s.get("goal") or s.get("action") or "-"
        steps.append(StepLine(
            step_id=step_id, order=order, goal_type=goal_type,
            description=s.get("action") or s.get("goal") or "-", deps=list(deps),
        ))
        prev_step_id = step_id

        raw_verify = s.get("verification")
        if isinstance(raw_verify, str) and raw_verify.strip():
            verify_reqs.append(VerifyReqLine(step_id=step_id, verification_goal=goal_type, description=raw_verify))
        elif isinstance(raw_verify, dict) and raw_verify.get("statement"):
            verify_reqs.append(VerifyReqLine(
                step_id=step_id, verification_goal=goal_type, description=raw_verify["statement"],
            ))

    return ProcedureLine(
        procedure_id=procedure_id, status=str(procedure.get("verification_state") or "-"),
        topic=str(procedure.get("domain") or "-"), scope=str(procedure.get("scope_type") or scope_type or "-"),
        name=procedure.get("name") or procedure.get("goal") or procedure_id, version=version,
        steps=steps, verify_reqs=verify_reqs,
    )


async def _build_procedures_page(
    pool: asyncpg.Pool, context: dict[str, Any], procedure: dict,
    extra_ids: tuple[str, ...] = (),
) -> tuple[str, list[IdxRow]]:
    """Meta-harness Sec 26: `PROCEDURE|<id>|<status>|<topic>|<scope>|<name>
    |version=<version>`, `STEP|<procedure_id>|<step_id>|<order>|<goal_type>
    |<description>|deps=<step_ids_csv>`, `VERIFY_REQ|<procedure_id>|<step_id>
    |<verification_goal>|<description>`.

    Run-independent by construction -- unlike the old MdBlock version,
    this never reads `context["nodes"]` (that is run-specific execution
    state, belongs to run.md, not to the abstract Procedure/Step
    definition every run using this procedure shares). `status` is the
    real `verification_state` column (candidate/verified/retired) --
    honest, not an invented "ACTIVE" label this schema doesn't have.
    `topic` is the real `domain` column when set, else `-` (never
    fabricated). Steps are linear by construction (db/18_procedures.sql's
    own DDL comment -- no branching field exists), so `deps` is the
    immediately preceding step's id unless a step explicitly declares its
    own (future-proofing for a real dependency field, unused today).

    VERIFY_REQ lines come from each step's own `verification` field --
    the SAME field `verification.py::derive_criteria`'s node-scoped path
    reads (Sec 14) -- never from `postconditions` (those are procedure-
    wide, not tied to one step, and stay in run.md's run-level VERIFY
    lines instead, per that module's own scoping rule).
    """
    from app.stealth.pipe_format import render_procedures_md

    pid = str(context["procedure_id"])
    ver = context["procedure_version"]
    scope_type = context.get("scope_type")

    entries: list[tuple[str, int, dict]] = [(pid, ver, procedure)]
    if extra_ids:
        from app.services.shards import fanout_fetch
        rows = await fanout_fetch(
            pool,
            "SELECT * FROM procedures WHERE procedure_id = ANY($1::uuid[]) AND t_invalid IS NULL",
            [i for i in extra_ids if i != pid],
        )
        entries += [(str(r["procedure_id"]), r["version"], dict(r)) for r in rows]

    lines_objs = [_procedure_line_from_row(proc, procedure_id=oid, version=v, scope_type=scope_type) for oid, v, proc in entries]
    procedures_md = render_procedures_md(lines_objs)

    idx_rows: list[IdxRow] = []
    lines = procedures_md.splitlines()
    starts = [i for i, ln in enumerate(lines, start=1) if ln.startswith("PROCEDURE|")]
    # `starts` and `lines_objs` are in the SAME order -- one PROCEDURE
    # block per entry, rendered in the order given -- zipped by position,
    # not re-matched by reconstructing the (cleaned) line text, which
    # could drift from the real rendered line if a name needed cleaning.
    for pos, (start, pl) in enumerate(zip(starts, lines_objs)):
        end = (starts[pos + 1] - 2) if pos + 1 < len(starts) else len(lines)
        idx_rows.append(IdxRow(
            obj_id=pl.procedure_id, version=str(pl.version), scope=pl.scope, status=pl.status,
            tags=(pl.topic,), file="procedures.md", start=start, end=end, summary=pl.name[:110],
        ))
    return procedures_md, idx_rows


# READY/RUNNING/BLOCKED/DONE per meta-harness Sec 24's own RUN_STATE
# vocabulary -- mapped from the real execution_run_nodes.status values
# (durable_run.py's own lifecycle), not invented. A status this map
# doesn't recognize is an honest bug signal, not silently dropped --
# _build_index_md's caller sees a KeyError in that case rather than a
# node quietly vanishing from every bucket.
_RUN_STATE_BUCKET = {
    "pending": "READY", "resumable": "READY",
    "running": "RUNNING",
    "blocked": "BLOCKED",
    "succeeded": "DONE", "failed": "DONE", "cancelled": "DONE",
}


async def _gather_index_groups(
    pool: asyncpg.Pool, *, context: dict[str, Any],
    claims_rows: list[IdxRow], procedures_rows: list[IdxRow],
):
    """The real data-gathering half of `index.md` (meta-harness/execu.md
    Sec 24), kept separate from the final render call so the caller can
    supply `revision` (the journal's own monotonic seq, only known once
    `append_events` has actually run under the write lock -- see
    `generate_projection`'s own call site) without this function needing
    to know anything about journaling.

    CLAIM_GROUP/PROCEDURE_GROUP reuse the SAME topic tags already
    computed for claims.idx/procedures.idx (`.tags[0]`, claim_type/domain
    respectively) -- one source of grouping truth, not two.
    GOAL_GROUP groups by the real
    `goals.tags` column (migration 83, `ingestion` lane) for exactly the
    Goals `_resolve_goals_by_normalized_name` resolves for this run's own
    nodes -- the SAME resolution `_build_run_page`'s NODE/GOAL lines use,
    not a second lookup with its own drift risk. Never the whole `goals`
    corpus (B30's bounded-working-set rule, same as claims.md's own
    scoping) -- 3272 real rows exist as of this writing; only ones this
    run actually touches are ever grouped here.
    """
    from app.stealth.pipe_format import GroupLine, RunStateLine

    def _group(rows: list[IdxRow]) -> list[GroupLine]:
        by_topic: dict[str, list[str]] = {}
        for r in rows:
            topic = r.tags[0] if r.tags else "-"
            by_topic.setdefault(topic, []).append(r.obj_id)
        return [GroupLine(topic=t, ids=ids) for t, ids in sorted(by_topic.items())]

    claim_groups = _group(claims_rows)
    procedure_groups = _group(procedures_rows)

    node_goal_texts = [n.get("goal") for n in context.get("nodes", []) if n.get("goal")]
    goal_rows = await _resolve_goals_by_normalized_name(
        pool, node_goal_texts, scope_type=context.get("scope_type"), scope_entity_id=context.get("scope_entity_id"),
    )
    by_goal_topic: dict[str, list[str]] = {}
    for r in goal_rows.values():
        tags = r.get("tags") or []
        topic = tags[0] if tags else "-"
        by_goal_topic.setdefault(topic, []).append(str(r["id"]))
    goal_groups = [GroupLine(topic=t, ids=sorted(set(ids))) for t, ids in sorted(by_goal_topic.items())]

    by_state: dict[str, list[str]] = {"READY": [], "RUNNING": [], "BLOCKED": [], "DONE": []}
    for n in context.get("nodes", []):
        bucket = _RUN_STATE_BUCKET[n["status"]]
        by_state[bucket].append(f"N{n['node_order']}")
    run_states = [RunStateLine(state=s, node_ids=by_state[s]) for s in ("READY", "RUNNING", "BLOCKED", "DONE")]

    return claim_groups, procedure_groups, goal_groups, run_states, goal_rows


async def _resolve_goals_by_normalized_name(
    pool: asyncpg.Pool, normalized_names_in: Any, *, scope_type: str | None, scope_entity_id: str | None,
) -> dict[str, dict]:
    """Shared by `_build_run_page` (NODE/GOAL lines) and
    `_gather_index_groups` (GOAL_GROUP) -- ONE real lookup, not two
    independently-drifting ones. Read-only against the real `goals`
    table (backend/db/83_goals.sql), using the SAME normalization
    function (`goals.py::normalize_goal_name`) the one real Goal writer
    (`find_or_create_goal`) uses for its own dedup key, so a match here
    is the identical key a write would have deduped against -- never a
    looser or stricter rule invented here.

    Prefers a non-global (more specific) match over a global one for the
    same normalized name, if both exist -- real specificity preference,
    not arbitrary "first row wins".
    """
    from app.services.goals import normalize_goal_name

    normalized_names = sorted({normalize_goal_name(n) for n in normalized_names_in if n} - {""})
    goal_by_normalized: dict[str, dict] = {}
    if not normalized_names:
        return goal_by_normalized

    if scope_type and scope_type != "global":
        rows = await pool.fetch(
            "SELECT id, normalized_name, canonical_name, expected_outcome, scope_type, tags, status, version, aliases, verification_requirement, scope_entity_id FROM goals "
            "WHERE normalized_name = ANY($1::text[]) AND t_invalid IS NULL "
            "AND ((scope_type = $2 AND scope_entity_id IS NOT DISTINCT FROM $3) OR scope_type = 'global')",
            normalized_names, scope_type, scope_entity_id,
        )
    else:
        rows = await pool.fetch(
            "SELECT id, normalized_name, canonical_name, expected_outcome, scope_type, tags, status, version, aliases, verification_requirement, scope_entity_id FROM goals "
            "WHERE normalized_name = ANY($1::text[]) AND t_invalid IS NULL AND scope_type = 'global'",
            normalized_names,
        )
    for r in rows:
        key = r["normalized_name"]
        existing = goal_by_normalized.get(key)
        if existing is None or (existing["scope_type"] == "global" and r["scope_type"] != "global"):
            goal_by_normalized[key] = dict(r)
    return goal_by_normalized


def _stringify_jsonb(value: Any) -> Optional[str]:
    """A `goals.expected_outcome`/`verification_requirement` JSONB value
    may already be a plain string (a real writer stored one) or a dict
    (a real writer stored structure) -- render either honestly, never
    guess a sub-field out of a dict that might not have one."""
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value)


async def _build_goals_page(goal_rows: dict[str, dict]) -> tuple[str, list[IdxRow]]:
    """execu.md Sec 23: `goals.md`, the newest `.stealth/` file (Goal
    now has a canonical table, backend/db/83_goals.sql, `ingestion`
    lane). Takes the SAME resolved rows `_build_run_page`/
    `_gather_index_groups` already fetched via
    `_resolve_goals_by_normalized_name` -- one real lookup shared three
    ways, not three independent queries that could drift.
    """
    from app.stealth.pipe_format import GoalLine, render_goals_md

    goal_lines = [
        GoalLine(
            goal_id=str(r["id"]), status=str(r.get("status") or "-"), scope=str(r.get("scope_type") or "-"),
            name=str(r.get("canonical_name") or r["id"]), version=int(r.get("version") or 1),
            expected_outcome=_stringify_jsonb(r.get("expected_outcome")),
            verification_summary=_stringify_jsonb(r.get("verification_requirement")),
            aliases=list(r.get("aliases") or []),
        )
        for r in goal_rows.values()
    ]
    # Stable order -- real ids, not dict-iteration-order-dependent.
    goal_lines.sort(key=lambda g: g.goal_id)
    goals_md = render_goals_md(goal_lines)

    idx_rows: list[IdxRow] = []
    lines = goals_md.splitlines()
    starts = [i for i, ln in enumerate(lines, start=1) if ln.startswith("GOAL|")]
    for pos, (start, gl) in enumerate(zip(starts, goal_lines)):
        end = (starts[pos + 1] - 2) if pos + 1 < len(starts) else len(lines)
        idx_rows.append(IdxRow(
            obj_id=gl.goal_id, version=str(gl.version), scope=gl.scope, status=gl.status,
            tags=(), file="goals.md", start=start, end=end, summary=gl.name[:110],
        ))
    return goals_md, idx_rows


async def _build_run_page(
    pool: asyncpg.Pool, context: dict[str, Any], procedure: dict,
    intents: dict[int, dict] | None = None,
) -> tuple[str, list[RunIdxRow]]:
    """Meta-harness/execu.md Sec 25: `NODE|<id>|<status>|<name>|
    goal=<goal_id>|step=<procedure:step>|impl=<id>|executor=<...>|
    deps=<...>`, `GOAL|<node_id>|<goal_id>|<summary>`, `INPUT|...`,
    `CONTEXT|...`, `ACCESS|...`, `OUTCOME|...`, `VERIFY|...`, `OWNER|...`.

    `intents` maps node_order -> a file-intent row from
    `execution_run_nodes` (owner_agent_id / write_globs / ... ). Empty
    when no coordination has been declared.

    Goal resolution is READ-ONLY, real, and honest: no per-step goal_id
    column exists yet (confirmed live with the `ingestion` lane, who
    built the `goals` table -- ProcedureStep is a JSONB list item inside
    `procedures.steps`, not a row, so a step-level goal_id would be a
    NEW writer-populated JSONB key, a real, separate decision this
    function does not make). Instead, each step's own raw goal text is
    normalized via `goals.py::normalize_goal_name` (the SAME function
    the one real Goal writer, `find_or_create_goal`, uses for its own
    dedup key) and looked up against real `goals.normalized_name` rows,
    scoped the same way find_or_create_goal scopes writes (global, or
    this run's own scope_type+scope_entity_id). A step whose normalized
    text matches no real Goal renders `goal=-` and no GOAL line --
    unresolved stays unresolved, never fabricated.

    `executor` is derived from the node's step binding when one is bound, else the documented "frontier"
    default (step_binding.execute_node's own fallback) -- never guessed independently of what would actually run.
    """
    from app.execution.run_collaboration import list_run_collaboration
    from app.services.goals import normalize_goal_name
    from app.stealth.pipe_format import CollabLine, NodeLine, RunLine, VerifyLine, render_run_md

    intents = intents or {}
    nodes = context.get("nodes", [])
    procedure_id = str(context["procedure_id"])
    scope_type = context.get("scope_type")
    scope_entity_id = context.get("scope_entity_id")
    steps_by_order = {s.get("order"): s for s in (procedure.get("steps") or [])}

    # Collaboration records (NOTE/BLOCKER/HANDOFF/QUESTION/ANSWER, migration
    # 90) fetched once, here, for the whole run -- shared by the "no nodes
    # yet" early-return below and the full render path, so a run's open
    # blockers/handoffs are visible even before any node exists.
    collab_rows = await list_run_collaboration(pool, str(context["procedure_run_id"]))
    collab_lines = [
        CollabLine(
            record_id=str(r["id"]), kind=r["kind"], actor=r["actor_agent_id"], body=r["body"],
            created_at=r["created_at"].isoformat() if r["created_at"] else "-",
            node_id=(f"N{r['node_order']}" if r.get("node_order") is not None else None),
            answers_id=str(r["answers_id"]) if r.get("answers_id") else None,
            target_agent_id=r.get("target_agent_id"),
        )
        for r in collab_rows
    ]

    if not nodes:
        if not collab_lines:
            return ("# run.md -- GENERATED, not canonical. Do not hand-edit.\n\n(no nodes)\n", [])
        run_line = RunLine(
            run_id=str(context["procedure_run_id"]), status=context["status"],
            objective=context.get("objective") or "-", procedure_id=procedure_id,
            procedure_version=context["procedure_version"],
        )
        return render_run_md(run_line, [], collab_lines), []

    # --- batched real lookups, never one round trip per node -----------
    normalized_by_order = {
        n["node_order"]: normalize_goal_name(n.get("goal") or "") for n in nodes if n.get("goal")
    }
    goal_by_normalized = await _resolve_goals_by_normalized_name(
        pool, normalized_by_order.values(), scope_type=scope_type, scope_entity_id=scope_entity_id,
    )

    node_ids = [str(n["id"]) for n in nodes]
    verify_by_node: dict[str, list[VerifyLine]] = {}
    if node_ids:
        vrows = await pool.fetch(
            "SELECT execution_run_node_id, criterion_id, state, method, statement, evidence_refs "
            "FROM verification_results WHERE execution_run_node_id = ANY($1::uuid[])",
            node_ids,
        )
        for r in vrows:
            nid_str = str(r["execution_run_node_id"])
            evidence = r["evidence_refs"]
            evidence_summary = ",".join(str(e) for e in evidence) if evidence else None
            verify_by_node.setdefault(nid_str, []).append(VerifyLine(
                node_id="", verification_id=r["criterion_id"], state=r["state"],
                verification_type=r["method"], criterion=r["statement"], evidence=evidence_summary,
            ))

    node_lines: list[NodeLine] = []
    for n in nodes:
        order = n["node_order"]
        nid = f"N{order}"
        deps = [f"N{d}" for d in (n.get("deps") or [])]
        it = intents.get(order) or {}
        owner = it.get("owner_agent_id")
        wglobs = it.get("write_globs") or []
        wexact = it.get("write_exact") or []
        access: list[tuple[str, str]] = [("filesystem", f"write:{g}") for g in wglobs]
        access += [("filesystem", f"write:{g}") for g in wexact]

        from app.execution.step_binding import executor_kind
        binding = n.get("binding")
        if isinstance(binding, str):
            binding = json.loads(binding)
        binding_kind = binding.get("kind") if binding else None
        executor = executor_kind(binding)

        normalized = normalized_by_order.get(order)
        goal_row = goal_by_normalized.get(normalized) if normalized else None
        goal_id = str(goal_row["id"]) if goal_row else None
        outcome = None
        if goal_row and goal_row.get("expected_outcome"):
            eo = goal_row["expected_outcome"]
            outcome = eo if isinstance(eo, str) else json.dumps(eo)
        elif steps_by_order.get(order, {}).get("expected_outputs"):
            outcome = "; ".join(steps_by_order[order]["expected_outputs"])

        node_verify = [
            VerifyLine(node_id=nid, verification_id=v.verification_id, state=v.state,
                       verification_type=v.verification_type, criterion=v.criterion, evidence=v.evidence)
            for v in verify_by_node.get(str(n["id"]), [])
        ]

        node_lines.append(NodeLine(
            node_id=nid, status=n["status"], name=n.get("goal") or "-",
            procedure_id=procedure_id, step_id=f"S{order}", binding=binding_kind, executor=executor,
            deps=deps, goal_id=goal_id,
            grounded_goal_summary=(goal_row["canonical_name"] if goal_row else None),
            inputs={}, access=access, expected_outcome=outcome, verify=node_verify,
            owner=owner, lease_until=it.get("file_intent_lease_expires_at"),
        ))

    run_line = RunLine(
        run_id=str(context["procedure_run_id"]), status=context["status"],
        objective=context.get("objective") or "-", procedure_id=procedure_id,
        procedure_version=context["procedure_version"],
    )
    run_md = render_run_md(run_line, node_lines, collab_lines)

    rows: list[RunIdxRow] = []
    lines = run_md.splitlines()
    node_starts = [(i, ln) for i, ln in enumerate(lines, start=1) if ln.startswith("NODE|")]
    for pos, (start, _) in enumerate(node_starts):
        nl = node_lines[pos]
        # end = just before the next NODE| line, or EOF for the last one
        end = (node_starts[pos + 1][0] - 2) if pos + 1 < len(node_starts) else len(lines)
        rows.append(RunIdxRow(
            node_id=nl.node_id, status=nl.status, owner=nl.owner or "-",
            deps=tuple(nl.deps), write_globs=tuple(a[1] for a in nl.access),
            file="run.md", start=start, end=end, summary=nl.name[:100],
        ))
    return run_md, rows


async def _fetch_file_intents(pool: asyncpg.Pool, run_id: str) -> dict[int, dict]:
    """Live (non-expired) file-intent declarations for the run's nodes
    (migration 56 columns on `execution_run_nodes`). Empty when none.

    BUG FIXED (synthetic-fallback sweep, 2026-09-11): this used to catch
    the entire `asyncpg.PostgresError` hierarchy and return `{}` for
    ANYTHING -- a real connection drop, a permissions error, a genuine
    query bug -- indistinguishable from the one case this is actually
    meant to tolerate (migration 56 not applied on this DB yet). A real
    DB failure must surface, not silently render as "no coordination
    declared" in the `.stealth/` projection. Narrowed to
    UndefinedColumnError (migration 56 adds columns, not a new table) and
    logged, mirroring `app/services/procedure_claim_refs.py`'s own
    UndefinedTableError/migration-66 pattern."""
    try:
        rows = await pool.fetch(
            "SELECT node_order, owner_agent_id, read_exact, read_globs, write_exact, "
            "       write_globs, symbols_expected_to_modify, file_intent_lease_expires_at "
            "FROM execution_run_nodes WHERE execution_run_id = $1::uuid "
            "AND (owner_agent_id IS NOT NULL "
            "     OR write_globs <> '[]'::jsonb OR write_exact <> '[]'::jsonb)",
            run_id,
        )
    except asyncpg.UndefinedColumnError:
        logger.warning("execution_run_nodes missing file-intent columns; migration 56 not applied?")
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
    from app.stealth.project_sync import ensure_stable_project_id

    # Read-or-mint once, persisted at meta.json's own "stable_project_id"
    # key (see app.stealth.project_sync's module docstring) -- a UUID identity
    # that survives this workspace folder being renamed or moved, unlike
    # this function's own run-scoped `workspace_id` field below (a
    # scope_entity_id/path-hash short string, unrelated). Read here, before
    # meta_json is rebuilt from scratch a few lines down, so a regeneration
    # never mints a fresh one.
    stable_project_id = ensure_stable_project_id(workspace_root)

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
    context.setdefault("scope_entity_id", run_row.get("scope_entity_id"))
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
    claims_md, claims_rows = await _build_claims_page(pool, global_claims)
    procedures_md, procedures_rows = await _build_procedures_page(
        pool, context, procedure,
        extra_ids=tuple(b.obj_id for b in faulted.get("procedure", ())))
    run_md, run_rows = await _build_run_page(pool, context, procedure, intents)
    exploration_md, exploration_rows = render_exploration_page(workspace_root)

    claim_groups, procedure_groups, goal_groups, run_states, goal_rows = await _gather_index_groups(
        pool, context=context, claims_rows=claims_rows, procedures_rows=procedures_rows,
    )
    goals_md, goals_rows = await _build_goals_page(goal_rows)

    claims_idx = render_idx(claims_rows, header="claims.idx  id|version|scope|status|tags|file|start|end|summary")
    procedures_idx = render_idx(procedures_rows, header="procedures.idx  id|version|scope|status|tags|file|start|end|summary")
    goals_idx = render_idx(goals_rows, header="goals.idx  id|version|scope|status|tags|file|start|end|summary")
    run_idx = render_idx(run_rows, header="run.idx  node|status|owner|deps|globs|file|start|end|summary")
    exploration_idx = render_idx(exploration_rows, header="exploration.idx  id|version|scope|status|tags|file|start|end|summary")

    for name, content in (("claims.idx", claims_idx), ("procedures.idx", procedures_idx),
                          ("goals.idx", goals_idx),
                          ("run.idx", run_idx), ("exploration.idx", exploration_idx)):
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
        "context.md", "run.json", "meta.json", "index.md",
        *CONTENT_PAGE_FILES, "events.jsonl",
        "index/root.idx", "index/claims.idx", "index/procedures.idx",
        "index/goals.idx", "index/run.idx",
    ]
    if has_expl:
        file_list += [*OPTIONAL_CONTENT_PAGE_FILES, "index/exploration.idx"]
    meta_json.update({
        "schema": "stealth-projection/2",
        "stable_project_id": stable_project_id,
        "workspace_id": str(run_row.get("scope_entity_id") or _short(hashlib.sha1(
            workspace_root.encode("utf-8")).hexdigest(), 12)),
        "change_cursor": change_cursor,
        "revisions": {
            "claims": len(claims_rows),
            "procedures": len(procedures_rows),
            "goals": len(goals_rows),
            "run": len(run_rows),
            "exploration": len(exploration_rows),
        },
        "counts": {
            "claims": len(claims_rows),
            "procedures": len(procedures_rows),
            "goals": len(goals_rows),
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

        from app.stealth.pipe_format import render_index_md

        index_md = render_index_md(
            repo=os.path.basename(os.path.abspath(workspace_root)) or workspace_root,
            revision=seq, active_run=str(run_row["id"]),
            claim_groups=claim_groups, procedure_groups=procedure_groups,
            goal_groups=goal_groups, run_states=run_states,
        )

        plan: list[tuple[str, str]] = [
            (os.path.join(stealth_dir, "context.md"), context_md),
            (os.path.join(stealth_dir, "run.json"), json.dumps(run_json, indent=2, default=str)),
            (os.path.join(stealth_dir, "index.md"), index_md),
            (os.path.join(stealth_dir, "claims.md"), claims_md),
            (os.path.join(stealth_dir, "procedures.md"), procedures_md),
            (os.path.join(stealth_dir, "goals.md"), goals_md),
            (os.path.join(stealth_dir, "run.md"), run_md),
            (os.path.join(index_dir, "root.idx"), root_idx),
            (os.path.join(index_dir, "claims.idx"), claims_idx),
            (os.path.join(index_dir, "procedures.idx"), procedures_idx),
            (os.path.join(index_dir, "goals.idx"), goals_idx),
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
        "index_md": index_md,
        "claims_md": claims_md,
        "procedures_md": procedures_md,
        "goals_md": goals_md,
        "run_md": run_md,
        "exploration_md": exploration_md,
        "root_idx": root_idx,
        "claims_idx": claims_idx,
        "procedures_idx": procedures_idx,
        "goals_idx": goals_idx,
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
