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
    "# NODE|<node_id>|<status>|<name>|step=<procedure_id>:<step_id>|impl=<implementation_id>|executor=<executor>|deps=<node_ids_csv>\n"
    "# VERIFY|<node_id>|<verification_id>|<state>|<verification_type>|<criterion>|source=<source_ref>|evidence=<evidence_ref_or_none>\n\n"
)
_INDEX_HEADER = "# index.md -- GENERATED, not canonical. Do not hand-edit. This is a routing index, not prose.\n\n"


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
    goal_type: Optional[str] = None
    inputs: dict[str, str] = field(default_factory=dict)
    context_claims: list[str] = field(default_factory=list)  # already "claim_id@version" strings
    access: list[tuple[str, str]] = field(default_factory=list)  # (access_type, access_spec)
    expected_outcome: Optional[str] = None
    verify: list[VerifyLine] = field(default_factory=list)
    owner: Optional[str] = None
    lease_until: Optional[str] = None


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
            + _SEP + _kv_field("step", f"{n.procedure_id}:{n.step_id}")
            + _SEP + _kv_field("impl", n.implementation_id or "-")
            + _SEP + _kv_field("executor", n.executor)
            + _SEP + _kv_field("deps", _csv(n.deps)),
        ]
        if n.goal_type:
            lines.append(_row("GOAL", n.node_id, n.goal_type))
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
) -> str:
    """Directive Sec 24. The tiny router -- one-line-per-group records,
    no duplicated object bodies, bounded size. `active_run=None` renders
    the literal `none` (never a fabricated run id)."""
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
