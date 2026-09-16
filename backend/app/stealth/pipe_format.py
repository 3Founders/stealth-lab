"""
Pipe-delimited `.stealth/` projection format (meta-harness directive
Sec 23-31, 2026-09-15) -- the exact grammar for `index.md`, `claims.md`,
`procedures.md`, `run.md`.

Deliberately separate from `app.stealth.format`'s existing `.idx`/
`MdBlock`/`render_md_page` machinery, not a replacement of it: this
directive's whole design point is that each line here is already
self-contained and independently `rg`-able (`CLAIM|C-022|...` is a
complete record on one line), so there is no line-range index to
maintain for these four files the way `claims.idx`/`procedures.idx`/
`run.idx` exist for the OLDER `## HEADING` pages (`implementations.md`,
`exploration.md`) that still use that mechanism, unchanged, alongside
this one. Reuses `app.stealth.format._clean` for the same pipe/newline
safety guarantee the existing `.idx` rows already have -- one sanitizer,
not two.

Every render function here is pure (no pool, no I/O) and takes already-
resolved dataclasses -- the DB queries that produce them live in
`generator.py`, matching this repo's established split between "gather"
and "render" (see `format.py`'s own module docstring).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.stealth.format import _clean

_SEP = "|"

_CLAIMS_HEADER = (
    "# claims.md -- GENERATED, not canonical. Do not hand-edit.\n"
    "# CLAIM|<claim_id>|<status>|<topic>|<scope>|<statement>|source=<source_ref>|version=<version>\n\n"
)
_PROCEDURES_HEADER = (
    "# procedures.md -- GENERATED, not canonical. Do not hand-edit.\n"
    "# PROCEDURE|<procedure_id>|<status>|<topic>|<scope>|<name>|version=<version>\n"
    "# STEP|<procedure_id>|<step_id>|<order>|<goal_type>|<description>|deps=<step_ids_csv>\n"
    "# VERIFY_REQ|<procedure_id>|<step_id>|<verification_goal>|<description>\n\n"
)
_RUN_HEADER = (
    "# run.md -- GENERATED, not canonical. Do not hand-edit.\n"
    "# NODE|<node_id>|<status>|<name>|goal=<goal_id>|step=<procedure_id>:<step_id>|impl=<implementation_id>|executor=<executor>|deps=<node_ids_csv>\n"
    "# GOAL|<node_id>|<goal_id>|<grounded_goal_summary>\n"
    "# VERIFY|<node_id>|<verification_id>|<state>|<verification_type>|<criterion>|source=<source_ref>|evidence=<evidence_ref_or_none>\n"
    "# COST_ESTIMATE not emitted yet -- no cost model exists in this codebase (honest, not silently skipped)\n\n"
)
_INDEX_HEADER = "# index.md -- GENERATED, not canonical. Do not hand-edit. This is a routing index, not prose.\n\n"
_GOALS_HEADER = (
    "# goals.md -- GENERATED, not canonical. Do not hand-edit.\n"
    "# GOAL|<goal_id>|<status>|<scope>|<name>|version=<version>\n"
    "# GOAL_DETAIL|<goal_id>|outcome=<expected_outcome>|verification=<verification_summary>\n"
    "# ALIASES|<goal_id>|<alias_csv>\n\n"
)


def _row(*fields: object) -> str:
    """One pipe-delimited record -- every field pipe/newline-safe via the
    SAME `_clean` the existing `.idx` rows use (one sanitizer, not two;
    see module docstring)."""
    return _SEP.join(_clean(f) for f in fields)


def _kv_field(key: str, value: object) -> str:
    """A `key=value` PIPE FIELD (not a `key: value` line) -- the
    directive's own syntax for e.g. `source=<source_ref>`,
    `version=<version>`, `deps=<ids_csv>`. The value is cleaned; the key
    is a literal ASCII identifier this module controls, never cleaned
    (it can never contain `|`/newline by construction)."""
    return f"{key}={_clean(value)}"


def _csv(ids: Optional[list[str]]) -> str:
    return ",".join(ids) if ids else "-"


# ===========================================================================
# claims.md
# ===========================================================================


@dataclass
class ClaimLine:
    claim_id: str
    status: str
    topic: str
    scope: str
    statement: str
    source: str
    version: int = 1


def render_claims_md(claims: list[ClaimLine]) -> str:
    """Directive Sec 25. Empty is a real, honest state -- rendered
    explicitly, never a fabricated placeholder row."""
    if not claims:
        return _CLAIMS_HEADER + "(no claims)\n"
    lines = [
        _row("CLAIM", c.claim_id, c.status, c.topic, c.scope, c.statement)
        + _SEP + _kv_field("source", c.source) + _SEP + _kv_field("version", c.version)
        for c in claims
    ]
    return _CLAIMS_HEADER + "\n".join(lines) + "\n"


# ===========================================================================
# procedures.md
# ===========================================================================


@dataclass
class StepLine:
    step_id: str
    order: int
    goal_type: str
    description: str
    deps: list[str] = field(default_factory=list)


@dataclass
class VerifyReqLine:
    step_id: str
    verification_goal: str
    description: str


@dataclass
class ProcedureLine:
    procedure_id: str
    status: str
    topic: str
    scope: str
    name: str
    version: int
    steps: list[StepLine] = field(default_factory=list)
    verify_reqs: list[VerifyReqLine] = field(default_factory=list)


def render_procedures_md(procedures: list[ProcedureLine]) -> str:
    """Directive Sec 26. Run-specific bindings (implementation_id,
    executor, concrete inputs) never appear here -- those belong to
    run.md's NODE lines; this file stays the abstract, run-independent
    Procedure/Step definition, unchanged across every run that uses it."""
    if not procedures:
        return _PROCEDURES_HEADER + "(no procedures)\n"
    blocks: list[str] = []
    for p in procedures:
        lines = [_row("PROCEDURE", p.procedure_id, p.status, p.topic, p.scope, p.name) + _SEP + _kv_field("version", p.version)]
        for s in p.steps:
            lines.append(
                _row("STEP", p.procedure_id, s.step_id, s.order, s.goal_type, s.description)
                + _SEP + _kv_field("deps", _csv(s.deps))
            )
        for v in p.verify_reqs:
            lines.append(_row("VERIFY_REQ", p.procedure_id, v.step_id, v.verification_goal, v.description))
        blocks.append("\n".join(lines))
    return _PROCEDURES_HEADER + "\n\n".join(blocks) + "\n"


# ===========================================================================
# run.md
# ===========================================================================


@dataclass
class VerifyLine:
    node_id: str
    verification_id: str
    state: str
    verification_type: str
    criterion: str
    source: str = "-"
    evidence: Optional[str] = None


@dataclass
class NodeLine:
    node_id: str
    status: str
    name: str
    procedure_id: str
    step_id: str
    implementation_id: Optional[str]
    executor: str
    deps: list[str] = field(default_factory=list)
    # execu.md Sec 25 (meta-harness directive, 2026-09-15 revision):
    # Goal identity is now first-class on the NODE line itself
    # (goal=<goal_id>), not a separate goal_type field. `goal_id` is
    # `None` when no real canonical Goal (backend/db/83_goals.sql) has
    # been resolved for this node's step -- rendered as the literal `-`,
    # never fabricated. `grounded_goal_summary` feeds the companion
    # GOAL|<node_id>|<goal_id>|<summary> line (only emitted when goal_id
    # is real -- a summary with no real goal_id to anchor it is not
    # rendered, per the same no-fabrication rule).
    goal_id: Optional[str] = None
    grounded_goal_summary: Optional[str] = None
    inputs: dict[str, str] = field(default_factory=dict)
    context_claims: list[str] = field(default_factory=list)  # already "claim_id@version" strings
    access: list[tuple[str, str]] = field(default_factory=list)  # (access_type, access_spec)
    expected_outcome: Optional[str] = None
    verify: list[VerifyLine] = field(default_factory=list)
    owner: Optional[str] = None
    lease_until: Optional[str] = None
    # Sec 25's COST_ESTIMATE line is deliberately NOT emitted anywhere in
    # this module -- no cost model exists yet in this codebase (confirmed
    # honestly, not silently skipped) -- fabricating a number here would
    # violate the whole ABI's own "never fabricate" contract. Add a
    # cost_estimate field + COST_ESTIMATE render line together with the
    # real cost model, not ahead of it.


@dataclass
class RunLine:
    run_id: str
    status: str
    objective: str
    procedure_id: str
    procedure_version: int


def render_run_md(run: RunLine, nodes: list[NodeLine]) -> str:
    """Directive Sec 27. Every node-specific line repeats `node_id` so
    `rg N-003 run.md` surfaces the node's complete operational state in
    one grep -- proven by test_run_md_node_lines_all_repeat_node_id."""
    header = _RUN_HEADER + _row("RUN", run.run_id, run.status, run.objective) + _SEP + \
        _kv_field("procedure", f"{run.procedure_id}@{run.procedure_version}") + "\n\n"
    if not nodes:
        return header + "(no nodes)\n"
    blocks: list[str] = []
    for n in nodes:
        lines = [
            _row("NODE", n.node_id, n.status, n.name)
            + _SEP + _kv_field("goal", n.goal_id or "-")
            + _SEP + _kv_field("step", f"{n.procedure_id}:{n.step_id}")
            + _SEP + _kv_field("impl", n.implementation_id or "-")
            + _SEP + _kv_field("executor", n.executor)
            + _SEP + _kv_field("deps", _csv(n.deps)),
        ]
        if n.goal_id:
            lines.append(_row("GOAL", n.node_id, n.goal_id, n.grounded_goal_summary or "-"))
        for key, value in n.inputs.items():
            lines.append(_row("INPUT", n.node_id) + _SEP + _kv_field(key, value))
        if n.context_claims:
            lines.append(_row("CONTEXT", n.node_id) + _SEP + _kv_field("claims", ",".join(n.context_claims)))
        for access_type, access_spec in n.access:
            lines.append(_row("ACCESS", n.node_id, access_type, access_spec))
        if n.expected_outcome:
            lines.append(_row("OUTCOME", n.node_id, n.expected_outcome))
        for v in n.verify:
            lines.append(
                _row("VERIFY", v.node_id, v.verification_id, v.state, v.verification_type, v.criterion)
                + _SEP + _kv_field("source", v.source) + _SEP + _kv_field("evidence", v.evidence or "none")
            )
        if n.owner:
            lines.append(_row("OWNER", n.node_id, n.owner) + _SEP + _kv_field("lease_until", n.lease_until or "none"))
        blocks.append("\n".join(lines))
    return header + "\n\n".join(blocks) + "\n"


# ===========================================================================
# goal_run.md -- Prompt 2 Sec 12/13's Goal-DAG execution trace. Deliberately
# a SEPARATE page/grammar from run.md above, not a shoehorned extension of
# it: `RunLine`/`NodeLine` are shaped around a real `execution_runs` row
# (Procedure-anchored -- run_id/procedure_id/procedure_version/step_id all
# required), which a pure Goal-DAG execution (`goal_execution.py`, no
# Postgres run row at all -- see that module's own docstring) never has.
# Forcing Goal-execution data into those fields would mean fabricating a
# procedure_id/step_id that doesn't exist. This page's own real source is
# `GoalExecutionResult` (already fully computed by `execute_goal_tree`),
# never re-derived or guessed.
# ===========================================================================

_GOAL_RUN_HEADER = (
    "# goal_run.md -- GENERATED, not canonical. Do not hand-edit.\n"
    "# GOAL_RUN|<execution_id>|<outcome>\n"
    "# GOAL_NODE|<goal_id>|<kind>|<status>|impl=<implementation_id_or_->|proc=<procedure_id_or_->|"
    "verify=<verification_state_or_->|human_intervention=<bool>|resumed=<bool>\n"
    "# ARTIFACT|<goal_id>|<filename>|sha256=<sha256>|size=<size_bytes>\n\n"
)


@dataclass
class GoalRunLine:
    goal_id: str
    kind: str  # "implementation" | "procedure"
    status: str
    implementation_id: Optional[str] = None
    procedure_id: Optional[str] = None
    verification_state: Optional[str] = None
    human_intervention_needed: bool = False
    resumed_from_journal: bool = False
    # Real output files this node's winning attempt produced (Sec 5's
    # `.stealth/artifacts/` -- `app.stealth.artifacts.write_execution_
    # artifacts`'s own real manifest), each `{filename, sha256, size_bytes}`
    # -- empty for the common case of no file output at all.
    artifacts: list[dict] = field(default_factory=list)


def render_goal_run_md(execution_id: str, outcome: str, nodes: list[GoalRunLine]) -> str:
    """Pure render, no pool/IO (same discipline every function in this
    module holds) -- the caller (`goal_execution.py`) already has every
    real field from its own `GoalExecutionResult`, this only formats it.
    `execution_id='-'` (never a fabricated id) when a caller renders a
    non-durable (no `workspace_root`) execution for inspection only."""
    header = _GOAL_RUN_HEADER + _row("GOAL_RUN", execution_id, outcome) + "\n\n"
    if not nodes:
        return header + "(no nodes)\n"
    lines: list[str] = []
    for n in nodes:
        lines.append(
            _row("GOAL_NODE", n.goal_id, n.kind, n.status)
            + _SEP + _kv_field("impl", n.implementation_id or "-")
            + _SEP + _kv_field("proc", n.procedure_id or "-")
            + _SEP + _kv_field("verify", n.verification_state or "-")
            + _SEP + _kv_field("human_intervention", n.human_intervention_needed)
            + _SEP + _kv_field("resumed", n.resumed_from_journal)
        )
        for a in n.artifacts:
            lines.append(
                _row("ARTIFACT", n.goal_id, a.get("filename", "-"))
                + _SEP + _kv_field("sha256", a.get("sha256", "-"))
                + _SEP + _kv_field("size", a.get("size_bytes", "-"))
            )
    return header + "\n".join(lines) + "\n"


# ===========================================================================
# index.md
# ===========================================================================


@dataclass
class GroupLine:
    topic: str
    ids: list[str]


@dataclass
class RunStateLine:
    state: str  # READY | RUNNING | BLOCKED | DONE
    node_ids: list[str]


def render_index_md(
    *, repo: str, revision: int, active_run: Optional[str],
    claim_groups: list[GroupLine], procedure_groups: list[GroupLine],
    implementation_groups: list[GroupLine], run_states: list[RunStateLine],
    goal_groups: list[GroupLine] = (),  # type: ignore[assignment]
) -> str:
    """Directive Sec 24. The tiny router -- one-line-per-group records,
    no duplicated object bodies, bounded size. `active_run=None` renders
    the literal `none` (never a fabricated run id).

    `goal_groups` defaults to `()` -- every pre-existing caller (this
    module's own earlier callers, before Goal had a canonical table)
    keeps working unchanged. Every *_GROUP line uses the SAME
    space-separated id list execu.md's own CLAIM_GROUP/PROCEDURE_GROUP/
    IMPLEMENTATION_GROUP examples already establish, deliberately not the
    comma-separated form that directive's own GOAL_GROUP example shows in
    isolation -- one separator convention across every group line in this
    file is worth more than matching one inconsistent example verbatim.
    """
    lines = [
        _row("REPO", repo),
        _row("REVISION", revision),
        _row("ACTIVE_RUN", active_run or "none"),
        "",
    ]
    for g in claim_groups:
        lines.append(_row("CLAIM_GROUP", g.topic, " ".join(g.ids)))
    if claim_groups:
        lines.append("")
    for g in goal_groups:
        lines.append(_row("GOAL_GROUP", g.topic, " ".join(g.ids)))
    if goal_groups:
        lines.append("")
    for g in procedure_groups:
        lines.append(_row("PROCEDURE_GROUP", g.topic, " ".join(g.ids)))
    if procedure_groups:
        lines.append("")
    for g in implementation_groups:
        lines.append(_row("IMPLEMENTATION_GROUP", g.topic, " ".join(g.ids)))
    if implementation_groups:
        lines.append("")
    for rs in run_states:
        lines.append(_row("RUN_STATE", rs.state, " ".join(rs.node_ids) if rs.node_ids else "-"))
    return _INDEX_HEADER + "\n".join(lines) + "\n"


# ===========================================================================
# goals.md
# ===========================================================================


@dataclass
class GoalLine:
    goal_id: str
    status: str
    scope: str
    name: str
    version: int
    expected_outcome: Optional[str] = None
    verification_summary: Optional[str] = None
    aliases: list[str] = field(default_factory=list)


def render_goals_md(goals: list[GoalLine]) -> str:
    """execu.md Sec 23: `GOAL|<goal_id>|<status>|<scope>|<name>|
    version=<version>`, `GOAL_DETAIL|<goal_id>|outcome=<expected_outcome>|
    verification=<verification_summary>`, `ALIASES|<goal_id>|<alias_csv>`.
    Empty is a real, honest state (a run with no resolved Goals), never a
    fabricated placeholder row."""
    if not goals:
        return _GOALS_HEADER + "(no goals)\n"
    blocks: list[str] = []
    for g in goals:
        lines = [_row("GOAL", g.goal_id, g.status, g.scope, g.name) + _SEP + _kv_field("version", g.version)]
        if g.expected_outcome or g.verification_summary:
            lines.append(
                _row("GOAL_DETAIL", g.goal_id)
                + _SEP + _kv_field("outcome", g.expected_outcome or "-")
                + _SEP + _kv_field("verification", g.verification_summary or "-")
            )
        if g.aliases:
            lines.append(_row("ALIASES", g.goal_id, ",".join(g.aliases)))
        blocks.append("\n".join(lines))
    return _GOALS_HEADER + "\n\n".join(blocks) + "\n"
